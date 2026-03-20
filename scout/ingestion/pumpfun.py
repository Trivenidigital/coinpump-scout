"""Pump.fun graduated token detection via DexScreener APIs.

Pump.fun tokens that "graduate" from the bonding curve to Raydium have proven
demand and are strong trading signals.  We identify them by querying DexScreener
for recently-profiled Solana tokens whose contract address ends with "pump"
(the pump.fun naming convention), then fetch full pair data for market-cap /
liquidity / volume filtering.
"""

import asyncio
from collections import defaultdict

import aiohttp
import structlog

from scout.config import Settings
from scout.models import CandidateToken

logger = structlog.get_logger()

PROFILES_URL = "https://api.dexscreener.com/token-profiles/latest/v1"
BOOST_URL = "https://api.dexscreener.com/token-boosts/latest/v1"
TOKEN_URL = "https://api.dexscreener.com/tokens/v1"

MAX_RETRIES = 3
MAX_CONCURRENT = 5


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
                        "PumpFun DexScreener returned error, retrying",
                        status=resp.status, wait=wait,
                        attempt=attempt + 1, retries=retries,
                    )
                    await asyncio.sleep(wait)
                    continue
                if resp.status != 200:
                    logger.warning(
                        "PumpFun DexScreener returned error",
                        status=resp.status,
                    )
                    return None
                return await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            wait = 2 ** attempt
            logger.warning(
                "PumpFun DexScreener request failed, retrying",
                error=str(exc), wait=wait,
            )
            await asyncio.sleep(wait)
    logger.warning("PumpFun DexScreener failed after retries", retries=retries)
    return None


def _is_pumpfun_token(entry: dict) -> bool:
    """Return True if the entry looks like a graduated pump.fun token on Solana."""
    chain = entry.get("chainId", "").lower()
    address = entry.get("tokenAddress", "")
    return chain == "solana" and address.endswith("pump")


async def _collect_pumpfun_addresses(
    session: aiohttp.ClientSession,
) -> list[str]:
    """Gather pump.fun graduated token addresses from DexScreener profiles + boosts."""
    seen: set[str] = set()
    addresses: list[str] = []

    profiles, boosts = await asyncio.gather(
        _get_json(session, PROFILES_URL),
        _get_json(session, BOOST_URL),
        return_exceptions=True,
    )

    for source_name, source_data in [("profiles", profiles), ("boosts", boosts)]:
        if isinstance(source_data, Exception):
            logger.warning(
                "PumpFun source fetch failed",
                source=source_name, error=str(source_data),
            )
            continue
        if not source_data or not isinstance(source_data, list):
            continue
        for entry in source_data:
            if _is_pumpfun_token(entry):
                addr = entry["tokenAddress"]
                if addr not in seen:
                    seen.add(addr)
                    addresses.append(addr)

    return addresses


async def fetch_pumpfun_graduated(
    session: aiohttp.ClientSession,
    settings: Settings,
) -> list[CandidateToken]:
    """Fetch recently graduated pump.fun tokens from DexScreener.

    1. Fetch latest token profiles and boosts from DexScreener.
    2. Filter for Solana tokens whose address ends with "pump" (pump.fun convention).
    3. For each address, fetch pair data to get market cap, liquidity, volume.
    4. Filter by market cap range from settings.
    5. Return as list[CandidateToken].
    """
    addresses = await _collect_pumpfun_addresses(session)
    if not addresses:
        logger.info("PumpFun: no graduated tokens found")
        return []

    logger.info("PumpFun: found candidate addresses", count=len(addresses))

    sem = asyncio.Semaphore(MAX_CONCURRENT)

    async def _fetch_one(address: str) -> list[CandidateToken]:
        async with sem:
            url = f"{TOKEN_URL}/solana/{address}"
            pairs = await _get_json(session, url)
            if not pairs or not isinstance(pairs, list):
                return []

            results: list[CandidateToken] = []
            for pair_data in pairs:
                fdv = float(pair_data.get("fdv") or 0)
                if not (settings.MIN_MARKET_CAP <= fdv <= settings.MAX_MARKET_CAP):
                    continue

                try:
                    token = CandidateToken.from_dexscreener(pair_data)
                except Exception:
                    logger.exception("Failed to parse PumpFun pair data")
                    continue

                results.append(token)
            return results

    tasks = [_fetch_one(addr) for addr in addresses]
    gather_results = await asyncio.gather(*tasks, return_exceptions=True)

    candidates: list[CandidateToken] = []
    for result in gather_results:
        if isinstance(result, Exception):
            logger.warning("PumpFun token fetch failed", error=str(result))
            continue
        candidates.extend(result)

    logger.info(
        "PumpFun: found candidates",
        candidate_count=len(candidates),
        addresses_checked=len(addresses),
    )
    return candidates
