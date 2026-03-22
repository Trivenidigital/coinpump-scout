"""Smart money feed — ingestion source for tokens detected by tracked wallet buys.

Direction 2 of smart money integration: reads from smart_money_injections table
(written by sniper's copy_trader) and creates CandidateToken objects for the
full scout pipeline.
"""

from collections import defaultdict

import aiohttp
import structlog

from scout.config import Settings
from scout.db import Database
from scout.models import CandidateToken

logger = structlog.get_logger()

DEXSCREENER_TOKENS_URL = "https://api.dexscreener.com/tokens/v1/solana"


async def fetch_smart_money_injections(
    session: aiohttp.ClientSession,
    db: Database,
    settings: Settings,
) -> list[CandidateToken]:
    """Read unprocessed smart money injections and create CandidateToken objects.

    Groups injections by token_mint, counts unique wallets per token,
    fetches metadata from DexScreener batch endpoint.
    """
    injections = await db.read_and_mark_injections()
    if not injections:
        return []

    # Group by token_mint, count unique wallets
    token_wallets: dict[str, set[str]] = defaultdict(set)
    for inj in injections:
        token_wallets[inj["token_mint"]].add(inj["wallet_address"])

    mints = list(token_wallets.keys())
    logger.info("Smart money injections to process", count=len(mints))

    # Batch fetch metadata from DexScreener
    candidates: list[CandidateToken] = []
    batch_url = f"{DEXSCREENER_TOKENS_URL}/{','.join(mints)}"

    try:
        async with session.get(
            batch_url,
            timeout=aiohttp.ClientTimeout(total=15),
        ) as resp:
            if resp.status != 200:
                logger.warning("DexScreener batch fetch failed", status=resp.status)
                return []
            data = await resp.json()
    except Exception as e:
        logger.warning("DexScreener fetch error", error=str(e))
        return []

    if not isinstance(data, list):
        data = [data] if data else []

    # Map DexScreener results by token address
    dex_by_mint: dict[str, dict] = {}
    for item in data:
        addr = item.get("tokenAddress", "")
        if addr:
            dex_by_mint[addr] = item

    for mint, wallets in token_wallets.items():
        dex_data = dex_by_mint.get(mint)
        if not dex_data:
            logger.debug("No DexScreener data for injected token", mint=mint[:20])
            continue

        info = dex_data.get("info", {})
        name = info.get("name", "Unknown")
        ticker = info.get("symbol", "???")
        mcap = dex_data.get("marketCap", 0) or 0
        liq = (dex_data.get("liquidity") or {}).get("usd", 0) or 0
        vol = (dex_data.get("volume") or {}).get("h24", 0) or 0

        candidate = CandidateToken(
            contract_address=mint,
            chain="solana",
            token_name=name,
            ticker=ticker,
            market_cap_usd=float(mcap),
            liquidity_usd=float(liq),
            volume_24h_usd=float(vol),
            smart_money_buys=len(wallets),
        )
        candidates.append(candidate)
        logger.info(
            "Smart money injection -> candidate",
            token=name,
            ticker=ticker,
            wallets=len(wallets),
            mcap=mcap,
        )

    return candidates
