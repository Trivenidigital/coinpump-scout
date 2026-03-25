"""Social sentiment enrichment for candidate tokens.

Gathers social signal data from multiple free sources:
1. Reddit search (free, no API key required)
2. LunarCrush API (requires LUNARCRUSH_API_KEY)
3. Twitter/X mention detection (SocialData API or DexScreener social links)
4. Telegram presence check (from DexScreener social links)
5. GitHub presence check (from DexScreener social links)

Results are combined into social_mentions_24h and social_score fields
on the CandidateToken model.
"""

import asyncio

import aiohttp
import structlog

from scout.config import Settings
from scout.ingestion._dexscreener_cache import get_cached, set_cached
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
        # Reddit ToS requires bot-format User-Agent: platform:app_id:version (by /u/username)
        "User-Agent": "script:CoinPumpScout:v1.0 (by /u/Trivenidigital)",
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


# SocialData API for Twitter mention search
_SOCIALDATA_SEARCH_URL = "https://api.socialdata.tools/twitter/search"

# DexScreener tokens endpoint for fetching social links
_DEXSCREENER_TOKEN_URL = "https://api.dexscreener.com/tokens/v1"


async def _fetch_dexscreener_socials(
    contract_address: str,
    chain: str,
    session: aiohttp.ClientSession,
) -> list:
    """Fetch social links from DexScreener pair data for a token.

    Returns a list of social link dicts, e.g. [{"type": "twitter", "url": "..."}].
    Returns empty list on failure.
    """
    cached = get_cached(contract_address)
    if cached is not None:
        pairs = cached
    else:
        url = f"{_DEXSCREENER_TOKEN_URL}/{chain}/{contract_address}"
        try:
            async with session.get(
                url,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    return []
                pairs = await resp.json()
                if not pairs or not isinstance(pairs, list):
                    return []
                set_cached(contract_address, pairs)
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return []
    # Use the first pair's info.socials
    for pair in pairs:
        info = pair.get("info", {})
        socials = info.get("socials") or []
        if socials:
            return socials
    return []


async def _fetch_twitter_mentions(
    ticker: str,
    token_name: str,
    session: aiohttp.ClientSession,
    api_key: str = "",
    token_socials: list | None = None,
) -> int:
    """Detect Twitter/X mentions for a token.

    Strategy:
    1. If SOCIALDATA_API_KEY is set, query the SocialData search API for
       recent tweets mentioning the ticker. Returns the count of results.
    2. If no API key, fall back to checking whether DexScreener's social
       links include a Twitter URL. If yes, return a partial credit count
       of 5 (indicates presence but not volume).

    Returns the number of Twitter mentions (or partial credit estimate).
    """
    # Strategy 1: SocialData API (if key available)
    if api_key:
        query = f"{ticker} crypto"
        params = {"query": query}
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Accept": "application/json",
        }
        try:
            async with session.get(
                _SOCIALDATA_SEARCH_URL,
                params=params,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    tweets = data.get("tweets") or data.get("data") or []
                    if isinstance(tweets, list):
                        return len(tweets)
                else:
                    logger.debug(
                        "SocialData API returned non-200",
                        ticker=ticker,
                        status=resp.status,
                    )
        except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
            logger.debug("SocialData API request failed", ticker=ticker, error=str(exc))

    # Strategy 2: Check DexScreener socials for Twitter link (partial credit)
    if token_socials:
        for social in token_socials:
            social_type = (social.get("type") or "").lower()
            social_url = (social.get("url") or "").lower()
            if social_type == "twitter" or "twitter.com" in social_url or "x.com" in social_url:
                return 5  # Partial credit: Twitter exists but unknown volume

    return 0


async def _check_telegram_presence(
    token_socials: list,
    session: aiohttp.ClientSession,
) -> dict:
    """Check if DexScreener returned a Telegram link in token social data.

    Having an active Telegram community is a legitimacy signal for new tokens.
    For simplicity, we only check for the existence of a Telegram link rather
    than attempting to fetch member counts (which requires a bot token).

    Args:
        token_socials: List of social link dicts from DexScreener info.socials.
        session: Shared aiohttp session (unused but kept for future expansion).

    Returns:
        {"has_telegram": bool}
    """
    if not token_socials:
        return {"has_telegram": False}

    for social in token_socials:
        social_type = (social.get("type") or "").lower()
        social_url = (social.get("url") or "").lower()
        if social_type == "telegram" or "t.me/" in social_url or "telegram" in social_url:
            return {"has_telegram": True}

    return {"has_telegram": False}


async def _check_github_presence(
    token_socials: list,
    token_info: dict,
    session: aiohttp.ClientSession,
) -> dict:
    """Check if DexScreener returned a GitHub link in token social/website data.

    Having a GitHub repository signals active development and higher legitimacy.

    Args:
        token_socials: List of social link dicts from DexScreener info.socials.
        token_info: The full DexScreener info dict (may contain websites).
        session: Shared aiohttp session (unused but kept for future expansion).

    Returns:
        {"has_github": bool}
    """
    # Check socials list
    if token_socials:
        for social in token_socials:
            social_type = (social.get("type") or "").lower()
            social_url = (social.get("url") or "").lower()
            if social_type == "github" or "github.com" in social_url:
                return {"has_github": True}

    # Check websites list from DexScreener info
    websites = token_info.get("websites") or []
    for site in websites:
        url = (site.get("url") or site if isinstance(site, str) else site.get("url", "")).lower()
        if "github.com" in url:
            return {"has_github": True}

    return {"has_github": False}


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
        await asyncio.sleep(1.0)  # respect Reddit rate limits

        # 2. LunarCrush (only if API key is configured)
        lunarcrush_data: dict = {}
        if settings.LUNARCRUSH_API_KEY:
            lunarcrush_data = await _fetch_lunarcrush(
                token.ticker, session, settings.LUNARCRUSH_API_KEY,
            )

        # 3. Fetch DexScreener social links for Twitter/Telegram/GitHub checks
        token_socials: list = []
        dex_info: dict = {}
        cached_pairs = get_cached(token.contract_address)
        if cached_pairs is not None:
            pairs = cached_pairs
        else:
            pairs = None
            try:
                dex_url = f"{_DEXSCREENER_TOKEN_URL}/{token.chain}/{token.contract_address}"
                async with session.get(
                    dex_url,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 200:
                        pairs = await resp.json()
                        if pairs and isinstance(pairs, list):
                            set_cached(token.contract_address, pairs)
                        else:
                            pairs = None
            except (aiohttp.ClientError, asyncio.TimeoutError):
                logger.debug("Failed to fetch DexScreener socials", ticker=token.ticker)
        if pairs and isinstance(pairs, list):
            for pair in pairs:
                info = pair.get("info", {})
                socials = info.get("socials") or []
                if socials:
                    token_socials = socials
                    dex_info = info
                    break
                if not dex_info and info:
                    dex_info = info

        # 4. Twitter mention detection
        twitter_mentions = 0
        has_twitter = False
        if settings.TWITTER_SCOUT_ENABLED:
            twitter_mentions = await _fetch_twitter_mentions(
                token.ticker,
                token.token_name,
                session,
                api_key=settings.SOCIALDATA_API_KEY,
                token_socials=token_socials,
            )
            has_twitter = twitter_mentions > 0

        # 5. Telegram presence check
        telegram_data = await _check_telegram_presence(token_socials, session)
        has_telegram = telegram_data["has_telegram"]

        # 6. GitHub presence check
        github_data = await _check_github_presence(token_socials, dex_info, session)
        has_github = github_data["has_github"]

        # Combine mention counts
        total_mentions = reddit_mentions + twitter_mentions
        if lunarcrush_data:
            total_mentions += lunarcrush_data.get("social_volume", 0)

        # Compute composite social score
        social_score = _compute_social_score(reddit_mentions, lunarcrush_data)

        logger.debug(
            "Social enrichment complete",
            ticker=token.ticker,
            reddit_mentions=reddit_mentions,
            twitter_mentions=twitter_mentions,
            lunarcrush_available=bool(lunarcrush_data),
            total_mentions=total_mentions,
            social_score=social_score,
            has_twitter=has_twitter,
            has_telegram=has_telegram,
            has_github=has_github,
        )

        return token.model_copy(
            update={
                "social_mentions_24h": total_mentions,
                "social_score": social_score,
                "has_twitter": has_twitter,
                "has_telegram": has_telegram,
                "has_github": has_github,
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
