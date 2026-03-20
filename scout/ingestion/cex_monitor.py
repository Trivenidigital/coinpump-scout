"""CEX listing detection via CoinGecko public API.

Checks whether a token ticker is listed on CoinGecko, which strongly
correlates with centralized exchange (CEX) listings. Being on CoinGecko
is a legitimacy and liquidity signal.
"""

import asyncio

import aiohttp
import structlog

logger = structlog.get_logger()

_COINGECKO_SEARCH_URL = "https://api.coingecko.com/api/v3/search"


async def check_cex_listing(
    ticker: str,
    session: aiohttp.ClientSession,
) -> dict:
    """Check if a token is listed on CoinGecko (proxy for CEX listing).

    Queries the CoinGecko public search API. If the ticker appears in
    results with a matching symbol, the token is considered to be on
    CoinGecko and likely has CEX listings.

    Rate limits: CoinGecko free tier allows ~10-30 req/min. Caller
    should pace requests accordingly.

    Args:
        ticker: The token ticker/symbol to search for (e.g. "BONK").
        session: Shared aiohttp session.

    Returns:
        {"on_coingecko": bool, "cex_listed": bool}
    """
    defaults: dict = {"on_coingecko": False, "cex_listed": False}

    try:
        params = {"query": ticker}
        async with session.get(
            _COINGECKO_SEARCH_URL,
            params=params,
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status == 429:
                logger.debug("CoinGecko rate limited, skipping", ticker=ticker)
                return defaults
            if resp.status != 200:
                logger.debug(
                    "CoinGecko search returned non-200",
                    ticker=ticker,
                    status=resp.status,
                )
                return defaults

            data = await resp.json()
            coins = data.get("coins", [])

            # Check if any result has a matching symbol (case-insensitive)
            ticker_upper = ticker.upper()
            for coin in coins:
                symbol = (coin.get("symbol") or "").upper()
                if symbol == ticker_upper:
                    # Found on CoinGecko -- likely has CEX listings
                    return {"on_coingecko": True, "cex_listed": True}

            return defaults

    except (aiohttp.ClientError, asyncio.TimeoutError) as exc:
        logger.debug("CoinGecko search failed", ticker=ticker, error=str(exc))
        return defaults
    except Exception as exc:
        logger.warning(
            "CoinGecko check unexpected error",
            ticker=ticker,
            error=str(exc),
        )
        return defaults
