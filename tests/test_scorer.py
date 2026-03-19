"""Tests for quantitative scoring engine."""

import pytest

from scout.config import Settings
from scout.models import CandidateToken
from scout.scorer import score, confidence, _token_age_score, RAW_MAX


def _settings(**overrides) -> Settings:
    defaults = dict(
        TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c", LLM_API_KEY="k",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_token(**overrides) -> CandidateToken:
    defaults = dict(
        contract_address="0xtest", chain="solana", token_name="Test",
        ticker="TST", token_age_days=2.0, market_cap_usd=50000.0,
        liquidity_usd=20000.0, volume_24h_usd=160000.0,
        holder_count=100, holder_growth_1h=25,
        social_mentions_24h=0, buys_1h=0, sells_1h=0,
    )
    defaults.update(overrides)
    return CandidateToken(**defaults)


class TestHardDisqualifiers:
    """BL-010: Tokens below liquidity floor get score 0."""

    def test_below_liquidity_floor_returns_zero(self):
        token = _make_token(liquidity_usd=10000)  # < 15K default
        points, signals = score(token, _settings())
        assert points == 0
        assert signals == []

    def test_at_liquidity_floor_passes(self):
        token = _make_token(liquidity_usd=15000)
        points, signals = score(token, _settings())
        assert points > 0

    def test_custom_liquidity_floor(self):
        token = _make_token(liquidity_usd=5000)
        settings = _settings(MIN_LIQUIDITY_USD=4000)
        points, signals = score(token, settings)
        assert points > 0


class TestIndividualSignals:
    """Test each signal fires independently."""

    def test_vol_liq_ratio_fires(self):
        # volume/liquidity = 160000/20000 = 8x (> 5x)
        token = _make_token(volume_24h_usd=160000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, social_mentions_24h=0)
        points, signals = score(token, _settings())
        assert "vol_liq_ratio" in signals
        # With 0.8x penalty (vol_liq without holder_growth): 30 * 100/125 * 0.8 = 19
        assert points == 19

    def test_vol_liq_ratio_does_not_fire(self):
        # volume/liquidity = 40000/20000 = 2x (< 5x)
        token = _make_token(volume_24h_usd=40000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, social_mentions_24h=0)
        points, signals = score(token, _settings())
        assert "vol_liq_ratio" not in signals

    def test_market_cap_range_fires(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=50000, holder_growth_1h=0,
                            token_age_days=30, social_mentions_24h=0)
        points, signals = score(token, _settings())
        assert "market_cap_range" in signals

    def test_market_cap_below_range(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=5000, holder_growth_1h=0,
                            token_age_days=30, social_mentions_24h=0)
        points, signals = score(token, _settings())
        assert "market_cap_range" not in signals

    def test_market_cap_above_range(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=600000, holder_growth_1h=0,
                            token_age_days=30, social_mentions_24h=0)
        points, signals = score(token, _settings())
        assert "market_cap_range" not in signals

    def test_holder_growth_fires(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=25,
                            token_age_days=30, social_mentions_24h=0)
        points, signals = score(token, _settings())
        assert "holder_growth" in signals

    def test_holder_growth_exactly_20(self):
        # > 20, not >= 20
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=20,
                            token_age_days=30, social_mentions_24h=0)
        points, signals = score(token, _settings())
        assert "holder_growth" not in signals

    def test_social_mentions_fires(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, social_mentions_24h=60)
        points, signals = score(token, _settings())
        assert "social_mentions" in signals

    def test_social_mentions_zero(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, social_mentions_24h=0)
        points, signals = score(token, _settings())
        assert "social_mentions" not in signals


class TestBuyPressure:
    """BL-011: Buy pressure ratio signal."""

    def test_buy_pressure_fires_above_65_pct(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, buys_1h=70, sells_1h=30)
        points, signals = score(token, _settings())
        assert "buy_pressure" in signals

    def test_buy_pressure_does_not_fire_at_50_pct(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, buys_1h=50, sells_1h=50)
        points, signals = score(token, _settings())
        assert "buy_pressure" not in signals

    def test_buy_pressure_zero_txns(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, buys_1h=0, sells_1h=0)
        points, signals = score(token, _settings())
        assert "buy_pressure" not in signals


class TestTokenAgeBellCurve:
    """BL-012: Bell curve token age scoring."""

    def test_under_12h_zero(self):
        assert _token_age_score(0.3) == 0

    def test_12h_to_24h_five(self):
        assert _token_age_score(0.7) == 5

    def test_peak_1_to_3_days(self):
        assert _token_age_score(2.0) == 10

    def test_3_to_5_days_five(self):
        assert _token_age_score(4.0) == 5

    def test_over_5_days_zero(self):
        assert _token_age_score(6.0) == 0

    def test_boundary_1_day(self):
        assert _token_age_score(1.0) == 10

    def test_boundary_3_days(self):
        assert _token_age_score(3.0) == 10

    def test_boundary_5_days(self):
        assert _token_age_score(5.0) == 5


class TestScoreVelocity:
    """BL-013: Score velocity bonus."""

    def test_rising_scores_get_bonus(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30)
        points_with, signals_with = score(token, _settings(), previous_scores=[30, 40, 50])
        points_without, signals_without = score(token, _settings(), previous_scores=None)
        assert "score_velocity" in signals_with
        assert "score_velocity" not in signals_without
        assert points_with > points_without

    def test_flat_scores_no_bonus(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30)
        _, signals = score(token, _settings(), previous_scores=[50, 50, 50])
        assert "score_velocity" not in signals

    def test_declining_scores_no_bonus(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30)
        _, signals = score(token, _settings(), previous_scores=[50, 40, 30])
        assert "score_velocity" not in signals

    def test_fewer_than_3_scores_no_bonus(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30)
        _, signals = score(token, _settings(), previous_scores=[30, 40])
        assert "score_velocity" not in signals


class TestCoOccurrenceMultiplier:
    """BL-014: Co-occurrence multiplier."""

    def test_vol_liq_and_holder_growth_bonus(self):
        """Both firing -> 1.2x multiplier."""
        token = _make_token(volume_24h_usd=160000, liquidity_usd=20000,
                            holder_growth_1h=25, market_cap_usd=999999,
                            token_age_days=30)
        points, signals = score(token, _settings())
        assert "vol_liq_ratio" in signals
        assert "holder_growth" in signals
        # Raw: 30 + 25 = 55, normalized: 55*100/125 = 44, then 44*1.2 = 52
        assert points == 52

    def test_vol_liq_without_holder_growth_penalty(self):
        """Vol/liq alone -> 0.8x penalty."""
        token = _make_token(volume_24h_usd=160000, liquidity_usd=20000,
                            holder_growth_1h=0, market_cap_usd=999999,
                            token_age_days=30)
        points, signals = score(token, _settings())
        assert "vol_liq_ratio" in signals
        assert "holder_growth" not in signals
        # Raw: 30, normalized: 30*100/125 = 24, then 24*0.8 = 19
        assert points == 19

    def test_holder_growth_without_vol_liq_no_modifier(self):
        """Holder growth alone -> no multiplier adjustment."""
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            holder_growth_1h=25, market_cap_usd=999999,
                            token_age_days=30)
        points, signals = score(token, _settings())
        assert "holder_growth" in signals
        assert "vol_liq_ratio" not in signals
        # Raw: 25, normalized: 25*100/125 = 20, no multiplier
        assert points == 20


class TestNormalization:
    """BL-016: Normalization to 100 scale."""

    def test_max_raw_normalizes_to_100(self):
        """All signals firing should cap at 100."""
        token = _make_token(
            volume_24h_usd=160000, liquidity_usd=20000,
            market_cap_usd=50000, holder_growth_1h=25,
            token_age_days=2.0, social_mentions_24h=60,
            buys_1h=70, sells_1h=30,
        )
        points, signals = score(token, _settings(), previous_scores=[30, 40, 50])
        assert points <= 100

    def test_score_never_negative(self):
        token = _make_token(
            volume_24h_usd=0, liquidity_usd=20000,
            market_cap_usd=999999, holder_growth_1h=0,
            token_age_days=30, social_mentions_24h=0,
        )
        points, _ = score(token, _settings())
        assert points >= 0

    def test_returns_tuple(self):
        token = _make_token()
        result = score(token, _settings())
        assert isinstance(result, tuple)
        assert isinstance(result[0], int)
        assert isinstance(result[1], list)


class TestConfidence:
    """BL-015: Signal confidence levels."""

    def test_high_confidence(self):
        assert confidence(["a", "b", "c"]) == "HIGH"

    def test_medium_confidence(self):
        assert confidence(["a", "b"]) == "MEDIUM"

    def test_low_confidence_one(self):
        assert confidence(["a"]) == "LOW"

    def test_low_confidence_zero(self):
        assert confidence([]) == "LOW"


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_zero_liquidity_disqualified(self):
        """Zero liquidity -> below floor -> score 0."""
        token = _make_token(
            volume_24h_usd=80000, liquidity_usd=0,
            market_cap_usd=999999, holder_growth_1h=0,
            token_age_days=30, social_mentions_24h=0,
        )
        points, signals = score(token, _settings())
        assert points == 0

    def test_zero_volume(self):
        token = _make_token(
            volume_24h_usd=0, liquidity_usd=20000,
            market_cap_usd=999999, holder_growth_1h=0,
            token_age_days=30, social_mentions_24h=0,
        )
        points, signals = score(token, _settings())
        assert "vol_liq_ratio" not in signals

    def test_custom_thresholds(self):
        """Scoring uses settings for thresholds, not hardcoded values."""
        settings = _settings(MIN_VOL_LIQ_RATIO=10.0)
        token = _make_token(
            volume_24h_usd=160000, liquidity_usd=20000,  # ratio 8x < 10x
            market_cap_usd=50000, holder_growth_1h=25,
            token_age_days=2, social_mentions_24h=0,
        )
        points, signals = score(token, settings)
        assert "vol_liq_ratio" not in signals  # 8x < 10x threshold
        assert "token_age" in signals
