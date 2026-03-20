"""GeckoTerminal API poller for trending pools."""

import asyncio

import aiohttp
import structlog

from scout.config import Settings
from scout.models import CandidateToken

logger = structlog.get_logger()

GECKO_BASE = "https://api.geckoterminal.com/api/v2"

# Map config chain names to GeckoTerminal network identifiers
_CHAIN_TO_NETWORK: dict[str, str] = {
    "ethereum": "eth",
    "solana": "solana",
    "base": "base",
}


async def fetch_trending_pools(
    session: aiohttp.ClientSession, settings: Settings
) -> list[CandidateToken]:
    """Fetch trending and new pools from GeckoTerminal for all configured chains."""
    candidates: list[CandidateToken] = []

    for chain in settings.CHAINS:
        network = _CHAIN_TO_NETWORK.get(chain, chain)
        for endpoint in ("trending_pools", "new_pools"):
            url = f"{GECKO_BASE}/networks/{network}/{endpoint}"
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "GeckoTerminal returned error",
                            chain=chain, network=network,
                            endpoint=endpoint, status=resp.status,
                        )
                        continue
                    data = await resp.json()
            except (aiohttp.ClientError, asyncio.TimeoutError) as e:
                logger.warning(
                    "GeckoTerminal request error",
                    chain=chain, network=network,
                    endpoint=endpoint, error=str(e),
                )
                continue

            for pool in data.get("data", []):
                try:
                    token = CandidateToken.from_geckoterminal(pool, chain=chain)
                    if settings.MIN_MARKET_CAP <= token.market_cap_usd <= settings.MAX_MARKET_CAP:
                        candidates.append(token)
                except Exception as e:
                    logger.warning("Failed to parse GeckoTerminal pool", error=str(e))
                    continue

    return candidates
