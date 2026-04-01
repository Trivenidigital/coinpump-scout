"""Shared test fixtures for CoinPump Scout."""

import pytest

from scout.config import Settings
from scout.models import CandidateToken

# Env vars that leak from .env and override Settings defaults in tests.
_ENV_VARS_TO_CLEAR = [
    "MIN_SCORE", "CONVICTION_THRESHOLD", "QUANT_ONLY_CONVICTION_THRESHOLD",
    "CHAINS", "SCAN_INTERVAL_SECONDS", "MAX_MIROFISH_JOBS_PER_DAY",
    "HELIUS_API_KEY", "MORALIS_API_KEY", "ANTHROPIC_API_KEY",
    "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID", "PAPER_MODE",
    "PUMPFUN_ENABLED", "ONCHAIN_SIGNALS_ENABLED", "QUALITY_GATE_ENABLED",
    "SOCIAL_ENRICHMENT_ENABLED", "TWITTER_SCOUT_ENABLED",
    "BIRDEYE_API_KEY", "SOCIALDATA_API_KEY", "CRYPTOPANIC_API_KEY",
    "GOPLUS_FAIL_CLOSED", "POOL_WATCHER_ENABLED",
    "SMART_MONEY_WALLETS", "DISCORD_WEBHOOK_URL",
    "REENTRY_MIN_CONVICTION", "REENTRY_DIP_PCT",
    "MIN_LIQUIDITY_USD", "MAX_CANDIDATES_PER_CYCLE",
    "SNIPER_DB_PATH", "INJECTIONS_DB_PATH",
]


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """Remove production env vars so Settings uses code defaults.

    Also patches Settings to not read .env file during tests.
    """
    for var in _ENV_VARS_TO_CLEAR:
        monkeypatch.delenv(var, raising=False)
    # Prevent Pydantic BaseSettings from reading .env file
    monkeypatch.setattr(
        "scout.config.Settings.model_config",
        {**Settings.model_config, "env_file": None},
    )


@pytest.fixture
def settings_factory():
    def _make(**overrides):
        defaults = dict(TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c", ANTHROPIC_API_KEY="k")
        defaults.update(overrides)
        return Settings(**defaults)
    return _make


@pytest.fixture
def token_factory():
    def _make(**overrides):
        defaults = dict(
            contract_address="0xTEST1234", chain="solana", token_name="Test",
            ticker="TST", token_age_days=1.0, market_cap_usd=50000.0,
            liquidity_usd=10000.0, volume_24h_usd=80000.0,
            holder_count=100, holder_growth_1h=25,
        )
        defaults.update(overrides)
        return CandidateToken(**defaults)
    return _make
