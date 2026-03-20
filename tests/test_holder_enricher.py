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

    # Mock DAS API (holder count) - called twice (holder count + deployer balance)
    mock_aiohttp.post(helius_rpc, payload={"result": {"total": 450, "items": []}})
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
