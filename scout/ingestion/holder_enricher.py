"""Holder data enrichment via Helius (Solana) and Moralis (EVM)."""

import asyncio
from collections import Counter

import structlog

import aiohttp

from scout.config import Settings
from scout.models import CandidateToken

logger = structlog.get_logger()

# Chain mappings for Moralis
MORALIS_CHAIN_MAP = {
    "ethereum": "eth",
    "base": "base",
    "polygon": "polygon",
}

HELIUS_RPC = "https://mainnet.helius-rpc.com"
HELIUS_API = "https://api.helius.xyz/v0"

# Rate-limit guard: max 1 concurrent Helius call with delay between requests
_helius_semaphore = asyncio.Semaphore(1)
_HELIUS_DELAY = 0.5  # seconds between requests

# Retry config for rate-limited requests
_MAX_RETRIES = 4
_RETRY_BACKOFF = [2.0, 4.0, 8.0, 12.0]


async def _helius_request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    **kwargs,
) -> dict | list | None:
    """Make a Helius request with rate-limit semaphore and retry on 429."""
    async with _helius_semaphore:
        await asyncio.sleep(_HELIUS_DELAY)
        for attempt in range(_MAX_RETRIES):
            try:
                if method == "post":
                    async with session.post(url, **kwargs) as resp:
                        if resp.status == 429:
                            wait = _RETRY_BACKOFF[attempt] if attempt < len(_RETRY_BACKOFF) else 4.0
                            logger.warning("Helius rate limited, retrying", attempt=attempt + 1, wait=wait)
                            await asyncio.sleep(wait)
                            continue
                        resp.raise_for_status()
                        data = await resp.json()
                        if isinstance(data, dict) and "error" in data:
                            logger.warning("Helius RPC error", error=data["error"])
                            return None
                        return data
                else:
                    async with session.get(url, **kwargs) as resp:
                        if resp.status == 429:
                            wait = _RETRY_BACKOFF[attempt] if attempt < len(_RETRY_BACKOFF) else 4.0
                            logger.warning("Helius rate limited, retrying", attempt=attempt + 1, wait=wait)
                            await asyncio.sleep(wait)
                            continue
                        if resp.status != 200:
                            return None
                        return await resp.json()
            except aiohttp.ClientError as e:
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_BACKOFF[attempt])
                    continue
                raise
    logger.warning("Helius request failed after retries", url=url)
    return None


async def enrich_holders(
    token: CandidateToken,
    session: aiohttp.ClientSession,
    settings: Settings,
) -> CandidateToken:
    """Enrich a token with holder count and on-chain analysis data.

    - Solana -> Helius DAS API + transaction analysis
    - EVM chains -> Moralis ERC20 owners
    - Missing API key -> return unenriched (graceful degradation)
    - API failure -> log warning, return unenriched
    """
    if token.chain == "solana":
        if not settings.HELIUS_API_KEY:
            return token
        return await _enrich_solana(token, session, settings)
    elif token.chain in MORALIS_CHAIN_MAP:
        if not settings.MORALIS_API_KEY:
            return token
        return await _enrich_evm(token, session, settings)
    return token


async def _enrich_solana(
    token: CandidateToken,
    session: aiohttp.ClientSession,
    settings: Settings,
) -> CandidateToken:
    """Fetch holder count and on-chain signals from Helius."""
    updates: dict = {}

    # 1. Holder count via DAS API
    holder_count = await _helius_holder_count(token.contract_address, session, settings)
    if holder_count is not None:
        updates["holder_count"] = holder_count

    # 2. Transaction analysis via parsed transactions API (BL-021, BL-022, BL-024)
    txn_data = await _helius_txn_analysis(token.contract_address, session, settings)
    updates.update(txn_data)

    # 3. Deployer supply concentration (BL-023)
    deployer_pct = await _helius_deployer_concentration(token.contract_address, session, settings)
    if deployer_pct is not None:
        updates["deployer_supply_pct"] = deployer_pct

    if updates:
        return token.model_copy(update=updates)
    return token


async def _helius_holder_count(
    mint: str, session: aiohttp.ClientSession, settings: Settings,
) -> int | None:
    """Fetch holder count from Helius DAS API (getTokenAccounts)."""
    url = f"{HELIUS_RPC}/?api-key={settings.HELIUS_API_KEY}"
    payload = {
        "jsonrpc": "2.0",
        "id": "holder-enrichment",
        "method": "getTokenAccounts",
        "params": {"mint": mint, "limit": 1000},
    }
    try:
        data = await _helius_request(session, "post", url, json=payload)
        if data is None:
            return None
        return data.get("result", {}).get("total", 0)
    except Exception:
        logger.warning("Helius holder lookup failed", contract_address=mint, exc_info=True)
        return None


async def _helius_txn_analysis(
    mint: str, session: aiohttp.ClientSession, settings: Settings,
) -> dict:
    """Analyze recent transactions for unique buyers, concentration, and size distribution.

    Returns dict with: unique_buyers_1h, top3_wallet_concentration, small_txn_ratio
    """
    url = f"{HELIUS_API}/addresses/{mint}/transactions"
    params = {"api-key": settings.HELIUS_API_KEY, "limit": 100, "type": "SWAP"}
    result: dict = {}

    try:
        txns = await _helius_request(session, "get", url, params=params)
        if not txns or not isinstance(txns, list):
            return result
    except Exception:
        logger.warning("Helius txn analysis failed", contract_address=mint, exc_info=True)
        return result

    # Parse transactions for buyer wallets and amounts
    buyer_wallets: list[str] = []
    wallet_volume: Counter = Counter()
    txn_amounts: list[float] = []

    for txn in txns:
        # Helius parsed transactions include tokenTransfers
        transfers = txn.get("tokenTransfers", [])
        fee_payer = txn.get("feePayer", "")
        for transfer in transfers:
            if transfer.get("mint") == mint:
                amount = float(transfer.get("tokenAmount", 0))
                to_addr = transfer.get("toUserAccount", "")
                from_addr = transfer.get("fromUserAccount", "")

                # A buy is when the token moves TO a wallet (not from a pool/program)
                if to_addr and to_addr != mint:
                    buyer_wallets.append(to_addr)
                    wallet_volume[to_addr] += amount

                if amount > 0:
                    txn_amounts.append(amount)

    # BL-021: Unique buyer count
    unique_buyers = len(set(buyer_wallets))
    if unique_buyers > 0:
        result["unique_buyers_1h"] = unique_buyers

    # BL-022: Top-3 wallet concentration
    if wallet_volume:
        total_vol = sum(wallet_volume.values())
        if total_vol > 0:
            top3 = wallet_volume.most_common(3)
            top3_vol = sum(v for _, v in top3)
            result["top3_wallet_concentration"] = top3_vol / total_vol

    # BL-024: Small transaction ratio
    # Organic = many small txns ($50-$500 equivalent in tokens)
    # We use relative sizing: "small" = below median * 2
    if len(txn_amounts) >= 5:
        sorted_amounts = sorted(txn_amounts)
        median = sorted_amounts[len(sorted_amounts) // 2]
        if median > 0:
            small_count = sum(1 for a in txn_amounts if a <= median * 2)
            result["small_txn_ratio"] = small_count / len(txn_amounts)

    return result


async def _helius_deployer_concentration(
    mint: str, session: aiohttp.ClientSession, settings: Settings,
) -> float | None:
    """Check deployer/creator wallet token supply concentration (BL-023).

    Uses Helius DAS getAsset to find the creator, then checks their balance
    as a percentage of total supply. For pump.fun tokens where authorities
    is empty, falls back to mint_extensions metadata update_authority.
    """
    url = f"{HELIUS_RPC}/?api-key={settings.HELIUS_API_KEY}"

    # Get token metadata to find creator/authority
    payload = {
        "jsonrpc": "2.0",
        "id": "deployer-check",
        "method": "getAsset",
        "params": {"id": mint},
    }
    try:
        data = await _helius_request(session, "post", url, json=payload)
        if data is None:
            return None

        result = data.get("result", {})
        authorities = result.get("authorities", [])

        # Try authorities first
        deployer = None
        if authorities:
            deployer = authorities[0].get("address")

        # Fallback for pump.fun tokens: check creators field
        if not deployer:
            creators = result.get("creators", [])
            if creators:
                deployer = creators[0].get("address")

        # Fallback: check content metadata update_authority
        if not deployer:
            ownership = result.get("ownership", {})
            deployer = ownership.get("owner")

        if not deployer:
            return None

        supply_info = result.get("token_info", {})
        total_supply = float(supply_info.get("supply", 0))
        decimals = int(supply_info.get("decimals", 0))
        if total_supply <= 0:
            return None

        # Get deployer's token balance via getTokenAccounts
        balance_payload = {
            "jsonrpc": "2.0",
            "id": "deployer-balance",
            "method": "getTokenAccounts",
            "params": {"owner": deployer, "mint": mint, "limit": 1},
        }
        bal_data = await _helius_request(session, "post", url, json=balance_payload)
        if bal_data is None:
            return None

        accounts = bal_data.get("result", {}).get("token_accounts", [])
        if not accounts:
            return 0.0

        deployer_amount = float(accounts[0].get("amount", 0))
        return deployer_amount / total_supply

    except Exception:
        logger.warning("Helius deployer check failed", contract_address=mint, exc_info=True)
        return None


async def _enrich_evm(
    token: CandidateToken,
    session: aiohttp.ClientSession,
    settings: Settings,
) -> CandidateToken:
    """Fetch holder count from Moralis ERC20 owners endpoint."""
    chain = MORALIS_CHAIN_MAP[token.chain]
    url = (
        f"https://deep-index.moralis.io/api/v2.2/erc20/"
        f"{token.contract_address}/owners?chain={chain}"
    )
    headers = {"X-API-Key": settings.MORALIS_API_KEY}
    try:
        async with session.get(url, headers=headers) as resp:
            resp.raise_for_status()
            data = await resp.json()
            holders = data.get("result", [])
            return token.model_copy(update={"holder_count": len(holders)})
    except Exception:
        logger.warning(
            "Moralis holder lookup failed",
            contract_address=token.contract_address, exc_info=True,
        )
        return token
