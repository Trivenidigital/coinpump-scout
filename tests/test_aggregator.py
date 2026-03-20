"""Tests for candidate aggregator."""

from scout.aggregator import aggregate
from scout.models import CandidateToken


def _make_token(**overrides) -> CandidateToken:
    defaults = dict(
        contract_address="0xTEST1234", chain="solana", token_name="Test",
        ticker="TST", token_age_days=1.0, market_cap_usd=50000.0,
        liquidity_usd=10000.0, volume_24h_usd=80000.0,
        holder_count=100, holder_growth_1h=20,
    )
    defaults.update(overrides)
    return CandidateToken(**defaults)


def test_aggregate_dedup_by_contract_address():
    t1 = _make_token(contract_address="0xabc12345", volume_24h_usd=50000)
    t2 = _make_token(contract_address="0xabc12345", volume_24h_usd=99999)  # same addr, newer data
    t3 = _make_token(contract_address="0xdef12345", volume_24h_usd=30000)

    result = aggregate([t1, t2, t3])

    assert len(result) == 2
    by_addr = {t.contract_address: t for t in result}
    assert by_addr["0xabc12345"].volume_24h_usd == 99999  # last-write-wins
    assert by_addr["0xdef12345"].volume_24h_usd == 30000


def test_aggregate_empty_input():
    assert aggregate([]) == []


def test_aggregate_single_token():
    t = _make_token()
    result = aggregate([t])
    assert len(result) == 1
    assert result[0].contract_address == "0xTEST1234"


def test_aggregate_preserves_all_fields():
    t = _make_token(contract_address="0xfull1234", quant_score=75, holder_count=500)
    result = aggregate([t])
    assert result[0].quant_score == 75
    assert result[0].holder_count == 500


def test_aggregate_prefers_nonzero_fields():
    """CR-003: Aggregator should merge fields, not last-write-wins."""
    from scout.models import CandidateToken
    dex = CandidateToken(
        contract_address="0xSAMETOKEN12", chain="solana",
        token_name="Token", ticker="TKN",
        buys_1h=50, sells_1h=20,
        volume_24h_usd=100000.0,
        token_age_days=2.5,
    )
    gecko = CandidateToken(
        contract_address="0xSAMETOKEN12", chain="solana",
        token_name="Token", ticker="TKN",
        buys_1h=0, sells_1h=0,
        volume_24h_usd=95000.0,
        liquidity_usd=50000.0,
    )
    result = aggregate([dex, gecko])
    assert len(result) == 1
    merged = result[0]
    assert merged.buys_1h == 50
    assert merged.sells_1h == 20
    assert merged.volume_24h_usd == 100000.0
    assert merged.liquidity_usd == 50000.0
    assert merged.token_age_days == 2.5
