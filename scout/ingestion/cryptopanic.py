"""CryptoPanic news sentiment — detect tokens trending in crypto news.

CryptoPanic aggregates news from 100+ crypto media sources and provides
sentiment analysis (bullish/bearish/neutral) via their API.

API docs: https://cryptopanic.com/developers/api/
Free key: https://cryptopanic.com/developers/api/ (register required)

When a token's ticker appears in recent bullish news, it's a strong
signal that the narrative is spreading — exactly what we want to catch
before the price moves.
"""

import asyncio

import aiohttp
import structlog

from scout.config import Settings
from scout.models import CandidateToken

logger = structlog.get_logger()

_CRYPTOPANIC_API = "https://cryptopanic.com/api/developer/v1/posts/"


async def check_cryptopanic_sentiment(
    ticker: str,
    session: aiohttp.ClientSession,
    settings: Settings,
) -> dict:
    """Check CryptoPanic for recent news mentioning a token.

    Returns:
        {
            "news_mentions": int,       # total news articles mentioning the token
            "bullish_mentions": int,     # articles with bullish sentiment
            "bearish_mentions": int,     # articles with bearish sentiment
            "news_sentiment": float,     # -1.0 (bearish) to +1.0 (bullish)
            "has_news": bool,            # whether any news was found
        }
    """
    defaults = {
        "news_mentions": 0,
        "bullish_mentions": 0,
        "bearish_mentions": 0,
        "news_sentiment": 0.0,
        "has_news": False,
    }

    if not settings.CRYPTOPANIC_API_KEY:
        return defaults

    params = {
        "auth_token": settings.CRYPTOPANIC_API_KEY,
        "currencies": ticker.upper(),
        "kind": "news",
        "filter": "hot",
        "public": "true",
    }

    try:
        async with session.get(
            _CRYPTOPANIC_API,
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 429:
                logger.debug("CryptoPanic rate limited", ticker=ticker)
                return defaults
            if resp.status != 200:
                logger.debug("CryptoPanic non-200", ticker=ticker, status=resp.status)
                return defaults

            data = await resp.json()
            results = data.get("results", [])

            if not results:
                return defaults

            bullish = 0
            bearish = 0
            neutral = 0

            for post in results:
                votes = post.get("votes", {})
                pos = votes.get("positive", 0) or 0
                neg = votes.get("negative", 0) or 0

                # Also check the "kind" field for sentiment tags
                sentiment = post.get("sentiment")
                if sentiment == "positive" or pos > neg:
                    bullish += 1
                elif sentiment == "negative" or neg > pos:
                    bearish += 1
                else:
                    neutral += 1

            total = len(results)
            # Sentiment score: -1 (all bearish) to +1 (all bullish)
            if total > 0:
                sentiment_score = (bullish - bearish) / total
            else:
                sentiment_score = 0.0

            return {
                "news_mentions": total,
                "bullish_mentions": bullish,
                "bearish_mentions": bearish,
                "news_sentiment": round(sentiment_score, 2),
                "has_news": total > 0,
            }

    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.debug("CryptoPanic request failed", ticker=ticker, error=str(exc))
        return defaults


async def enrich_news_sentiment(
    token: CandidateToken,
    session: aiohttp.ClientSession,
    settings: Settings,
) -> CandidateToken:
    """Enrich a token with CryptoPanic news sentiment data.

    Skips silently if CRYPTOPANIC_API_KEY is not set.
    """
    if not settings.CRYPTOPANIC_API_KEY:
        return token

    try:
        result = await check_cryptopanic_sentiment(token.ticker, session, settings)

        updates = {}
        if result["has_news"]:
            updates["news_mentions"] = result["news_mentions"]
            updates["news_sentiment"] = result["news_sentiment"]
            updates["has_news"] = True

            logger.debug(
                "CryptoPanic enrichment complete",
                ticker=token.ticker,
                news_mentions=result["news_mentions"],
                bullish=result["bullish_mentions"],
                bearish=result["bearish_mentions"],
                sentiment=result["news_sentiment"],
            )

        if updates:
            return token.model_copy(update=updates)
        return token

    except Exception as exc:
        logger.warning("CryptoPanic enrichment failed", ticker=token.ticker, error=str(exc))
        return token
