"""Tests for CEX listing detection."""
import pytest
from aioresponses import aioresponses
import aiohttp
from scout.ingestion.cex_monitor import check_cex_listing


@pytest.fixture
def mock_aiohttp():
    with aioresponses() as m:
        yield m


@pytest.mark.asyncio
async def test_ticker_match_returns_true(mock_aiohttp):
    """Matching symbol in CoinGecko search results -> on_coingecko=True."""
    mock_aiohttp.get(
        "https://api.coingecko.com/api/v3/search?query=TST",
        payload={"coins": [{"symbol": "TST", "id": "test-token"}]},
    )
    async with aiohttp.ClientSession() as session:
        result = await check_cex_listing("TST", session)
    assert result["on_coingecko"] is True


@pytest.mark.asyncio
async def test_no_match_returns_false(mock_aiohttp):
    """No matching symbol in CoinGecko results -> on_coingecko=False."""
    mock_aiohttp.get(
        "https://api.coingecko.com/api/v3/search?query=NONEXISTENT",
        payload={"coins": []},
    )
    async with aiohttp.ClientSession() as session:
        result = await check_cex_listing("NONEXISTENT", session)
    assert result["on_coingecko"] is False


@pytest.mark.asyncio
async def test_non_200_returns_defaults(mock_aiohttp):
    """Non-200 from CoinGecko -> safe defaults returned."""
    mock_aiohttp.get(
        "https://api.coingecko.com/api/v3/search?query=TST",
        status=500,
    )
    async with aiohttp.ClientSession() as session:
        result = await check_cex_listing("TST", session)
    assert result["on_coingecko"] is False


@pytest.mark.asyncio
async def test_rate_limited_returns_defaults(mock_aiohttp):
    """429 from CoinGecko -> safe defaults returned."""
    mock_aiohttp.get(
        "https://api.coingecko.com/api/v3/search?query=TST",
        status=429,
    )
    async with aiohttp.ClientSession() as session:
        result = await check_cex_listing("TST", session)
    assert result["on_coingecko"] is False
