"""GeckoTerminal API poller for trending pools."""

import logging

import aiohttp

from scout.config import Settings
from scout.models import CandidateToken

logger = logging.getLogger(__name__)

GECKO_BASE = "https://api.geckoterminal.com/api/v2"


async def fetch_trending_pools(
    session: aiohttp.ClientSession, settings: Settings
) -> list[CandidateToken]:
    """Fetch trending pools from GeckoTerminal for all configured chains."""
    candidates: list[CandidateToken] = []

    for chain in settings.CHAINS:
        url = f"{GECKO_BASE}/networks/{chain}/trending_pools"
        try:
            async with session.get(url) as resp:
                if resp.status != 200:
                    logger.warning("GeckoTerminal %s returned %d", chain, resp.status)
                    continue
                data = await resp.json()
        except (aiohttp.ClientError, Exception) as e:
            logger.warning("GeckoTerminal %s error: %s", chain, e)
            continue

        for pool in data.get("data", []):
            try:
                token = CandidateToken.from_geckoterminal(pool, chain=chain)
                if settings.MIN_MARKET_CAP <= token.market_cap_usd <= settings.MAX_MARKET_CAP:
                    candidates.append(token)
            except Exception as e:
                logger.warning("Failed to parse GeckoTerminal pool: %s", e)
                continue

    return candidates
