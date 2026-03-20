"""Birdeye API poller for trending Solana tokens."""

import asyncio

import aiohttp
import structlog

from scout.config import Settings
from scout.models import CandidateToken

logger = structlog.get_logger()

BIRDEYE_TRENDING_URL = (
    "https://public-api.birdeye.so/defi/token_trending"
    "?sort_by=rank&sort_type=asc&offset=0&limit=20"
)


async def fetch_trending_birdeye(
    session: aiohttp.ClientSession, settings: Settings
) -> list[CandidateToken]:
    """Fetch trending Solana tokens from Birdeye.

    Returns an empty list when BIRDEYE_API_KEY is not configured (graceful
    degradation).
    """
    if not settings.BIRDEYE_API_KEY:
        return []

    headers = {
        "X-API-KEY": settings.BIRDEYE_API_KEY,
        "x-chain": "solana",
    }

    try:
        async with session.get(BIRDEYE_TRENDING_URL, headers=headers) as resp:
            if resp.status != 200:
                logger.warning("Birdeye returned error", status=resp.status)
                return []
            data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning("Birdeye request error", error=str(e))
        return []

    candidates: list[CandidateToken] = []
    items = data.get("data", {}).get("items", [])

    for item in items:
        try:
            token = CandidateToken(
                contract_address=item.get("address", ""),
                chain="solana",
                token_name=item.get("name", ""),
                ticker=item.get("symbol", ""),
                market_cap_usd=float(item.get("mc", 0) or 0),
                liquidity_usd=float(item.get("liquidity", 0) or 0),
                volume_24h_usd=float(item.get("v24hUSD", 0) or 0),
            )
            if settings.MIN_MARKET_CAP <= token.market_cap_usd <= settings.MAX_MARKET_CAP:
                candidates.append(token)
        except Exception as e:
            logger.warning("Failed to parse Birdeye token", error=str(e))
            continue

    logger.info("Birdeye: found candidates", candidate_count=len(candidates))
    return candidates
