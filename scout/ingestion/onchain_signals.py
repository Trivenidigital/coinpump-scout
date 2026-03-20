"""On-chain signal enrichment: smart money, liquidity locks, volume spikes, multi-DEX.

High-signal checks that run after holder enrichment:
1. Smart money / whale detection via Helius parsed transactions
2. Liquidity lock check via DexScreener pair data
3. Volume spike detection via historical DB comparison
4. Multi-DEX listing check via Jupiter route plans
5. CEX listing check via CoinGecko
"""

import asyncio

import aiohttp
import structlog

from scout.config import Settings
from scout.db import Database
from scout.ingestion.cex_monitor import check_cex_listing
from scout.models import CandidateToken

logger = structlog.get_logger()

HELIUS_API = "https://api.helius.xyz/v0"
HELIUS_RPC = "https://mainnet.helius-rpc.com"
DEXSCREENER_PAIR_URL = "https://api.dexscreener.com/tokens/v1"

# Known smart-money / alpha wallets (curated set — extend as needed)
SMART_MONEY_WALLETS: set[str] = set()

# Whale buy threshold in USD-equivalent token amount
_WHALE_USD_THRESHOLD = 1_000.0

# Rate-limit guard: reuse the same pattern as holder_enricher
_helius_semaphore = asyncio.Semaphore(1)
_HELIUS_DELAY = 0.5
_MAX_RETRIES = 4
_RETRY_BACKOFF = [2.0, 4.0, 8.0, 12.0]

# Dead/burn addresses used to detect permanently locked liquidity
_BURN_ADDRESSES = {
    "1nc1nerator11111111111111111111111111111111",
    "11111111111111111111111111111111",
    "1111111111111111111111111111111111111111111",
}


# ------------------------------------------------------------------
# Helius request helper (mirrors holder_enricher._helius_request)
# ------------------------------------------------------------------

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
                            logger.warning("Helius rate limited (onchain_signals)", attempt=attempt + 1, wait=wait)
                            await asyncio.sleep(wait)
                            continue
                        resp.raise_for_status()
                        data = await resp.json()
                        if isinstance(data, dict) and "error" in data:
                            logger.warning("Helius RPC error (onchain_signals)", error=data["error"])
                            return None
                        return data
                else:
                    async with session.get(url, **kwargs) as resp:
                        if resp.status == 429:
                            wait = _RETRY_BACKOFF[attempt] if attempt < len(_RETRY_BACKOFF) else 4.0
                            logger.warning("Helius rate limited (onchain_signals)", attempt=attempt + 1, wait=wait)
                            await asyncio.sleep(wait)
                            continue
                        if resp.status != 200:
                            return None
                        return await resp.json()
            except aiohttp.ClientError:
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_BACKOFF[attempt])
                    continue
                raise
    logger.warning("Helius request failed after retries (onchain_signals)", url=url)
    return None


# ------------------------------------------------------------------
# 1. Smart Money Detection
# ------------------------------------------------------------------

async def check_smart_money(
    mint: str,
    session: aiohttp.ClientSession,
    settings: Settings,
) -> dict:
    """Detect smart-money and whale activity in recent swaps.

    Fetches the last 50 SWAP transactions for *mint* from Helius, extracts
    unique buyer wallets, and checks for:
    - Buyers present in the curated SMART_MONEY_WALLETS set
    - Whale buys (any single swap where native value > _WHALE_USD_THRESHOLD)
    - Unique recent buyer count

    Returns:
        {"smart_money_buys": int, "whale_buys": int, "unique_buyers_recent": int}
    """
    defaults = {"smart_money_buys": 0, "whale_buys": 0, "unique_buyers_recent": 0}

    if not settings.HELIUS_API_KEY:
        return defaults

    url = f"{HELIUS_API}/addresses/{mint}/transactions"
    params = {"api-key": settings.HELIUS_API_KEY, "limit": 50, "type": "SWAP"}

    try:
        txns = await _helius_request(session, "get", url, params=params)
        if not txns or not isinstance(txns, list):
            return defaults
    except Exception:
        logger.warning("Smart money check failed", contract_address=mint, exc_info=True)
        return defaults

    buyer_wallets: set[str] = set()
    smart_money_count = 0
    whale_count = 0

    for txn in txns:
        fee_payer = txn.get("feePayer", "")
        native_transfers = txn.get("nativeTransfers", [])
        token_transfers = txn.get("tokenTransfers", [])

        # Determine if this is a buy: token moves TO a wallet
        is_buy = False
        buyer = ""
        for transfer in token_transfers:
            if transfer.get("mint") == mint:
                to_addr = transfer.get("toUserAccount", "")
                if to_addr and to_addr != mint:
                    is_buy = True
                    buyer = to_addr
                    break

        if not is_buy:
            continue

        buyer_wallets.add(buyer)

        # Check smart money set
        if buyer in SMART_MONEY_WALLETS or fee_payer in SMART_MONEY_WALLETS:
            smart_money_count += 1

        # Whale detection: estimate USD value from native SOL transferred
        # Helius nativeTransfers amounts are in lamports (1 SOL = 1e9 lamports)
        # Rough SOL price estimate used only for whale threshold classification
        sol_spent = 0.0
        for nt in native_transfers:
            if nt.get("fromUserAccount") == fee_payer:
                sol_spent += abs(float(nt.get("amount", 0))) / 1e9

        # Use a conservative SOL price floor for whale classification
        estimated_usd = sol_spent * 150  # conservative SOL/USD estimate
        if estimated_usd >= _WHALE_USD_THRESHOLD:
            whale_count += 1

    return {
        "smart_money_buys": smart_money_count,
        "whale_buys": whale_count,
        "unique_buyers_recent": len(buyer_wallets),
    }


# ------------------------------------------------------------------
# 2. Liquidity Lock Check
# ------------------------------------------------------------------

async def check_liquidity_lock(
    mint: str,
    session: aiohttp.ClientSession,
    settings: Settings,
) -> dict:
    """Check whether a token's liquidity is locked or burned.

    Uses the DexScreener tokens endpoint to inspect pair data for lock info
    and burned LP tokens.

    Returns:
        {"liquidity_locked": bool, "lock_source": str | None}
    """
    defaults: dict = {"liquidity_locked": False, "lock_source": None}

    # Query DexScreener for pair data (works for any chain)
    url = f"{DEXSCREENER_PAIR_URL}/solana/{mint}"
    try:
        async with session.get(url) as resp:
            if resp.status != 200:
                return defaults
            pairs = await resp.json()
    except Exception:
        logger.warning("Liquidity lock check failed (DexScreener)", contract_address=mint, exc_info=True)
        return defaults

    if not pairs or not isinstance(pairs, list):
        return defaults

    for pair in pairs:
        # Check for explicit lock info in DexScreener response
        liquidity = pair.get("liquidity", {})
        locks = pair.get("locks") or liquidity.get("locks")
        if locks:
            return {"liquidity_locked": True, "lock_source": "dexscreener_locks"}

        # Check if LP tokens are burned (sent to dead address)
        # DexScreener includes info.labels for burned tokens
        info = pair.get("info", {})
        labels = [lbl.lower() if isinstance(lbl, str) else "" for lbl in (info.get("labels") or [])]
        if "burned" in labels or "burn" in labels:
            return {"liquidity_locked": True, "lock_source": "lp_burned"}

        # Check for pump.fun graduated tokens (have Raydium/Meteora pool)
        dex_id = pair.get("dexId", "")
        if dex_id in ("raydium", "meteora"):
            # Having a DEX pool means graduated from pump.fun bonding curve
            # Not locked per se, but indicates maturity — mark as partial
            pair_labels = pair.get("labels") or []
            if "pump.fun" in str(pair_labels).lower() or "pumpfun" in str(pair.get("url", "")).lower():
                return {"liquidity_locked": False, "lock_source": "pumpfun_graduated"}

    return defaults


# ------------------------------------------------------------------
# 3. Volume Spike Detection
# ------------------------------------------------------------------

async def check_volume_spike(
    mint: str,
    current_volume_24h: float,
    db: Database,
    settings: Settings,
) -> dict:
    """Detect abnormal volume spikes by comparing to historical average.

    Stores current volume in the ``volume_history`` table and compares
    against the average of the last 3 recordings.

    Returns:
        {"volume_spike": bool, "volume_ratio": float, "avg_volume": float}
    """
    defaults: dict = {"volume_spike": False, "volume_ratio": 0.0, "avg_volume": 0.0}

    try:
        avg_volume = await db.get_avg_volume(mint, lookback=3)
        await db.log_volume(mint, current_volume_24h)

        if avg_volume is None or avg_volume <= 0:
            return defaults

        ratio = current_volume_24h / avg_volume
        is_spike = ratio > 3.0

        return {
            "volume_spike": is_spike,
            "volume_ratio": round(ratio, 2),
            "avg_volume": round(avg_volume, 2),
        }
    except Exception:
        logger.warning("Volume spike check failed", contract_address=mint, exc_info=True)
        return defaults


# ------------------------------------------------------------------
# 4. Token Distribution Analysis (Gini Coefficient)
# ------------------------------------------------------------------

async def check_holder_distribution(
    mint: str,
    session: aiohttp.ClientSession,
    settings: Settings,
) -> dict:
    """Analyse top-holder concentration via Helius getTokenAccounts.

    Fetches the top 20 holder balances and calculates what percentage of
    the top-20 total is held by the top 5 wallets.  If the top 5 hold
    less than 30% of the top-20 total, distribution is considered healthy.

    Returns:
        {"holder_gini_healthy": bool, "top5_concentration": float}
    """
    defaults: dict = {"holder_gini_healthy": False, "top5_concentration": 0.0}

    if not settings.HELIUS_API_KEY:
        return defaults

    url = f"{HELIUS_RPC}/?api-key={settings.HELIUS_API_KEY}"
    payload = {
        "jsonrpc": "2.0",
        "id": "holder-distribution",
        "method": "getTokenAccounts",
        "params": {"mint": mint, "limit": 20},
    }

    try:
        data = await _helius_request(session, "post", url, json=payload)
        if data is None:
            return defaults

        accounts = data.get("result", {}).get("token_accounts", [])
        if not accounts:
            return defaults

        # Extract balances and sort descending
        balances = sorted(
            [float(a.get("amount", 0)) for a in accounts],
            reverse=True,
        )

        top20_total = sum(balances)
        if top20_total <= 0:
            return defaults

        top5_total = sum(balances[:5])
        concentration = top5_total / top20_total

        return {
            "holder_gini_healthy": concentration < 0.30,
            "top5_concentration": round(concentration, 4),
        }
    except Exception:
        logger.warning("Holder distribution check failed", contract_address=mint, exc_info=True)
        return defaults


# ------------------------------------------------------------------
# 5. Whale Alert (Large Transactions)
# ------------------------------------------------------------------

async def check_whale_activity(
    mint: str,
    session: aiohttp.ClientSession,
    settings: Settings,
) -> dict:
    """Count recent large transactions (> 1 SOL equivalent) for *mint*.

    Re-uses the same Helius parsed-transactions endpoint as
    ``check_smart_money``, scanning the last 50 SWAP transactions and
    counting those where the SOL value transferred exceeds 1 SOL.

    Returns:
        {"whale_txns_1h": int}
    """
    defaults: dict = {"whale_txns_1h": 0}

    if not settings.HELIUS_API_KEY:
        return defaults

    url = f"{HELIUS_API}/addresses/{mint}/transactions"
    params = {"api-key": settings.HELIUS_API_KEY, "limit": 50, "type": "SWAP"}

    try:
        txns = await _helius_request(session, "get", url, params=params)
        if not txns or not isinstance(txns, list):
            return defaults
    except Exception:
        logger.warning("Whale activity check failed", contract_address=mint, exc_info=True)
        return defaults

    whale_count = 0
    for txn in txns:
        fee_payer = txn.get("feePayer", "")
        native_transfers = txn.get("nativeTransfers", [])

        # Sum SOL spent by the fee payer (buyer) in this transaction
        sol_spent = 0.0
        for nt in native_transfers:
            if nt.get("fromUserAccount") == fee_payer:
                sol_spent += abs(float(nt.get("amount", 0))) / 1e9

        if sol_spent > 1.0:
            whale_count += 1

    return {"whale_txns_1h": whale_count}


# ------------------------------------------------------------------
# 6. Multi-DEX Listing Check (Jupiter route plans)
# ------------------------------------------------------------------

_JUPITER_QUOTE_URL = "https://quote-api.jup.ag/v6/quote"

# USDC mint on Solana (used as output mint for Jupiter quote)
_USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


async def check_multi_dex(
    mint: str,
    session: aiohttp.ClientSession,
    settings: Settings,
) -> dict:
    """Check if a token is traded on multiple DEXs via Jupiter route plans.

    Requests a Jupiter quote for a small swap of the token to USDC. Jupiter
    returns a routePlan array where each entry represents a hop through a
    different DEX. Multiple routes indicate the token has liquidity across
    multiple venues, which is a health and legitimacy signal.

    Only works for Solana tokens (Jupiter is Solana-only).

    Args:
        mint: The token's mint/contract address.
        session: Shared aiohttp session.
        settings: Application settings.

    Returns:
        {"multi_dex": bool, "dex_count": int}
    """
    defaults: dict = {"multi_dex": False, "dex_count": 0}

    try:
        # Request a quote for a minimal amount (1 token unit in smallest denomination)
        params = {
            "inputMint": mint,
            "outputMint": _USDC_MINT,
            "amount": "1000000",  # 1 token (assuming 6 decimals; Jupiter handles this)
            "slippageBps": "500",  # 5% slippage for illiquid tokens
        }
        async with session.get(
            _JUPITER_QUOTE_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                logger.debug(
                    "Jupiter quote returned non-200",
                    mint=mint,
                    status=resp.status,
                )
                return defaults

            data = await resp.json()
            route_plan = data.get("routePlan") or []

            # Each route plan entry has a "swapInfo" with an "ammKey" identifying the DEX
            # Count unique DEX labels/amm keys
            dex_labels: set[str] = set()
            for step in route_plan:
                swap_info = step.get("swapInfo", {})
                label = swap_info.get("label", "")
                if label:
                    dex_labels.add(label)

            dex_count = len(dex_labels)
            return {
                "multi_dex": dex_count >= 2,
                "dex_count": dex_count,
            }

    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.debug("Jupiter multi-DEX check failed", mint=mint, error=str(exc))
        return defaults
    except Exception as exc:
        logger.warning(
            "Jupiter multi-DEX check unexpected error",
            mint=mint,
            error=str(exc),
        )
        return defaults


# ------------------------------------------------------------------
# Main enrichment entry point
# ------------------------------------------------------------------

async def enrich_onchain_signals(
    token: CandidateToken,
    session: aiohttp.ClientSession,
    db: Database,
    settings: Settings,
) -> CandidateToken:
    """Run all on-chain signal checks and update the token model.

    Called after holder enrichment, before scoring. Each sub-check
    handles its own errors and returns safe defaults on failure.
    """
    if not settings.ONCHAIN_SIGNALS_ENABLED:
        return token

    updates: dict = {}

    # 1. Smart money / whale detection (Solana only, requires Helius)
    if token.chain == "solana" and settings.HELIUS_API_KEY:
        sm_data = await check_smart_money(token.contract_address, session, settings)
        updates["smart_money_buys"] = sm_data["smart_money_buys"]
        updates["whale_buys"] = sm_data["whale_buys"]

    # 2. Liquidity lock check
    lock_data = await check_liquidity_lock(token.contract_address, session, settings)
    updates["liquidity_locked"] = lock_data["liquidity_locked"]

    # 3. Volume spike detection
    vol_data = await check_volume_spike(
        token.contract_address, token.volume_24h_usd, db, settings,
    )
    updates["volume_spike"] = vol_data["volume_spike"]
    updates["volume_spike_ratio"] = vol_data["volume_ratio"]

    # 4. Holder distribution analysis (Solana only, requires Helius)
    if token.chain == "solana" and settings.HELIUS_API_KEY:
        dist_data = await check_holder_distribution(token.contract_address, session, settings)
        updates["holder_gini_healthy"] = dist_data["holder_gini_healthy"]

    # 5. Whale alert — large transactions (Solana only, requires Helius)
    if token.chain == "solana" and settings.HELIUS_API_KEY:
        whale_data = await check_whale_activity(token.contract_address, session, settings)
        updates["whale_txns_1h"] = whale_data["whale_txns_1h"]

    # 6. Multi-DEX listing check (Solana only — Jupiter is Solana-only)
    if token.chain == "solana":
        multi_dex_data = await check_multi_dex(
            token.contract_address, session, settings,
        )
        updates["multi_dex"] = multi_dex_data["multi_dex"]
        updates["dex_count"] = multi_dex_data["dex_count"]

    # 7. CEX listing check via CoinGecko (all chains)
    cex_data = await check_cex_listing(token.ticker, session)
    updates["on_coingecko"] = cex_data["on_coingecko"]

    if updates:
        return token.model_copy(update=updates)
    return token
