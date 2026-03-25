"""Shared Helius API utilities — single semaphore for rate limiting."""

import asyncio
import time

import aiohttp
import structlog

logger = structlog.get_logger()

HELIUS_RPC = "https://mainnet.helius-rpc.com"
HELIUS_API = "https://api.helius.xyz/v0"

# Single semaphore shared across ALL Helius callers (lazily initialized)
_helius_semaphore: asyncio.Semaphore | None = None
HELIUS_DELAY = 0.2

# Daily call counter — auto-disable at limit to avoid burning credits
_daily_calls = 0
_daily_reset_time = 0.0
DAILY_CALL_LIMIT = 150_000  # 5M/month ≈ 166K/day, leave buffer


_helius_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _get_helius_semaphore() -> asyncio.Semaphore:
    """Lazily create semaphore, resetting if the event loop changed."""
    global _helius_semaphore, _helius_semaphore_loop
    loop = asyncio.get_running_loop()
    if _helius_semaphore is None or _helius_semaphore_loop is not loop:
        _helius_semaphore = asyncio.Semaphore(3)  # Reduced from 5 to conserve credits
        _helius_semaphore_loop = loop
    return _helius_semaphore

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
    """Make a Helius request with shared semaphore and retry on 429.

    Semaphore is held only during the actual request, released before
    retry backoff sleeps to avoid blocking other callers.
    """
    global _daily_calls, _daily_reset_time
    now = time.monotonic()
    if now - _daily_reset_time > 86400:
        _daily_calls = 0
        _daily_reset_time = now

    if _daily_calls >= DAILY_CALL_LIMIT:
        logger.warning("Helius daily limit reached — auto-disabled", calls=_daily_calls, limit=DAILY_CALL_LIMIT)
        return None

    _daily_calls += 1
    if _daily_calls == int(DAILY_CALL_LIMIT * 0.8):
        logger.warning("Helius 80% daily limit warning", calls=_daily_calls, limit=DAILY_CALL_LIMIT)

    for attempt in range(_MAX_RETRIES):
        need_retry = False
        async with _get_helius_semaphore():
            await asyncio.sleep(HELIUS_DELAY)
            try:
                req_fn = session.post if method == "post" else session.get
                async with req_fn(url, **kwargs) as resp:
                    if resp.status == 429:
                        need_retry = True
                    elif method == "post":
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
                    need_retry = True
                else:
                    raise
        # Semaphore released — sleep outside it so other callers aren't blocked
        if need_retry:
            wait = _RETRY_BACKOFF[attempt] if attempt < len(_RETRY_BACKOFF) else 4.0
            if attempt < _MAX_RETRIES - 1:
                logger.warning("Helius rate limited, retrying", attempt=attempt + 1, wait=wait)
                await asyncio.sleep(wait)
    logger.warning("Helius request failed after retries", reason="rate_limited_429", attempts=_MAX_RETRIES)
    return None


def get_daily_call_count() -> int:
    """Return current daily Helius API call count."""
    return _daily_calls
