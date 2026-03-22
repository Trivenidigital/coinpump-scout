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


@pytest.mark.asyncio
async def test_enrich_disabled_returns_unchanged():
    """When ONCHAIN_SIGNALS_ENABLED=False, token is returned unchanged."""
    settings = _settings(ONCHAIN_SIGNALS_ENABLED=False)
    token = _make_token()
    mock_session = AsyncMock()
    mock_db = AsyncMock()
    result = await enrich_onchain_signals(token, mock_session, mock_db, settings)
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


def test_smart_money_wallets_loaded_from_config():
    """SMART_MONEY_WALLETS should be loaded from settings, not empty set."""
    from scout.ingestion.onchain_signals import _get_smart_wallets
    settings = _settings(SMART_MONEY_WALLETS="wallet1,wallet2,wallet3")
    wallets = _get_smart_wallets(settings)
    assert wallets == {"wallet1", "wallet2", "wallet3"}


@pytest.mark.asyncio
async def test_enrich_preserves_higher_smart_money_buys():
    """C1: enrich_onchain_signals should preserve the higher smart_money_buys value."""
    settings = _settings(HELIUS_API_KEY="test_key", ONCHAIN_SIGNALS_ENABLED=True)
    # Token injected with 5 smart money buys from smart_money_feed
    token = _make_token(smart_money_buys=5)
    mock_session = AsyncMock()
    mock_db = AsyncMock()
    mock_db.get_avg_volume = AsyncMock(return_value=None)
    mock_db.log_volume = AsyncMock()

    # Helius returns only 2 smart money buys
    with patch("scout.ingestion.onchain_signals.check_smart_money", new_callable=AsyncMock) as mock_sm, \
         patch("scout.ingestion.onchain_signals.check_liquidity_lock", new_callable=AsyncMock) as mock_ll, \
         patch("scout.ingestion.onchain_signals.check_holder_distribution", new_callable=AsyncMock) as mock_hd, \
         patch("scout.ingestion.onchain_signals.check_multi_dex", new_callable=AsyncMock) as mock_md, \
         patch("scout.ingestion.onchain_signals.check_cex_listing", new_callable=AsyncMock) as mock_cex:
        mock_sm.return_value = {"smart_money_buys": 2, "whale_buys": 0, "unique_buyers_recent": 10, "whale_txns_1h": 0}
        mock_ll.return_value = {"liquidity_locked": False, "lock_source": None}
        mock_hd.return_value = {"holder_gini_healthy": False, "top5_concentration": 0.0}
        mock_md.return_value = {"multi_dex": False, "dex_count": 0}
        mock_cex.return_value = {"on_coingecko": False}

        result = await enrich_onchain_signals(token, mock_session, mock_db, settings)
        # Should keep 5 (from injection), not overwrite with 2 (from Helius)
        assert result.smart_money_buys == 5


@pytest.mark.asyncio
async def test_enrich_uses_helius_when_higher():
    """enrich_onchain_signals should use Helius value when it's higher than injection."""
    settings = _settings(HELIUS_API_KEY="test_key", ONCHAIN_SIGNALS_ENABLED=True)
    token = _make_token(smart_money_buys=1)
    mock_session = AsyncMock()
    mock_db = AsyncMock()
    mock_db.get_avg_volume = AsyncMock(return_value=None)
    mock_db.log_volume = AsyncMock()

    with patch("scout.ingestion.onchain_signals.check_smart_money", new_callable=AsyncMock) as mock_sm, \
         patch("scout.ingestion.onchain_signals.check_liquidity_lock", new_callable=AsyncMock) as mock_ll, \
         patch("scout.ingestion.onchain_signals.check_holder_distribution", new_callable=AsyncMock) as mock_hd, \
         patch("scout.ingestion.onchain_signals.check_multi_dex", new_callable=AsyncMock) as mock_md, \
         patch("scout.ingestion.onchain_signals.check_cex_listing", new_callable=AsyncMock) as mock_cex:
        mock_sm.return_value = {"smart_money_buys": 4, "whale_buys": 1, "unique_buyers_recent": 10, "whale_txns_1h": 0}
        mock_ll.return_value = {"liquidity_locked": False, "lock_source": None}
        mock_hd.return_value = {"holder_gini_healthy": False, "top5_concentration": 0.0}
        mock_md.return_value = {"multi_dex": False, "dex_count": 0}
        mock_cex.return_value = {"on_coingecko": False}

        result = await enrich_onchain_signals(token, mock_session, mock_db, settings)
        # Should use 4 from Helius since it's higher than 1 from injection
        assert result.smart_money_buys == 4


def test_smart_money_wallets_empty_when_not_configured():
    """SMART_MONEY_WALLETS returns empty set when not configured."""
    from scout.ingestion.onchain_signals import _get_smart_wallets
    settings = _settings(SMART_MONEY_WALLETS="")
    wallets = _get_smart_wallets(settings)
    assert wallets == set()
