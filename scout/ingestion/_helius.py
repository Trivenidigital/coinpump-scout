"""Shared Helius API utilities — single semaphore for rate limiting."""

import asyncio

import aiohttp
import structlog

logger = structlog.get_logger()

HELIUS_RPC = "https://mainnet.helius-rpc.com"
HELIUS_API = "https://api.helius.xyz/v0"

# Single semaphore shared across ALL Helius callers
helius_semaphore = asyncio.Semaphore(1)
HELIUS_DELAY = 0.5

_MAX_RETRIES = 4
_RETRY_BACKOFF = [2.0, 4.0, 8.0, 12.0]


def helius_rpc_url(api_key: str) -> str:
    """Build Helius RPC URL with key in path (Helius standard pattern)."""
    return f"{HELIUS_RPC}/?api-key={api_key}"


async def helius_request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    **kwargs,
) -> dict | list | None:
    """Make a Helius request with shared semaphore and retry on 429."""
    async with helius_semaphore:
        await asyncio.sleep(HELIUS_DELAY)
        for attempt in range(_MAX_RETRIES):
            try:
                req_fn = session.post if method == "post" else session.get
                async with req_fn(url, **kwargs) as resp:
                    if resp.status == 429:
                        wait = _RETRY_BACKOFF[attempt] if attempt < len(_RETRY_BACKOFF) else 4.0
                        logger.warning("Helius rate limited, retrying", attempt=attempt + 1, wait=wait)
                        await asyncio.sleep(wait)
                        continue
                    if method == "post":
                        resp.raise_for_status()
                        data = await resp.json()
                        if isinstance(data, dict) and "error" in data:
                            logger.warning("Helius RPC error", error=data["error"])
                            return None
                        return data
                    else:
                        if resp.status != 200:
                            return None
                        return await resp.json()
            except aiohttp.ClientError:
                if attempt < _MAX_RETRIES - 1:
                    await asyncio.sleep(_RETRY_BACKOFF[attempt])
                    continue
                raise
    logger.warning("Helius request failed after retries", url=url)
    return None
