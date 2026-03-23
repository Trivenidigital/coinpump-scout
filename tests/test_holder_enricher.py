"""Tests for holder enrichment."""

import re

import pytest
import aiohttp
from aioresponses import aioresponses

from scout.config import Settings
from scout.ingestion.holder_enricher import enrich_holders
from scout.models import CandidateToken


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
        ticker="TST", token_age_days=1.0, market_cap_usd=50000.0,
        liquidity_usd=10000.0, volume_24h_usd=80000.0,
    )
    defaults.update(overrides)
    return CandidateToken(**defaults)


@pytest.fixture
def mock_aiohttp():
    with aioresponses() as m:
        yield m


async def test_enrich_solana_with_helius(mock_aiohttp):
    token = _make_token(chain="solana", contract_address="SoLAddr123")
    settings = _settings(HELIUS_API_KEY="test-helius-key")

    helius_rpc = "https://mainnet.helius-rpc.com/?api-key=test-helius-key"
    helius_api = re.compile(r"https://api\.helius\.xyz/v0/addresses/.+/transactions.*")

    # Mock DAS API (holder count) - getTokenAccounts returns token_accounts list
    mock_aiohttp.post(helius_rpc, payload={
        "result": {"token_accounts": [{"address": f"a{i}"} for i in range(450)], "cursor": None}
    })
    mock_aiohttp.post(helius_rpc, payload={
        "result": {
            "authorities": [{"address": "DeployerWallet"}],
            "token_info": {"supply": "1000000", "decimals": 6},
        }
    })
    mock_aiohttp.post(helius_rpc, payload={
        "result": {"total": 1, "token_accounts": [{"amount": "100000"}]}
    })

    # Mock transaction analysis
    mock_aiohttp.get(helius_api, payload=[])

    async with aiohttp.ClientSession() as session:
        enriched = await enrich_holders(token, session, settings)

    assert enriched.holder_count == 450


async def test_enrich_evm_with_moralis(mock_aiohttp):
    token = _make_token(chain="ethereum", contract_address="0xEvmAddr")
    settings = _settings(MORALIS_API_KEY="test-moralis-key")

    mock_aiohttp.get(
        "https://deep-index.moralis.io/api/v2.2/erc20/0xEvmAddr/owners?chain=eth",
        payload={"result": [{"owner": "0x1"}, {"owner": "0x2"}], "cursor": None},
        headers={"X-API-Key": "test-moralis-key"},
    )

    async with aiohttp.ClientSession() as session:
        enriched = await enrich_holders(token, session, settings)

    assert enriched.holder_count == 2


async def test_enrich_no_api_key_returns_unenriched(mock_aiohttp):
    """Graceful degradation: no API key -> return token unchanged."""
    token = _make_token(chain="solana")
    settings = _settings()

    async with aiohttp.ClientSession() as session:
        enriched = await enrich_holders(token, session, settings)

    assert enriched.holder_count == 0
    assert enriched.holder_growth_1h == 0


async def test_enrich_api_failure_returns_unenriched(mock_aiohttp):
    """API failure -> return token unchanged, don't crash."""
    token = _make_token(chain="solana", contract_address="SoLAddr1")
    settings = _settings(HELIUS_API_KEY="bad-key")

    helius_rpc = "https://mainnet.helius-rpc.com/?api-key=bad-key"
    helius_api = re.compile(r"https://api\.helius\.xyz/v0/addresses/.+/transactions.*")

    mock_aiohttp.post(helius_rpc, status=500)
    mock_aiohttp.get(helius_api, status=500)
    mock_aiohttp.post(helius_rpc, status=500)

    async with aiohttp.ClientSession() as session:
        enriched = await enrich_holders(token, session, settings)

    assert enriched.holder_count == 0  # unchanged, graceful degradation


# ---------------------------------------------------------------------------
# Rugcheck path tests (CR-023)
# ---------------------------------------------------------------------------

async def test_enrich_rugcheck_success(mock_aiohttp):
    """Rugcheck success path sets holder_count, top3_wallet_concentration, deployer_supply_pct."""
    token = _make_token(chain="solana", contract_address="SoLAddrRug1")
    settings = _settings()  # no Helius key -> only Rugcheck runs

    rugcheck_url = "https://api.rugcheck.xyz/v1/tokens/SoLAddrRug1/report"
    mock_aiohttp.get(
        rugcheck_url,
        payload={
            "holderCount": 500,
            "topHolders": [
                {"pct": 10, "isInsider": False},
                {"pct": 8, "isInsider": False},
                {"pct": 7, "isInsider": True},
                {"pct": 5, "isInsider": False},
            ],
            "markets": [],
        },
    )

    async with aiohttp.ClientSession() as session:
        enriched = await enrich_holders(token, session, settings)

    assert enriched.holder_count == 500
    # top 3 pct = (10+8+7) out of (10+8+7+5)=30 total => 25/30 but calculated as top3/100
    assert enriched.top3_wallet_concentration == pytest.approx(0.25, abs=0.01)
    # insider pct = 7 => 7/100 = 0.07
    assert enriched.deployer_supply_pct == pytest.approx(0.07, abs=0.001)


async def test_enrich_rugcheck_failure_returns_unenriched(mock_aiohttp):
    """Rugcheck 500 -> token returned unchanged (graceful degradation)."""
    token = _make_token(chain="solana", contract_address="SoLAddrRug2")
    settings = _settings()

    rugcheck_url = "https://api.rugcheck.xyz/v1/tokens/SoLAddrRug2/report"
    mock_aiohttp.get(rugcheck_url, status=500)

    async with aiohttp.ClientSession() as session:
        enriched = await enrich_holders(token, session, settings)

    assert enriched.holder_count == 0
    assert enriched.top3_wallet_concentration == 0.0
    assert enriched.deployer_supply_pct == 0.0


async def test_enrich_rugcheck_sets_liquidity_locked(mock_aiohttp):
    """Rugcheck market with lpLockedPct > 50 sets liquidity_locked=True."""
    token = _make_token(chain="solana", contract_address="SoLAddrRug3")
    settings = _settings()

    rugcheck_url = "https://api.rugcheck.xyz/v1/tokens/SoLAddrRug3/report"
    mock_aiohttp.get(
        rugcheck_url,
        payload={
            "topHolders": [],
            "markets": [
                {"lp": {"lpLockedPct": 75}},
            ],
        },
    )

    async with aiohttp.ClientSession() as session:
        enriched = await enrich_holders(token, session, settings)

    assert enriched.liquidity_locked is True
