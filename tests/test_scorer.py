"""Tests for quantitative scoring engine."""

import pytest

from scout.config import Settings
from scout.models import CandidateToken
from scout.scorer import score, confidence, _token_age_score, _market_cap_tier_score, RAW_MAX


def _settings(**overrides) -> Settings:
    defaults = dict(
        TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c", ANTHROPIC_API_KEY="k",
        HELIUS_API_KEY="", MORALIS_API_KEY="",
    )
    defaults.update(overrides)
    return Settings(**defaults)


def _make_token(**overrides) -> CandidateToken:
    defaults = dict(
        contract_address="0xTEST1234", chain="solana", token_name="Test",
        ticker="TST", token_age_days=2.0, market_cap_usd=50000.0,
        liquidity_usd=20000.0, volume_24h_usd=160000.0,
        holder_count=100, holder_growth_1h=25,
        social_mentions_24h=0, buys_1h=0, sells_1h=0,
        unique_buyers_1h=0, top3_wallet_concentration=0.0,
        deployer_supply_pct=0.0, small_txn_ratio=0.0,
    )
    defaults.update(overrides)
    return CandidateToken(**defaults)


class TestHardDisqualifiers:
    """BL-010, BL-022, BL-023: Hard disqualifiers return score 0."""

    def test_below_liquidity_floor_returns_zero(self):
        token = _make_token(liquidity_usd=10000)
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

    def test_wash_trade_disqualified(self):
        """BL-022: Top-3 wallet concentration > 40% -> score 0."""
        token = _make_token(top3_wallet_concentration=0.45)
        points, signals = score(token, _settings())
        assert points == 0
        assert signals == []

    def test_wash_trade_at_boundary_passes(self):
        token = _make_token(top3_wallet_concentration=0.40)
        points, signals = score(token, _settings())
        assert points > 0

    def test_deployer_concentration_disqualified(self):
        """BL-023: Deployer holds > 20% supply -> score 0."""
        token = _make_token(deployer_supply_pct=0.25)
        points, signals = score(token, _settings())
        assert points == 0
        assert signals == []

    def test_deployer_at_boundary_passes(self):
        token = _make_token(deployer_supply_pct=0.20)
        points, signals = score(token, _settings())
        assert points > 0


class TestIndividualSignals:
    """Test each signal fires independently."""

    def test_vol_liq_ratio_fires(self):
        token = _make_token(volume_24h_usd=160000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, social_mentions_24h=0, chain="ethereum")
        points, signals = score(token, _settings())
        assert "vol_liq_ratio" in signals

    def test_vol_liq_ratio_does_not_fire(self):
        token = _make_token(volume_24h_usd=40000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, social_mentions_24h=0, chain="ethereum")
        points, signals = score(token, _settings())
        assert "vol_liq_ratio" not in signals

    def test_holder_growth_fires(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=25,
                            token_age_days=30, social_mentions_24h=0, chain="ethereum")
        points, signals = score(token, _settings())
        assert "holder_growth" in signals

    def test_holder_growth_exactly_20(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=20,
                            token_age_days=30, social_mentions_24h=0, chain="ethereum")
        points, signals = score(token, _settings())
        assert "holder_growth" not in signals

    def test_social_mentions_fires(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, social_mentions_24h=60, chain="ethereum")
        points, signals = score(token, _settings())
        assert "social_mentions" in signals

    def test_social_mentions_zero(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, social_mentions_24h=0, chain="ethereum")
        points, signals = score(token, _settings())
        assert "social_mentions" not in signals


class TestBuyPressure:
    """BL-011: Buy pressure ratio signal."""

    def test_buy_pressure_fires_above_65_pct(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, buys_1h=70, sells_1h=30, chain="ethereum")
        points, signals = score(token, _settings())
        assert "buy_pressure" in signals

    def test_buy_pressure_does_not_fire_at_50_pct(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, buys_1h=50, sells_1h=50, chain="ethereum")
        points, signals = score(token, _settings())
        assert "buy_pressure" not in signals

    def test_buy_pressure_zero_txns(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, buys_1h=0, sells_1h=0, chain="ethereum")
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


class TestMarketCapTier:
    """BL-031: Graduated market cap scoring."""

    def test_peak_zone_10k_100k(self):
        assert _market_cap_tier_score(50000, _settings()) == 8

    def test_mid_zone_100k_250k(self):
        assert _market_cap_tier_score(150000, _settings()) == 5

    def test_late_zone_250k_500k(self):
        assert _market_cap_tier_score(300000, _settings()) == 2

    def test_below_min(self):
        assert _market_cap_tier_score(5000, _settings()) == 0

    def test_above_max(self):
        assert _market_cap_tier_score(600000, _settings()) == 0

    def test_boundary_10k(self):
        assert _market_cap_tier_score(10000, _settings()) == 8

    def test_boundary_100k(self):
        assert _market_cap_tier_score(100000, _settings()) == 8

    def test_boundary_250k(self):
        assert _market_cap_tier_score(250000, _settings()) == 5

    def test_boundary_500k(self):
        assert _market_cap_tier_score(500000, _settings()) == 2


class TestScoreVelocity:
    """BL-013: Score velocity bonus."""

    def test_rising_scores_get_bonus(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, chain="ethereum")
        points_with, signals_with = score(token, _settings(), previous_scores=[30, 40, 50])
        points_without, signals_without = score(token, _settings(), previous_scores=None)
        assert "score_velocity" in signals_with
        assert "score_velocity" not in signals_without
        assert points_with > points_without

    def test_flat_scores_no_bonus(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, chain="ethereum")
        _, signals = score(token, _settings(), previous_scores=[50, 50, 50])
        assert "score_velocity" not in signals

    def test_fewer_than_3_scores_no_bonus(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, chain="ethereum")
        _, signals = score(token, _settings(), previous_scores=[30, 40])
        assert "score_velocity" not in signals


class TestUniqueBuyers:
    """BL-021: Unique buyer wallet count signal."""

    def test_high_buyer_ratio_fires(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, buys_1h=40, sells_1h=10,
                            unique_buyers_1h=30, chain="ethereum")
        _, signals = score(token, _settings())
        assert "unique_buyers" in signals

    def test_low_buyer_ratio_does_not_fire(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, buys_1h=40, sells_1h=60,
                            unique_buyers_1h=10, chain="ethereum")
        _, signals = score(token, _settings())
        assert "unique_buyers" not in signals

    def test_zero_buyers_does_not_fire(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, buys_1h=0, sells_1h=0,
                            unique_buyers_1h=0, chain="ethereum")
        _, signals = score(token, _settings())
        assert "unique_buyers" not in signals


class TestSolanaBonus:
    """BL-030: Solana chain bonus."""

    def test_solana_gets_bonus(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, chain="solana")
        _, signals = score(token, _settings())
        assert "solana_bonus" in signals

    def test_ethereum_no_bonus(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, chain="ethereum")
        _, signals = score(token, _settings())
        assert "solana_bonus" not in signals


class TestSmallTxnRatio:
    """BL-024: Small transaction ratio signal."""

    def test_high_small_txn_ratio_fires(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, small_txn_ratio=0.75, chain="ethereum")
        _, signals = score(token, _settings())
        assert "small_txn_ratio" in signals

    def test_low_small_txn_ratio_does_not_fire(self):
        token = _make_token(volume_24h_usd=1000, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, small_txn_ratio=0.40, chain="ethereum")
        _, signals = score(token, _settings())
        assert "small_txn_ratio" not in signals


class TestCoOccurrenceMultiplier:
    """BL-014: Co-occurrence multiplier."""

    def test_vol_liq_and_holder_growth_bonus(self):
        """Both firing -> 1.2x multiplier."""
        token = _make_token(volume_24h_usd=160000, liquidity_usd=20000,
                            holder_growth_1h=25, market_cap_usd=999999,
                            token_age_days=30, chain="ethereum")
        points, signals = score(token, _settings())
        assert "vol_liq_ratio" in signals
        assert "holder_growth" in signals

        # Without multiplier: (30+25)*100/138 = 39
        # With 1.2x: 39*1.2 = 47
        raw = int((30 + 25) * 100 / RAW_MAX)
        expected = min(100, int(raw * 1.2))
        assert points == expected

    def test_vol_liq_without_holder_growth_penalty(self):
        """Vol/liq alone -> 0.8x penalty."""
        token = _make_token(volume_24h_usd=160000, liquidity_usd=20000,
                            holder_growth_1h=0, market_cap_usd=999999,
                            token_age_days=30, chain="ethereum")
        points, signals = score(token, _settings())
        assert "vol_liq_ratio" in signals
        assert "holder_growth" not in signals

        raw = int(30 * 100 / RAW_MAX)
        expected = int(raw * 0.8)
        assert points == expected


class TestNormalization:
    """BL-016: Normalization to 100 scale."""

    def test_score_capped_at_100(self):
        """All signals firing should cap at 100."""
        token = _make_token(
            volume_24h_usd=160000, liquidity_usd=20000,
            market_cap_usd=50000, holder_growth_1h=25,
            token_age_days=2.0, social_mentions_24h=60,
            buys_1h=70, sells_1h=30, unique_buyers_1h=40,
            small_txn_ratio=0.75, chain="solana",
        )
        points, signals = score(token, _settings(), previous_scores=[30, 40, 50])
        assert points <= 100

    def test_score_never_negative(self):
        token = _make_token(
            volume_24h_usd=0, liquidity_usd=20000,
            market_cap_usd=999999, holder_growth_1h=0,
            token_age_days=30, social_mentions_24h=0, chain="ethereum",
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
        token = _make_token(volume_24h_usd=80000, liquidity_usd=0)
        points, signals = score(token, _settings())
        assert points == 0

    def test_zero_volume(self):
        token = _make_token(volume_24h_usd=0, liquidity_usd=20000,
                            market_cap_usd=999999, holder_growth_1h=0,
                            token_age_days=30, chain="ethereum")
        points, signals = score(token, _settings())
        assert "vol_liq_ratio" not in signals

    def test_custom_thresholds(self):
        settings = _settings(MIN_VOL_LIQ_RATIO=10.0)
        token = _make_token(volume_24h_usd=160000, liquidity_usd=20000,
                            market_cap_usd=50000, holder_growth_1h=25,
                            token_age_days=2)
        points, signals = score(token, settings)
        assert "vol_liq_ratio" not in signals  # 8x < 10x threshold
        assert "token_age" in signals


class TestNewSignals:
    """CR-022: One test per untested scorer signal."""

    def test_smart_money_buys_fires(self):
        token = _make_token(smart_money_buys=1, liquidity_usd=20000)
        _, signals = score(token, _settings())
        assert "smart_money_buys" in signals

    def test_whale_buys_fires(self):
        token = _make_token(whale_buys=3, liquidity_usd=20000)
        _, signals = score(token, _settings())
        assert "whale_buys" in signals

    def test_liquidity_locked_fires(self):
        token = _make_token(liquidity_locked=True, liquidity_usd=20000)
        _, signals = score(token, _settings())
        assert "liquidity_locked" in signals

    def test_volume_spike_5x_fires(self):
        token = _make_token(volume_spike=True, volume_spike_ratio=6.0, liquidity_usd=20000)
        _, signals = score(token, _settings())
        assert "volume_spike_5x" in signals
        assert "volume_spike_3x" not in signals

    def test_volume_spike_3x_fires(self):
        token = _make_token(volume_spike=True, volume_spike_ratio=4.0, liquidity_usd=20000)
        _, signals = score(token, _settings())
        assert "volume_spike_3x" in signals
        assert "volume_spike_5x" not in signals

    def test_holder_gini_healthy_fires(self):
        token = _make_token(holder_gini_healthy=True, liquidity_usd=20000)
        _, signals = score(token, _settings())
        assert "holder_gini_healthy" in signals

    def test_whale_txns_1h_fires(self):
        token = _make_token(whale_txns_1h=3, liquidity_usd=20000)
        _, signals = score(token, _settings())
        assert "whale_txns_1h" in signals

    def test_has_twitter_fires(self):
        token = _make_token(has_twitter=True, liquidity_usd=20000)
        _, signals = score(token, _settings())
        assert "has_twitter" in signals

    def test_has_telegram_fires(self):
        token = _make_token(has_telegram=True, liquidity_usd=20000)
        _, signals = score(token, _settings())
        assert "has_telegram" in signals

    def test_has_github_fires(self):
        token = _make_token(has_github=True, liquidity_usd=20000)
        _, signals = score(token, _settings())
        assert "has_github" in signals

    def test_on_coingecko_fires(self):
        token = _make_token(on_coingecko=True, liquidity_usd=20000)
        _, signals = score(token, _settings())
        assert "on_coingecko" in signals

    def test_multi_dex_fires(self):
        token = _make_token(multi_dex=True, dex_count=2, liquidity_usd=20000)
        _, signals = score(token, _settings())
        assert "multi_dex" in signals

    def test_has_news_fires(self):
        token = _make_token(has_news=True, news_mentions=1, liquidity_usd=20000)
        _, signals = score(token, _settings())
        assert "has_news" in signals

    def test_bullish_news_fires(self):
        token = _make_token(news_sentiment=0.5, liquidity_usd=20000)
        _, signals = score(token, _settings())
        assert "bullish_news" in signals

    def test_small_txn_ratio_fires(self):
        token = _make_token(small_txn_ratio=0.7, liquidity_usd=20000)
        _, signals = score(token, _settings())
        assert "small_txn_ratio" in signals


class TestSmartMoneyGraduatedBoost:
    """Task 6: Graduated smart money scorer boost — +20/wallet, capped at SMART_MONEY_BOOST_CAP."""

    def test_smart_money_graduated_boost_1_wallet(self, token_factory, settings_factory):
        """1 smart wallet buy = +20 points contribution."""
        token = token_factory(smart_money_buys=1, liquidity_usd=20000)
        settings = settings_factory(SMART_MONEY_BOOST_CAP=80)
        points, signals = score(token, settings)
        assert "smart_money_buys" in signals

    def test_smart_money_graduated_boost_3_wallets(self, token_factory, settings_factory):
        """3 smart wallet buys should score higher than 1."""
        token_1 = token_factory(smart_money_buys=1, liquidity_usd=20000)
        token_3 = token_factory(smart_money_buys=3, liquidity_usd=20000)
        settings = settings_factory(SMART_MONEY_BOOST_CAP=80)
        points_1, _ = score(token_1, settings)
        points_3, _ = score(token_3, settings)
        assert points_3 > points_1

    def test_smart_money_boost_capped(self, token_factory, settings_factory):
        """5 smart wallet buys should be capped same as 4 (both at 80)."""
        settings = settings_factory(SMART_MONEY_BOOST_CAP=80)
        token_4 = token_factory(smart_money_buys=4, liquidity_usd=20000)
        token_5 = token_factory(smart_money_buys=5, liquidity_usd=20000)
        points_4, _ = score(token_4, settings)
        points_5, _ = score(token_5, settings)
        assert points_5 == points_4


class TestRawMax:
    """CR-001: RAW_MAX must stay in sync with the actual signal point totals."""

    def test_raw_max_matches_signal_sum(self):
        """CR-001: RAW_MAX must match the actual sum of all max signal points."""
        signal_max_points = {
            "vol_liq_ratio": 30,
            "market_cap_tier": 8,
            "holder_growth": 25,
            "token_age": 10,
            "social_mentions": 15,
            "buy_pressure": 15,
            "score_velocity": 10,
            "unique_buyers": 15,
            "solana_bonus": 5,
            "small_txn_ratio": 5,
            "smart_money_buys": 10,
            "whale_buys": 5,
            "liquidity_locked": 10,
            "volume_spike_5x": 15,  # mutually exclusive with 3x
            "holder_gini_healthy": 5,
            "whale_txns_1h": 5,
            "has_twitter": 3,
            "has_telegram": 3,
            "has_github": 2,
            "on_coingecko": 8,
            "multi_dex": 5,
            "has_news": 7,
            "bullish_news": 8,
        }
        expected_max = sum(signal_max_points.values())
        assert RAW_MAX == expected_max, f"RAW_MAX={RAW_MAX} but signal sum={expected_max}"
