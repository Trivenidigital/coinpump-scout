"""Holder data enrichment via Helius (Solana) and Moralis (EVM)."""

import asyncio
from collections import Counter

import structlog

import aiohttp

from scout.config import Settings
from scout.ingestion._helius import HELIUS_API, HELIUS_RPC, helius_request, helius_rpc_url
from scout.models import CandidateToken

logger = structlog.get_logger()

# Chain mappings for Moralis
MORALIS_CHAIN_MAP = {
    "ethereum": "eth",
    "base": "base",
    "polygon": "polygon",
}

RUGCHECK_API = "https://api.rugcheck.xyz/v1/tokens"

# Rate limit Rugcheck concurrent calls to respect API constraints (lazily initialized)
_rugcheck_semaphore: asyncio.Semaphore | None = None
_rugcheck_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _get_rugcheck_semaphore() -> asyncio.Semaphore:
    """Lazily create semaphore, resetting if the event loop changed."""
    global _rugcheck_semaphore, _rugcheck_semaphore_loop
    loop = asyncio.get_running_loop()
    if _rugcheck_semaphore is None or _rugcheck_semaphore_loop is not loop:
        _rugcheck_semaphore = asyncio.Semaphore(3)
        _rugcheck_semaphore_loop = loop
    return _rugcheck_semaphore


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
        # Rugcheck first (free, fast, no API key)
        token = await _enrich_rugcheck(token, session)
        # Call Helius if Rugcheck only returned capped data (free tier caps at ~20)
        if settings.HELIUS_API_KEY and token.holder_count <= 20:
            token = await _enrich_solana_helius(token, session, settings)
        return token
    elif token.chain in MORALIS_CHAIN_MAP:
        if not settings.MORALIS_API_KEY:
            return token
        return await _enrich_evm(token, session, settings)
    return token


async def _enrich_rugcheck(
    token: CandidateToken,
    session: aiohttp.ClientSession,
) -> CandidateToken:
    """Fetch holder data from Rugcheck API (free, no API key needed).

    Returns holder count, top holder concentration, deployer %, and LP lock status.
    """
    url = f"{RUGCHECK_API}/{token.contract_address}/report"
    updates: dict = {}

    try:
        async with _get_rugcheck_semaphore():
            async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.debug("Rugcheck returned non-200", status=resp.status, mint=token.contract_address)
                    return token
                data = await resp.json()

            # Holder data
            top_holders = data.get("topHolders", [])
            if top_holders:
                # Count holders (Rugcheck returns top 20, but the actual count is higher)
                # Use len as minimum, real count is typically much higher
                holder_count = len(top_holders)
                # If there's a "totalHolders" or similar field, prefer that
                if data.get("holderCount"):
                    holder_count = int(data["holderCount"])

                # Only update if we got more holders than currently known
                if holder_count > token.holder_count:
                    updates["holder_count"] = holder_count

                # Top 3 concentration
                if len(top_holders) >= 3:
                    total_pct = sum(h.get("pct", 0) for h in top_holders)
                    if total_pct > 0:
                        top3_pct = sum(h.get("pct", 0) for h in top_holders[:3])
                        updates["top3_wallet_concentration"] = top3_pct / 100.0

                # Deployer / insider concentration
                insider_pct = sum(h.get("pct", 0) for h in top_holders if h.get("isInsider"))
                if insider_pct > 0:
                    updates["deployer_supply_pct"] = insider_pct / 100.0

            # LP lock status from markets
            markets = data.get("markets", [])
            for market in markets:
                lp = market.get("lp", {})
                locked_pct = lp.get("lpLockedPct", 0)
                if locked_pct > 50:
                    updates["liquidity_locked"] = True
                    break

            # Risk score
            risk_score = data.get("score", 0)
            risks = [r.get("name", "") for r in data.get("risks", [])]

            if risks:
                logger.debug(
                    "Rugcheck report",
                    mint=token.contract_address,
                    risk_score=risk_score,
                    risks=risks,
                    holders=len(top_holders),
                )

    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.debug("Rugcheck request failed", mint=token.contract_address, error=str(exc))
    except Exception:
        logger.warning("Rugcheck enrichment failed", mint=token.contract_address, exc_info=True)

    if updates:
        return token.model_copy(update=updates)
    return token


async def _enrich_solana_helius(
    token: CandidateToken,
    session: aiohttp.ClientSession,
    settings: Settings,
) -> CandidateToken:
    """Supplement with Helius data (transaction analysis, holder count if missing)."""
    updates: dict = {}

    # Fetch holder count from Helius if Rugcheck didn't provide it or returned capped data
    if token.holder_count <= 20:
        holder_count = await _helius_holder_count(token.contract_address, session, settings)
        if holder_count is not None and holder_count > token.holder_count:
            updates["holder_count"] = holder_count

    # Transaction analysis (unique buyers, small txn ratio)
    txn_data = await _helius_txn_analysis(token.contract_address, session, settings)
    # Only update fields that Rugcheck didn't already set
    for key, value in txn_data.items():
        if key == "top3_wallet_concentration" and token.top3_wallet_concentration > 0:
            continue  # Rugcheck already set this
        updates[key] = value

    # Deployer concentration (only if not already set by Rugcheck)
    if token.deployer_supply_pct == 0:
        deployer_pct = await _helius_deployer_concentration(token.contract_address, session, settings)
        if deployer_pct is not None:
            updates["deployer_supply_pct"] = deployer_pct

    if updates:
        return token.model_copy(update=updates)
    return token


async def _helius_holder_count(
    mint: str, session: aiohttp.ClientSession, settings: Settings,
) -> int | None:
    """Fetch holder count from Helius DAS API (getTokenAccounts).

    Paginates with limit=1000 per page. The 'total' field in the response
    just mirrors the page limit, so we count actual accounts returned.
    Stops early once we have enough to confirm the token is active (1000+).
    """
    url = helius_rpc_url(settings.HELIUS_API_KEY)
    holder_count = 0
    cursor = None
    max_pages = 3  # Up to 3000 holders

    try:
        for _ in range(max_pages):
            payload = {
                "jsonrpc": "2.0",
                "id": "holder-enrichment",
                "method": "getTokenAccounts",
                "params": {
                    "mint": mint,
                    "limit": 1000,
                    "options": {"showZeroBalance": False},
                },
            }
            if cursor:
                payload["params"]["cursor"] = cursor

            data = await helius_request(session, "post", url, json=payload)
            if data is None:
                return holder_count if holder_count > 0 else None

            result = data.get("result", {})
            accounts = result.get("token_accounts", [])
            holder_count += len(accounts)
            cursor = result.get("cursor")

            if not cursor or not accounts:
                break

            await asyncio.sleep(0.2)

        return holder_count
    except Exception:
        logger.warning("Helius holder lookup failed", contract_address=mint, exc_info=True)
        return holder_count if holder_count > 0 else None


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
        txns = await helius_request(session, "get", url, params=params)
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

    # BL-022: Top-3 wallet concentration — NOT computed here.
    # Transaction volume concentration != holder concentration.
    # This field should only be set by Rugcheck (actual holder data).

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
    url = helius_rpc_url(settings.HELIUS_API_KEY)

    # Get token metadata to find creator/authority
    payload = {
        "jsonrpc": "2.0",
        "id": "deployer-check",
        "method": "getAsset",
        "params": {"id": mint},
    }
    try:
        data = await helius_request(session, "post", url, json=payload)
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
        bal_data = await helius_request(session, "post", url, json=balance_payload)
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
