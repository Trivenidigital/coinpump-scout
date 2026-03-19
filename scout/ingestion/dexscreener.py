"""DexScreener API poller for trending tokens."""

import asyncio
import logging
from collections import defaultdict

import aiohttp

from scout.config import Settings
from scout.exceptions import IngestionError
from scout.models import CandidateToken

logger = logging.getLogger(__name__)

BOOST_URL = "https://api.dexscreener.com/token-boosts/latest/v1"
TOKEN_URL = "https://api.dexscreener.com/tokens/v1"

MAX_RETRIES = 3


async def _get_json(
    session: aiohttp.ClientSession,
    url: str,
    *,
    retries: int = MAX_RETRIES,
) -> list | dict | None:
    """GET a URL with exponential backoff on 429 / 5xx."""
    for attempt in range(retries):
        try:
            async with session.get(url) as resp:
                if resp.status == 429 or resp.status >= 500:
                    wait = 2 ** attempt
                    logger.warning(
                        "DexScreener %s returned %s, retrying in %ss (attempt %d/%d)",
                        url, resp.status, wait, attempt + 1, retries,
                    )
                    await asyncio.sleep(wait)
                    continue
                if resp.status != 200:
                    logger.warning("DexScreener %s returned %s", url, resp.status)
                    return None
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            wait = 2 ** attempt
            logger.warning(
                "DexScreener request to %s failed: %s, retrying in %ss",
                url, exc, wait,
            )
            await asyncio.sleep(wait)
    logger.warning("DexScreener %s failed after %d retries", url, retries)
    return None


async def fetch_trending(
    session: aiohttp.ClientSession,
    settings: Settings,
) -> list[CandidateToken]:
    """Fetch trending tokens from DexScreener.

    1. Get boosted/trending token addresses from the boosts endpoint.
    2. For each, fetch full pair data from the tokens endpoint.
    3. Filter by market cap range and token age.
    4. Return list of CandidateToken.
    """
    boosts = await _get_json(session, BOOST_URL)
    if not boosts:
        return []

    # Group token addresses by chain for batched lookups
    chain_tokens: dict[str, list[str]] = defaultdict(list)
    for entry in boosts:
        chain = entry.get("chainId", "")
        address = entry.get("tokenAddress", "")
        if chain and address and address not in chain_tokens[chain]:
            chain_tokens[chain].append(address)

    candidates: list[CandidateToken] = []

    for chain, addresses in chain_tokens.items():
        for address in addresses:
            url = f"{TOKEN_URL}/{chain}/{address}"
            pairs = await _get_json(session, url)
            if not pairs or not isinstance(pairs, list):
                continue

            for pair_data in pairs:
                fdv = float(pair_data.get("fdv") or 0)
                if not (settings.MIN_MARKET_CAP <= fdv <= settings.MAX_MARKET_CAP):
                    continue

                try:
                    token = CandidateToken.from_dexscreener(pair_data)
                except Exception:
                    logger.exception("Failed to parse DexScreener pair data")
                    continue

                candidates.append(token)

    logger.info("DexScreener: found %d candidates from %d boosts", len(candidates), len(boosts))
    return candidates
