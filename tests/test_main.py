"""Tests for main pipeline loop."""

from unittest.mock import AsyncMock, patch, MagicMock

import pytest

from scout.config import Settings
from scout.main import run_cycle


def _settings(**overrides) -> Settings:
    defaults = dict(
        TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c", ANTHROPIC_API_KEY="k",
        HELIUS_API_KEY="", MORALIS_API_KEY="", DISCORD_WEBHOOK_URL="",
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.fixture
def mock_db():
    db = AsyncMock()
    db.initialize = AsyncMock()
    db.close = AsyncMock()
    db.upsert_candidate = AsyncMock()
    db.log_alert = AsyncMock()
    db.get_daily_mirofish_count = AsyncMock(return_value=0)
    db.get_daily_alert_count = AsyncMock(return_value=0)
    db.get_recent_scores = AsyncMock(return_value=[])
    db.log_score = AsyncMock()
    db.get_previous_holder_count = AsyncMock(return_value=None)
    db.log_holder_snapshot = AsyncMock()
    db.log_signal_snapshot = AsyncMock()
    db.commit = AsyncMock()
    db.was_recently_alerted = AsyncMock(return_value=False)
    db.get_last_alert_mcap = AsyncMock(return_value=None)
    db.get_last_alert_time = AsyncMock(return_value=None)
    db.prune_old_data = AsyncMock()
    db.log_volume = AsyncMock()
    db.get_avg_volume = AsyncMock(return_value=None)
    db.log_vol_gate_snapshot = AsyncMock()
    db.get_prev_vol_gate_snapshot = AsyncMock(return_value=None)
    db.get_holder_snapshot_older_than = AsyncMock(return_value=None)
    return db


@pytest.fixture
def mock_session():
    return AsyncMock()


async def test_run_cycle_dry_run(mock_db, mock_session):
    """Dry-run mode: pipeline runs but no alerts are sent."""
    from scout.models import CandidateToken

    settings = _settings()
    token = CandidateToken(
        contract_address="0xTEST1234", chain="solana", token_name="Test",
        ticker="TST", token_age_days=1, market_cap_usd=50000,
        liquidity_usd=10000, volume_24h_usd=80000,
        holder_count=100, holder_growth_1h=25,
    )

    with patch("scout.main.fetch_trending", new_callable=AsyncMock, return_value=[token]), \
         patch("scout.main.fetch_trending_pools", new_callable=AsyncMock, return_value=[]), \
         patch("scout.main.fetch_trending_birdeye", new_callable=AsyncMock, return_value=[]), \
         patch("scout.main.enrich_holders", new_callable=AsyncMock, side_effect=lambda t, s, st: t), \
         patch("scout.main.enrich_onchain_signals", new_callable=AsyncMock, side_effect=lambda t, s, d, st: t), \
         patch("scout.main.enrich_social_sentiment", new_callable=AsyncMock, side_effect=lambda t, s, st: t), \
         patch("scout.main.enrich_news_sentiment", new_callable=AsyncMock, side_effect=lambda t, s, st: t), \
         patch("scout.main.aggregate", return_value=[token]), \
         patch("scout.main.score", return_value=(75, ["vol_liq_ratio"])), \
         patch("scout.quality_gate.QualityGate.evaluate", new_callable=AsyncMock, return_value={"pass": True, "reason": None}), \
         patch("scout.main.evaluate", new_callable=AsyncMock, return_value=(True, 78.0, token)), \
         patch("scout.main.is_safe", new_callable=AsyncMock, return_value=True), \
         patch("scout.main.send_alert", new_callable=AsyncMock) as mock_alert:

        stats = await run_cycle(settings, mock_db, mock_session, dry_run=True)

    mock_alert.assert_not_called()
    assert stats["tokens_scanned"] >= 1


async def test_run_cycle_sends_alert(mock_db, mock_session):
    """Normal mode: alert fires when token passes all gates."""
    from scout.models import CandidateToken

    settings = _settings()
    token = CandidateToken(
        contract_address="0xTEST1234", chain="solana", token_name="Test",
        ticker="TST", token_age_days=1, market_cap_usd=50000,
        liquidity_usd=10000, volume_24h_usd=80000,
        holder_count=100, holder_growth_1h=25,
    )

    with patch("scout.main.fetch_trending", new_callable=AsyncMock, return_value=[token]), \
         patch("scout.main.fetch_trending_pools", new_callable=AsyncMock, return_value=[]), \
         patch("scout.main.fetch_trending_birdeye", new_callable=AsyncMock, return_value=[]), \
         patch("scout.main.enrich_holders", new_callable=AsyncMock, side_effect=lambda t, s, st: t), \
         patch("scout.main.enrich_onchain_signals", new_callable=AsyncMock, side_effect=lambda t, s, d, st: t), \
         patch("scout.main.enrich_social_sentiment", new_callable=AsyncMock, side_effect=lambda t, s, st: t), \
         patch("scout.main.enrich_news_sentiment", new_callable=AsyncMock, side_effect=lambda t, s, st: t), \
         patch("scout.main.aggregate", return_value=[token]), \
         patch("scout.main.score", return_value=(75, ["vol_liq_ratio"])), \
         patch("scout.quality_gate.QualityGate.evaluate", new_callable=AsyncMock, return_value={"pass": True, "reason": None}), \
         patch("scout.main.evaluate", new_callable=AsyncMock, return_value=(True, 78.0, token)), \
         patch("scout.main.is_safe", new_callable=AsyncMock, return_value=True), \
         patch("scout.main.send_alert", new_callable=AsyncMock) as mock_alert:

        stats = await run_cycle(settings, mock_db, mock_session, dry_run=False)

    mock_alert.assert_called_once()
    assert stats["alerts_fired"] == 1


async def test_run_cycle_skips_unsafe_token(mock_db, mock_session):
    """Unsafe token (GoPlus check fails) -> no alert."""
    from scout.models import CandidateToken

    settings = _settings()
    token = CandidateToken(
        contract_address="0xRUG12345", chain="solana", token_name="Rug",
        ticker="RUG", token_age_days=1, market_cap_usd=50000,
        liquidity_usd=10000, volume_24h_usd=80000,
        holder_count=100, holder_growth_1h=25,
    )

    with patch("scout.main.fetch_trending", new_callable=AsyncMock, return_value=[token]), \
         patch("scout.main.fetch_trending_pools", new_callable=AsyncMock, return_value=[]), \
         patch("scout.main.fetch_trending_birdeye", new_callable=AsyncMock, return_value=[]), \
         patch("scout.main.enrich_holders", new_callable=AsyncMock, side_effect=lambda t, s, st: t), \
         patch("scout.main.enrich_onchain_signals", new_callable=AsyncMock, side_effect=lambda t, s, d, st: t), \
         patch("scout.main.enrich_social_sentiment", new_callable=AsyncMock, side_effect=lambda t, s, st: t), \
         patch("scout.main.enrich_news_sentiment", new_callable=AsyncMock, side_effect=lambda t, s, st: t), \
         patch("scout.main.aggregate", return_value=[token]), \
         patch("scout.main.score", return_value=(75, ["vol_liq_ratio"])), \
         patch("scout.quality_gate.QualityGate.evaluate", new_callable=AsyncMock, return_value={"pass": True, "reason": None}), \
         patch("scout.main.evaluate", new_callable=AsyncMock, return_value=(True, 78.0, token)), \
         patch("scout.main.is_safe", new_callable=AsyncMock, return_value=False), \
         patch("scout.main.send_alert", new_callable=AsyncMock) as mock_alert:

        stats = await run_cycle(settings, mock_db, mock_session, dry_run=False)

    mock_alert.assert_not_called()
