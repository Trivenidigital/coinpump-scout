"""Social sentiment enrichment for candidate tokens.

Gathers social signal data from multiple free sources:
1. Reddit search (free, no API key required)
2. LunarCrush API (requires LUNARCRUSH_API_KEY)

Results are combined into social_mentions_24h and social_score fields
on the CandidateToken model.
"""

import asyncio

import aiohttp
import structlog

from scout.config import Settings
from scout.models import CandidateToken

logger = structlog.get_logger()

# Rate-limit: 3 seconds between Reddit requests to avoid 403 from VPS IPs
_REDDIT_DELAY_SEC = 3.0

# Use old.reddit.com — less aggressive rate limiting on server IPs
_REDDIT_SEARCH_URL = "https://old.reddit.com/search.json"

# LunarCrush v4 public endpoint
_LUNARCRUSH_URL = "https://lunarcrush.com/api4/public/coins/{symbol}/v1"

# Scores are weighted-combined into a 0-100 social_score
_REDDIT_WEIGHT = 0.4
_LUNARCRUSH_WEIGHT = 0.6


async def _fetch_reddit_mentions(
    ticker: str,
    token_name: str,
    session: aiohttp.ClientSession,
) -> int:
    """Search Reddit for recent mentions of the token.

    Returns the number of posts found in the last 24 hours.
    Reddit's public JSON endpoint does not require authentication.
    """
    # Search for both ticker and token name to get broader coverage.
    # Use the ticker with $ prefix (common in crypto communities) plus the name.
    query = f"{ticker} OR ${ticker} OR {token_name}"
    params = {
        "q": query,
        "sort": "new",
        "limit": "25",
        "t": "day",
        "restrict_sr": "",
    }
    headers = {
        # Reddit requires a non-generic User-Agent or it returns 429
        "User-Agent": "Mozilla/5.0 (compatible; CoinPumpScout/1.0; +https://github.com/Trivenidigital)",
    }

    try:
        async with session.get(
            _REDDIT_SEARCH_URL,
            params=params,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 429:
                logger.debug("Reddit rate limited, skipping", ticker=ticker)
                return 0
            if resp.status != 200:
                logger.debug(
                    "Reddit search returned non-200",
                    ticker=ticker,
                    status=resp.status,
                )
                return 0

            data = await resp.json()
            children = data.get("data", {}).get("children", [])
            return len(children)

    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.debug("Reddit search failed", ticker=ticker, error=str(exc))
        return 0


async def _fetch_lunarcrush(
    ticker: str,
    session: aiohttp.ClientSession,
    api_key: str,
) -> dict:
    """Fetch social metrics from LunarCrush v4 public API.

    Returns a dict with:
        - social_volume: int (number of social posts)
        - social_score: float (0-100 LunarCrush score)
        - galaxy_score: float (0-100 overall score)

    Returns empty dict on failure.
    """
    url = _LUNARCRUSH_URL.format(symbol=ticker.upper())
    headers = {
        "Authorization": f"Bearer {api_key}",
    }

    try:
        async with session.get(
            url,
            headers=headers,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                logger.debug(
                    "LunarCrush returned non-200",
                    ticker=ticker,
                    status=resp.status,
                )
                return {}

            data = await resp.json()
            # LunarCrush v4 returns data directly or nested under "data"
            payload = data.get("data", data) if isinstance(data, dict) else {}
            if not isinstance(payload, dict):
                return {}

            return {
                "social_volume": int(payload.get("social_volume", 0) or 0),
                "social_score": float(payload.get("social_score", 0) or 0),
                "galaxy_score": float(payload.get("galaxy_score", 0) or 0),
            }

    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.debug("LunarCrush request failed", ticker=ticker, error=str(exc))
        return {}


def _compute_social_score(
    reddit_mentions: int,
    lunarcrush_data: dict,
) -> float:
    """Combine sources into a single 0-100 social_score.

    - Reddit: scale mentions into 0-100 (cap at 25 mentions = 100)
    - LunarCrush: use galaxy_score directly (already 0-100)

    If LunarCrush data is unavailable, Reddit gets full weight.
    """
    # Reddit component: 0-100, linear scale capped at 25 mentions
    reddit_score = min(100.0, (reddit_mentions / 25) * 100)

    if lunarcrush_data:
        lc_score = lunarcrush_data.get("galaxy_score", 0.0)
        # Weighted combination
        combined = (reddit_score * _REDDIT_WEIGHT) + (lc_score * _LUNARCRUSH_WEIGHT)
    else:
        # Reddit only -- use full weight
        combined = reddit_score

    return min(100.0, round(combined, 1))


async def enrich_social_sentiment(
    token: CandidateToken,
    session: aiohttp.ClientSession,
    settings: Settings,
) -> CandidateToken:
    """Enrich a token with social sentiment data from multiple sources.

    Fetches data from Reddit (free) and LunarCrush (if API key configured).
    Updates social_mentions_24h and social_score on the token.

    On any failure, returns the token unchanged -- social enrichment is
    best-effort and must never block the pipeline.

    Args:
        token: The candidate token to enrich.
        session: Shared aiohttp session.
        settings: Application settings (for API keys and feature flags).

    Returns:
        A copy of the token with social fields populated.
    """
    if not settings.SOCIAL_ENRICHMENT_ENABLED:
        return token

    try:
        # 1. Reddit mentions (always available, free)
        reddit_mentions = await _fetch_reddit_mentions(
            token.ticker, token.token_name, session,
        )

        # 2. LunarCrush (only if API key is configured)
        lunarcrush_data: dict = {}
        if settings.LUNARCRUSH_API_KEY:
            lunarcrush_data = await _fetch_lunarcrush(
                token.ticker, session, settings.LUNARCRUSH_API_KEY,
            )

        # Combine mention counts
        total_mentions = reddit_mentions
        if lunarcrush_data:
            total_mentions += lunarcrush_data.get("social_volume", 0)

        # Compute composite social score
        social_score = _compute_social_score(reddit_mentions, lunarcrush_data)

        logger.debug(
            "Social enrichment complete",
            ticker=token.ticker,
            reddit_mentions=reddit_mentions,
            lunarcrush_available=bool(lunarcrush_data),
            total_mentions=total_mentions,
            social_score=social_score,
        )

        return token.model_copy(
            update={
                "social_mentions_24h": total_mentions,
                "social_score": social_score,
            },
        )

    except Exception as exc:
        # Social enrichment is best-effort -- never break the pipeline
        logger.warning(
            "Social enrichment failed, returning token unchanged",
            ticker=token.ticker,
            error=str(exc),
        )
        return token
