"""Tests for conviction gate."""

from unittest.mock import AsyncMock, patch

import pytest

from scout.config import Settings
from scout.gate import evaluate
from scout.models import CandidateToken, MiroFishResult


def _settings(**overrides) -> Settings:
    defaults = dict(
        TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c", ANTHROPIC_API_KEY="k",
        CONVICTION_THRESHOLD=70, QUANT_WEIGHT=0.6, NARRATIVE_WEIGHT=0.4,
        MIN_SCORE=60, QUANT_ONLY_CONVICTION_THRESHOLD=50,
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_token(**overrides) -> CandidateToken:
    defaults = dict(
        contract_address="0xTEST1234", chain="solana", token_name="Test",
        ticker="TST", token_age_days=1.0, market_cap_usd=50000.0,
        liquidity_usd=10000.0, volume_24h_usd=80000.0,
        holder_count=100, holder_growth_1h=25,
        quant_score=75,
    )
    defaults.update(overrides)
    return CandidateToken(**defaults)


@pytest.fixture
def mock_db():
    db = AsyncMock()
    return db


@pytest.fixture
def mock_session():
    return AsyncMock()


async def test_gate_fires_above_threshold(mock_db, mock_session):
    """conviction = 75*0.6 + 80*0.4 = 45+32 = 77 >= 70 -> fire."""
    token = _make_token(quant_score=75)
    settings = _settings()

    with patch("scout.gate.score_narrative_fallback", new_callable=AsyncMock) as mock_fallback, \
         patch("scout.gate.build_seed") as mock_seed:
        mock_seed.return_value = {"prompt": "test"}
        mock_fallback.return_value = MiroFishResult(
            narrative_score=80, virality_class="High", summary="Viral"
        )
        should_alert, conviction, token_out = await evaluate(token, mock_db, mock_session, settings)

    assert should_alert is True
    assert conviction == pytest.approx(77.0)


async def test_gate_rejects_below_threshold(mock_db, mock_session):
    """conviction = 60*0.6 + 20*0.4 = 36+8 = 44 < 70 -> no fire."""
    token = _make_token(quant_score=60)
    settings = _settings()

    with patch("scout.gate.score_narrative_fallback", new_callable=AsyncMock) as mock_fallback, \
         patch("scout.gate.build_seed") as mock_seed:
        mock_seed.return_value = {"prompt": "test"}
        mock_fallback.return_value = MiroFishResult(
            narrative_score=20, virality_class="Low", summary="Weak"
        )
        should_alert, conviction, token_out = await evaluate(token, mock_db, mock_session, settings)

    assert should_alert is False
    assert conviction == pytest.approx(44.0)


async def test_gate_boundary_exactly_70(mock_db, mock_session):
    """Exactly at threshold -> fire."""
    # Need: quant*0.6 + narrative*0.4 = 70
    # quant=100, narrative=25: 60+10=70
    token = _make_token(quant_score=100)
    settings = _settings()

    with patch("scout.gate.score_narrative_fallback", new_callable=AsyncMock) as mock_fallback, \
         patch("scout.gate.build_seed") as mock_seed:
        mock_seed.return_value = {"prompt": "test"}
        mock_fallback.return_value = MiroFishResult(
            narrative_score=25, virality_class="Low", summary="Weak"
        )
        should_alert, conviction, token_out = await evaluate(token, mock_db, mock_session, settings)

    assert should_alert is True
    assert conviction == pytest.approx(70.0)


async def test_gate_below_min_score_skips_narrative(mock_db, mock_session):
    """quant_score < MIN_SCORE -> skip narrative scoring, use quant-only."""
    token = _make_token(quant_score=40)
    settings = _settings(MIN_SCORE=60)

    should_alert, conviction, token_out = await evaluate(token, mock_db, mock_session, settings)

    assert conviction == pytest.approx(40.0 * 0.6)  # quant-only
    assert should_alert is False  # 24 < 50 (QUANT_ONLY_CONVICTION_THRESHOLD)


async def test_gate_quant_only_uses_higher_threshold(mock_db, mock_session):
    """M3: When narrative is unavailable, use QUANT_ONLY_CONVICTION_THRESHOLD."""
    token = _make_token(quant_score=75)
    settings = _settings(QUANT_ONLY_CONVICTION_THRESHOLD=50)

    # Force narrative scoring to fail
    with patch("scout.gate.score_narrative_fallback", new_callable=AsyncMock) as mock_fallback, \
         patch("scout.gate.build_seed") as mock_seed:
        mock_seed.return_value = {"prompt": "test"}
        mock_fallback.side_effect = Exception("API error")
        should_alert, conviction, token_out = await evaluate(token, mock_db, mock_session, settings)

    # conviction = 75*0.6 = 45, which is < 50 threshold
    assert conviction == pytest.approx(45.0)
    assert should_alert is False


async def test_gate_narrative_fallback_success(mock_db, mock_session):
    """Narrative scorer (Claude Haiku) succeeds directly."""
    token = _make_token(quant_score=80)
    settings = _settings()

    with patch("scout.gate.score_narrative_fallback", new_callable=AsyncMock) as mock_fallback, \
         patch("scout.gate.build_seed") as mock_seed:
        mock_seed.return_value = {"prompt": "test"}
        mock_fallback.return_value = MiroFishResult(
            narrative_score=70, virality_class="High", summary="Good narrative"
        )
        should_alert, conviction, token_out = await evaluate(token, mock_db, mock_session, settings)

    # conviction = 80*0.6 + 70*0.4 = 48+28 = 76
    assert conviction == pytest.approx(76.0)
    assert should_alert is True
    mock_fallback.assert_called_once()
