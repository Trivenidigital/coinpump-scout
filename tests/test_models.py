"""Tests for scout.models module."""

from datetime import datetime, timezone

import pytest

from scout.models import CandidateToken, MiroFishResult


def test_candidate_token_creation():
    token = CandidateToken(
        contract_address="0xabc12345",
        chain="solana",
        token_name="TestToken",
        ticker="TEST",
        token_age_days=2.5,
        market_cap_usd=50000.0,
        liquidity_usd=10000.0,
        volume_24h_usd=80000.0,
        holder_count=300,
        holder_growth_1h=25,
    )
    assert token.contract_address == "0xabc12345"
    assert token.chain == "solana"
    assert token.quant_score is None
    assert token.narrative_score is None
    assert token.conviction_score is None
    assert token.mirofish_report is None
    assert token.virality_class is None
    assert token.alerted_at is None
    assert token.first_seen_at is not None
    assert isinstance(token.first_seen_at, datetime)


def test_candidate_token_from_dexscreener():
    raw = {
        "baseToken": {"address": "0xdef45678", "name": "MemeToken", "symbol": "MEME"},
        "chainId": "solana",
        "pairCreatedAt": 1710720000000,  # milliseconds timestamp
        "fdv": 100000,
        "liquidity": {"usd": 20000},
        "volume": {"h24": 150000},
    }
    token = CandidateToken.from_dexscreener(raw)
    assert token.contract_address == "0xdef45678"
    assert token.chain == "solana"
    assert token.token_name == "MemeToken"
    assert token.ticker == "MEME"
    assert token.market_cap_usd == 100000
    assert token.liquidity_usd == 20000
    assert token.volume_24h_usd == 150000
    assert token.holder_count == 0  # not enriched yet
    assert token.holder_growth_1h == 0
    assert token.token_age_days >= 0


def test_candidate_token_from_geckoterminal():
    raw = {
        "id": "solana_0xgecko",
        "attributes": {
            "name": "GeckoToken / SOL",
            "base_token_price_usd": "0.001",
            "fdv_usd": "75000",
            "reserve_in_usd": "15000",
            "volume_usd": {"h24": "60000"},
            "pool_created_at": "2026-03-17T10:00:00Z",
        },
        "relationships": {
            "base_token": {"data": {"id": "solana_0xgeckoaddr"}},
        },
    }
    token = CandidateToken.from_geckoterminal(raw, chain="solana")
    assert token.contract_address == "0xgeckoaddr"
    assert token.chain == "solana"
    assert token.token_name == "GeckoToken"
    assert token.market_cap_usd == 75000
    assert token.liquidity_usd == 15000
    assert token.volume_24h_usd == 60000
    assert token.holder_count == 0
    assert token.holder_growth_1h == 0


def test_candidate_token_from_dexscreener_missing_optional_fields():
    """DexScreener sometimes returns null/missing fields."""
    raw = {
        "baseToken": {"address": "0xMINIMUM1", "name": "MinToken", "symbol": "MIN"},
        "chainId": "ethereum",
        "pairCreatedAt": None,
        "fdv": None,
        "liquidity": {"usd": None},
        "volume": {"h24": None},
    }
    token = CandidateToken.from_dexscreener(raw)
    assert token.contract_address == "0xMINIMUM1"
    assert token.market_cap_usd == 0
    assert token.liquidity_usd == 0
    assert token.volume_24h_usd == 0


def test_mirofish_result():
    result = MiroFishResult(
        narrative_score=85,
        virality_class="High",
        summary="Strong narrative with viral potential",
    )
    assert result.narrative_score == 85
    assert result.virality_class == "High"
    assert result.summary == "Strong narrative with viral potential"


def _make_base_token(**overrides) -> CandidateToken:
    defaults = dict(
        contract_address="0xTEST1234", chain="solana", token_name="Test",
        ticker="TST",
    )
    defaults.update(overrides)
    return CandidateToken(**defaults)


def test_empty_contract_address_rejected():
    with pytest.raises(ValueError, match="contract_address must be at least 8 characters"):
        _make_base_token(contract_address="")


def test_short_contract_address_rejected():
    with pytest.raises(ValueError, match="contract_address must be at least 8 characters"):
        _make_base_token(contract_address="abc")


def test_valid_contract_address_accepted():
    token = _make_base_token(contract_address="abcdefgh")
    assert token.contract_address == "abcdefgh"


def test_negative_ratio_clamped():
    token = _make_base_token(
        top3_wallet_concentration=-0.5,
        deployer_supply_pct=-1.0,
        small_txn_ratio=-99.0,
    )
    assert token.top3_wallet_concentration == 0.0
    assert token.deployer_supply_pct == 0.0
    assert token.small_txn_ratio == 0.0


def test_ratio_above_one_clamped():
    token = _make_base_token(
        top3_wallet_concentration=1.5,
        deployer_supply_pct=2.0,
        small_txn_ratio=99.9,
    )
    assert token.top3_wallet_concentration == 1.0
    assert token.deployer_supply_pct == 1.0
    assert token.small_txn_ratio == 1.0


def test_narrative_score_clamped_to_100():
    result = MiroFishResult(narrative_score=150, virality_class="High", summary="Over the top")
    assert result.narrative_score == 100


def test_narrative_score_clamped_to_0():
    result = MiroFishResult(narrative_score=-10, virality_class="Low", summary="Negative")
    assert result.narrative_score == 0
