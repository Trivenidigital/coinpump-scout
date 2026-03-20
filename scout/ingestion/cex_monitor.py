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
    contract_address: str = "",
    chain: str = "",
) -> dict:
    """Check if a token is listed on CoinGecko (proxy for CEX listing).

    Queries the CoinGecko public search API. If the ticker appears in
    results with a matching symbol, the token is considered to be on
    CoinGecko and likely has CEX listings.

    Note: This is a ticker-only match. A scam token with the same ticker as
    a legitimate project will match. The ``contract_address`` and ``chain``
    parameters are accepted for future on-chain verification and for logging
    to aid debugging of false positives.

    Rate limits: CoinGecko free tier allows ~10-30 req/min. Caller
    should pace requests accordingly.

    Args:
        ticker: The token ticker/symbol to search for (e.g. "BONK").
        session: Shared aiohttp session.
        contract_address: Token contract address (used for logging only).
        chain: Token chain identifier (used for logging only).

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
                    coin_id = coin.get("id", "")
                    if contract_address and coin_id:
                        logger.debug(
                            "CoinGecko ticker match (verify with contract address)",
                            ticker=ticker,
                            coin_id=coin_id,
                            contract_address=contract_address,
                        )
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
