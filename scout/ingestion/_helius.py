"""Shared Helius API utilities — single semaphore for rate limiting."""

import asyncio
import hashlib
import json
import time

import aiohttp
import structlog

logger = structlog.get_logger()

HELIUS_RPC = "https://mainnet.helius-rpc.com"
HELIUS_API = "https://api.helius.xyz/v0"

# Single semaphore shared across ALL Helius callers (lazily initialized)
_helius_semaphore: asyncio.Semaphore | None = None
HELIUS_DELAY = 0.2

# Response cache: avoids re-fetching the same data across scan cycles.
# Key = hash of (url, params/json body), Value = (response, timestamp).
# TTL = 300s (5 min) for transaction data, 600s (10 min) for holder data.
_response_cache: dict[str, tuple[any, float]] = {}
_CACHE_TTL_DEFAULT = 300  # 5 minutes
_CACHE_TTL_HOLDERS = 600  # 10 minutes for holder counts (change slowly)
_CACHE_MAX_SIZE = 500

_helius_semaphore_loop: asyncio.AbstractEventLoop | None = None


def _get_helius_semaphore() -> asyncio.Semaphore:
    """Lazily create semaphore, resetting if the event loop changed."""
    global _helius_semaphore, _helius_semaphore_loop
    loop = asyncio.get_running_loop()
    if _helius_semaphore is None or _helius_semaphore_loop is not loop:
        _helius_semaphore = asyncio.Semaphore(5)
        _helius_semaphore_loop = loop
    return _helius_semaphore

_MAX_RETRIES = 4
_RETRY_BACKOFF = [2.0, 4.0, 8.0, 12.0]

# Daily call counter (reset on first call each day)
_daily_call_count = 0
_daily_call_date = ""


def _cache_key(url: str, **kwargs) -> str:
    """Build a stable cache key from URL + request params."""
    # Strip API key from URL for cleaner keys
    clean_url = url.split("api-key=")[0] if "api-key=" in url else url
    payload = kwargs.get("json") or kwargs.get("params") or {}
    raw = f"{clean_url}:{json.dumps(payload, sort_keys=True, default=str)}"
    return hashlib.md5(raw.encode()).hexdigest()


def _cache_ttl(url: str, **kwargs) -> int:
    """Choose TTL based on request type.

    getTokenAccounts is NOT cached (TTL=0) — holder_growth needs fresh
    data every cycle to detect changes. getAsset is stable and cached.
    """
    payload = kwargs.get("json", {})
    if isinstance(payload, dict):
        method = payload.get("method", "")
        if method == "getTokenAccounts":
            return 120  # 2 min cache — balance between fresh holder_growth and credit burn
        if method == "getAsset":
            return _CACHE_TTL_HOLDERS  # 10 min for asset metadata
    return _CACHE_TTL_DEFAULT  # 5 min for transactions


def _prune_cache() -> None:
    """Remove expired entries when cache gets too large."""
    if len(_response_cache) <= _CACHE_MAX_SIZE:
        return
    now = time.monotonic()
    expired = [k for k, (_, ts) in _response_cache.items() if now - ts > _CACHE_TTL_HOLDERS]
    for k in expired:
        del _response_cache[k]


def get_daily_call_count() -> int:
    """Return today's Helius API call count."""
    return _daily_call_count


def helius_rpc_url(api_key: str) -> str:
    """Build Helius RPC URL with key in path (Helius standard pattern)."""
    return f"{HELIUS_RPC}/?api-key={api_key}"


async def helius_request(
    session: aiohttp.ClientSession,
    method: str,
    url: str,
    **kwargs,
) -> dict | list | None:
    """Make a Helius request with shared semaphore, caching, and retry on 429.

    Responses are cached by URL+params with a TTL (5 min for transactions,
    10 min for holder data). Cache hits avoid API calls entirely.
    """
    global _daily_call_count, _daily_call_date
    import datetime
    today = datetime.date.today().isoformat()
    if _daily_call_date != today:
        _daily_call_count = 0
        _daily_call_date = today

    # Check cache first
    key = _cache_key(url, **kwargs)
    cached = _response_cache.get(key)
    if cached is not None:
        data, ts = cached
        ttl = _cache_ttl(url, **kwargs)
        if time.monotonic() - ts < ttl:
            return data

    for attempt in range(_MAX_RETRIES):
        need_retry = False
        async with _get_helius_semaphore():
            await asyncio.sleep(HELIUS_DELAY)
            try:
                _daily_call_count += 1
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
                        _response_cache[key] = (data, time.monotonic())
                        _prune_cache()
                        return data
                    else:
                        if resp.status != 200:
                            return None
                        data = await resp.json()
                        _response_cache[key] = (data, time.monotonic())
                        _prune_cache()
                        return data
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
