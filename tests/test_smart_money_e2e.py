"""End-to-end test: smart money injection -> scout pipeline -> scored candidate."""
import pytest
from collections import defaultdict
from scout.config import Settings
from scout.db import Database
from scout.scorer import score
from scout.models import CandidateToken


def _settings(**overrides):
    defaults = dict(
        TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c", ANTHROPIC_API_KEY="k",
        SMART_MONEY_WALLETS="wallet1,wallet2",
        SMART_MONEY_BOOST_CAP=80,
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.mark.asyncio
async def test_injection_write_read_cycle(tmp_path):
    """Simulate sniper writing injection -> scout reading it."""
    db = Database(tmp_path / "scout.db")
    await db.initialize()
    # Simulate sniper writing injections
    await db._conn.execute(
        "INSERT OR IGNORE INTO smart_money_injections "
        "(token_mint, wallet_address, tx_signature, source) VALUES (?, ?, ?, ?)",
        ("mint_abc", "wallet1", "tx_001", "websocket"),
    )
    await db._conn.execute(
        "INSERT OR IGNORE INTO smart_money_injections "
        "(token_mint, wallet_address, tx_signature, source) VALUES (?, ?, ?, ?)",
        ("mint_abc", "wallet2", "tx_002", "websocket"),
    )
    await db._conn.commit()
    # Scout reads unprocessed (doesn't mark yet)
    injections = await db.get_unprocessed_injections()
    assert len(injections) == 2
    # Group by token
    wallets_per_token = defaultdict(set)
    ids = []
    for inj in injections:
        wallets_per_token[inj["token_mint"]].add(inj["wallet_address"])
        ids.append(inj["id"])
    assert wallets_per_token["mint_abc"] == {"wallet1", "wallet2"}
    # Mark as processed after successful DexScreener fetch
    await db.mark_injections_processed(ids)
    # Verify marked as processed
    second_read = await db.get_unprocessed_injections()
    assert len(second_read) == 0
    await db.close()


@pytest.mark.asyncio
async def test_graduated_scoring_with_smart_money():
    """Token with 3 smart money buys should get graduated boost in scorer."""
    settings = _settings(SMART_MONEY_BOOST_CAP=80)
    token = CandidateToken(
        contract_address="mint_xyz",
        chain="solana",
        token_name="SmartTest",
        ticker="SMT",
        market_cap_usd=50000,
        liquidity_usd=20000,
        volume_24h_usd=150000,
        smart_money_buys=3,
    )
    points, signals = score(token, settings)
    assert "smart_money_buys" in signals
    assert points > 0
    # Compare with 0 smart money
    token_no_sm = token.model_copy(update={"smart_money_buys": 0})
    points_no_sm, _ = score(token_no_sm, settings)
    assert points > points_no_sm
