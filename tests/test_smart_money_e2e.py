"""End-to-end test: smart money injection -> scout pipeline -> scored candidate.

Tests use a separate injections.db (mirrors production architecture where sniper
writes to injections.db and scout reads from it).
"""
import pytest
from collections import defaultdict

import aiosqlite

from scout.config import Settings
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
    """Simulate sniper writing injection to injections.db -> scout reading it."""
    inj_path = str(tmp_path / "injections.db")
    async with aiosqlite.connect(inj_path) as conn:
        conn.row_factory = aiosqlite.Row
        await conn.executescript("""
            CREATE TABLE IF NOT EXISTS smart_money_injections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_mint TEXT NOT NULL,
                wallet_address TEXT NOT NULL,
                tx_signature TEXT,
                source TEXT DEFAULT 'websocket',
                detected_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                processed INTEGER DEFAULT 0,
                UNIQUE(token_mint, tx_signature)
            );
        """)
        # Simulate sniper writing injections
        await conn.execute(
            "INSERT OR IGNORE INTO smart_money_injections "
            "(token_mint, wallet_address, tx_signature, source) VALUES (?, ?, ?, ?)",
            ("mint_abc", "wallet1", "tx_001", "websocket"),
        )
        await conn.execute(
            "INSERT OR IGNORE INTO smart_money_injections "
            "(token_mint, wallet_address, tx_signature, source) VALUES (?, ?, ?, ?)",
            ("mint_abc", "wallet2", "tx_002", "websocket"),
        )
        await conn.commit()

        # Scout reads unprocessed
        cursor = await conn.execute(
            "SELECT id, token_mint, wallet_address FROM smart_money_injections WHERE processed = 0"
        )
        injections = [dict(r) for r in await cursor.fetchall()]
        assert len(injections) == 2

        # Group by token
        wallets_per_token = defaultdict(set)
        ids = []
        for inj in injections:
            wallets_per_token[inj["token_mint"]].add(inj["wallet_address"])
            ids.append(inj["id"])
        assert wallets_per_token["mint_abc"] == {"wallet1", "wallet2"}

        # Mark as processed after successful DexScreener fetch
        placeholders = ",".join("?" for _ in ids)
        await conn.execute(
            f"UPDATE smart_money_injections SET processed = 1 WHERE id IN ({placeholders})",
            ids,
        )
        await conn.commit()

        # Verify marked as processed
        cursor = await conn.execute(
            "SELECT id FROM smart_money_injections WHERE processed = 0"
        )
        second_read = await cursor.fetchall()
        assert len(second_read) == 0


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
