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

# Rate limit CoinGecko API calls (free tier ~10-30 req/min, lazily initialized)
_coingecko_semaphore: asyncio.Semaphore | None = None
_COINGECKO_DELAY = 2.0


def _get_coingecko_semaphore() -> asyncio.Semaphore:
    """Lazily create semaphore in the current event loop."""
    global _coingecko_semaphore
    if _coingecko_semaphore is None:
        _coingecko_semaphore = asyncio.Semaphore(1)
    return _coingecko_semaphore

# CoinGecko chain name mapping
_COINGECKO_CHAIN_MAP = {
    "solana": "solana",
    "ethereum": "ethereum",
    "base": "base",
}


async def _verify_contract(
    coin_id: str,
    contract_address: str,
    chain: str,
    session: aiohttp.ClientSession,
) -> bool:
    """Verify a CoinGecko coin matches our contract address.

    Uses the CoinGecko coins/{id} endpoint to check if the coin's
    platform addresses include our contract_address.
    """
    cg_chain = _COINGECKO_CHAIN_MAP.get(chain)
    if not cg_chain:
        # Unknown chain — can't verify, accept the match
        return True

    url = f"https://api.coingecko.com/api/v3/coins/{coin_id}"
    try:
        async with session.get(
            url,
            params={"localization": "false", "tickers": "false",
                    "market_data": "false", "community_data": "false",
                    "developer_data": "false"},
            timeout=aiohttp.ClientTimeout(total=10),
        ) as resp:
            if resp.status != 200:
                # Can't verify — accept the match
                return True
            data = await resp.json()
            platforms = data.get("platforms", {})
            # Check if our contract address matches any platform
            for platform, addr in platforms.items():
                if addr and addr.lower() == contract_address.lower():
                    return True
            # No matching address found — this is a different token
            return False
    except (aiohttp.ClientError, asyncio.TimeoutError):
        # On error, accept the match (fail open for non-safety check)
        return True


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

    When ``contract_address`` and ``chain`` are provided, the match is
    verified against CoinGecko's coin detail endpoint to confirm the
    contract address matches, preventing scam tokens with the same ticker
    from receiving an undeserved legitimacy boost.

    Rate limits: CoinGecko free tier allows ~10-30 req/min. Caller
    should pace requests accordingly.

    Args:
        ticker: The token ticker/symbol to search for (e.g. "BONK").
        session: Shared aiohttp session.
        contract_address: Token contract address used to verify the match.
        chain: Token chain identifier used for contract verification.

    Returns:
        {"on_coingecko": bool, "cex_listed": bool}
    """
    defaults: dict = {"on_coingecko": False, "cex_listed": False}

    try:
        async with _get_coingecko_semaphore():
            await asyncio.sleep(_COINGECKO_DELAY)
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
                        # If we have contract_address, verify via CoinGecko contract endpoint
                        if contract_address and chain and coin_id:
                            verified = await _verify_contract(
                                coin_id, contract_address, chain, session,
                            )
                            if not verified:
                                logger.debug(
                                    "CoinGecko ticker match but contract mismatch",
                                    ticker=ticker, coin_id=coin_id,
                                    contract_address=contract_address,
                                )
                                continue  # skip this match, try next
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
