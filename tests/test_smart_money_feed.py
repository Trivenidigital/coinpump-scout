"""Tests for smart money feed ingestion source."""
import pytest
from unittest.mock import AsyncMock, MagicMock
from scout.ingestion.smart_money_feed import fetch_smart_money_injections
from scout.config import Settings


def _settings(**overrides):
    defaults = dict(
        TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c", ANTHROPIC_API_KEY="k",
        SMART_MONEY_WALLETS="wallet1,wallet2",
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.mark.asyncio
async def test_no_injections_returns_empty():
    """No unprocessed injections -> empty list."""
    mock_db = AsyncMock()
    mock_db.read_and_mark_injections = AsyncMock(return_value=[])
    mock_session = AsyncMock()
    settings = _settings()
    result = await fetch_smart_money_injections(mock_session, mock_db, settings)
    assert result == []


@pytest.mark.asyncio
async def test_injection_creates_candidate_with_smart_money_count():
    """Injection with 2 wallets buying same token -> smart_money_buys=2."""
    mock_db = AsyncMock()
    mock_db.read_and_mark_injections = AsyncMock(return_value=[
        {"token_mint": "mint1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "wallet_address": "wallet1", "tx_signature": "tx1", "source": "websocket", "detected_at": "2026-03-22T10:00:00"},
        {"token_mint": "mint1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "wallet_address": "wallet2", "tx_signature": "tx2", "source": "websocket", "detected_at": "2026-03-22T10:01:00"},
    ])
    settings = _settings()

    # Mock DexScreener response
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=[{
        "tokenAddress": "mint1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx",
        "info": {"name": "TestToken", "symbol": "TST"},
        "marketCap": 50000,
        "liquidity": {"usd": 20000},
        "volume": {"h24": 100000},
    }])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=cm)

    result = await fetch_smart_money_injections(mock_session, mock_db, settings)
    assert len(result) == 1
    assert result[0].contract_address == "mint1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    assert result[0].smart_money_buys == 2
    assert result[0].token_name == "TestToken"


@pytest.mark.asyncio
async def test_dexscreener_failure_returns_empty():
    """If DexScreener returns error, return empty list."""
    mock_db = AsyncMock()
    mock_db.read_and_mark_injections = AsyncMock(return_value=[
        {"token_mint": "mint1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "wallet_address": "wallet1", "tx_signature": "tx1", "source": "websocket", "detected_at": "2026-03-22T10:00:00"},
    ])
    settings = _settings()

    mock_resp = AsyncMock()
    mock_resp.status = 404
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=cm)

    result = await fetch_smart_money_injections(mock_session, mock_db, settings)
    assert result == []
