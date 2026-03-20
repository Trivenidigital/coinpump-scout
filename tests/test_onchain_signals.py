"""Tests for on-chain signal enrichment."""
import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from scout.ingestion.onchain_signals import enrich_onchain_signals, check_volume_spike
from scout.models import CandidateToken
from scout.config import Settings


def _settings(**overrides):
    defaults = dict(
        TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c", ANTHROPIC_API_KEY="k",
        HELIUS_API_KEY="", MORALIS_API_KEY="", ONCHAIN_SIGNALS_ENABLED=True,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_token(**overrides):
    defaults = dict(
        contract_address="0xTEST1234", chain="solana", token_name="Test",
        ticker="TST", liquidity_usd=20000.0, volume_24h_usd=100000.0,
    )
    defaults.update(overrides)
    return CandidateToken(**defaults)


def test_enrich_disabled_returns_unchanged():
    """When ONCHAIN_SIGNALS_ENABLED=False, token is returned unchanged."""
    import asyncio
    settings = _settings(ONCHAIN_SIGNALS_ENABLED=False)
    token = _make_token()
    mock_session = AsyncMock()
    mock_db = AsyncMock()
    result = asyncio.get_event_loop().run_until_complete(
        enrich_onchain_signals(token, mock_session, mock_db, settings)
    )
    assert result.smart_money_buys == 0


@pytest.mark.asyncio
async def test_check_volume_spike_no_history():
    """Volume spike returns defaults when no history exists."""
    mock_db = AsyncMock()
    mock_db.get_avg_volume = AsyncMock(return_value=None)
    mock_db.log_volume = AsyncMock()
    settings = _settings()
    result = await check_volume_spike("0xTEST1234", 100000.0, mock_db, settings)
    assert result["volume_spike"] is False


@pytest.mark.asyncio
async def test_check_volume_spike_detects_spike():
    """Volume spike detected when current > 3x average."""
    mock_db = AsyncMock()
    mock_db.get_avg_volume = AsyncMock(return_value=10000.0)
    mock_db.log_volume = AsyncMock()
    settings = _settings()
    result = await check_volume_spike("0xTEST1234", 50000.0, mock_db, settings)
    assert result["volume_spike"] is True
    assert result["volume_ratio"] == 5.0


@pytest.mark.asyncio
async def test_check_volume_spike_no_spike_below_3x():
    """Volume spike NOT detected when current <= 3x average."""
    mock_db = AsyncMock()
    mock_db.get_avg_volume = AsyncMock(return_value=10000.0)
    mock_db.log_volume = AsyncMock()
    settings = _settings()
    result = await check_volume_spike("0xTEST1234", 25000.0, mock_db, settings)
    assert result["volume_spike"] is False
    assert result["volume_ratio"] == 2.5
