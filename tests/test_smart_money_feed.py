"""Tests for smart money feed ingestion source (reads from separate injections.db)."""
import pytest
from unittest.mock import AsyncMock, MagicMock

import aiosqlite

from scout.ingestion.smart_money_feed import fetch_smart_money_injections
from scout.config import Settings


def _settings(tmp_path, **overrides):
    defaults = dict(
        TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c", ANTHROPIC_API_KEY="k",
        SMART_MONEY_WALLETS="wallet1,wallet2",
        INJECTIONS_DB_PATH=str(tmp_path / "injections.db"),
    )
    defaults.update(overrides)
    return Settings(**defaults)


async def _create_injections_db(path: str) -> aiosqlite.Connection:
    """Create the injections DB with schema (mirrors what sniper creates)."""
    conn = await aiosqlite.connect(path)
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
        CREATE INDEX IF NOT EXISTS idx_smi_unprocessed
            ON smart_money_injections(processed, detected_at);
    """)
    return conn


@pytest.mark.asyncio
async def test_no_injections_returns_empty(tmp_path):
    """No unprocessed injections -> empty list."""
    settings = _settings(tmp_path)
    conn = await _create_injections_db(str(settings.INJECTIONS_DB_PATH))
    await conn.close()

    mock_db = AsyncMock()
    mock_session = AsyncMock()
    result = await fetch_smart_money_injections(mock_session, mock_db, settings)
    assert result == []


@pytest.mark.asyncio
async def test_injection_creates_candidate_with_smart_money_count(tmp_path):
    """Injection with 2 wallets buying same token -> smart_money_buys=2."""
    settings = _settings(tmp_path)
    conn = await _create_injections_db(str(settings.INJECTIONS_DB_PATH))
    mint = "mint1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx"
    await conn.execute(
        "INSERT INTO smart_money_injections (token_mint, wallet_address, tx_signature) VALUES (?, ?, ?)",
        (mint, "wallet1", "tx1"),
    )
    await conn.execute(
        "INSERT INTO smart_money_injections (token_mint, wallet_address, tx_signature) VALUES (?, ?, ?)",
        (mint, "wallet2", "tx2"),
    )
    await conn.commit()
    await conn.close()

    mock_db = AsyncMock()

    # Mock DexScreener response
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=[{
        "tokenAddress": mint,
        "info": {"name": "TestToken", "symbol": "TST"},
        "marketCap": 50000,
        "liquidity": {"usd": 20000},
        "volume": {"h24": 100000},
    }])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=cm)

    result = await fetch_smart_money_injections(mock_session, mock_db, settings)
    assert len(result) == 1
    assert result[0].contract_address == mint
    assert result[0].smart_money_buys == 2
    assert result[0].token_name == "TestToken"

    # Verify injections are marked processed in injections.db
    conn2 = await aiosqlite.connect(str(settings.INJECTIONS_DB_PATH))
    cursor = await conn2.execute("SELECT COUNT(*) FROM smart_money_injections WHERE processed = 0")
    row = await cursor.fetchone()
    assert row[0] == 0
    await conn2.close()


@pytest.mark.asyncio
async def test_dexscreener_failure_leaves_injections_unprocessed(tmp_path):
    """If DexScreener returns error, injections stay unprocessed for retry."""
    settings = _settings(tmp_path)
    conn = await _create_injections_db(str(settings.INJECTIONS_DB_PATH))
    await conn.execute(
        "INSERT INTO smart_money_injections (token_mint, wallet_address, tx_signature) VALUES (?, ?, ?)",
        ("mint1xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx", "wallet1", "tx1"),
    )
    await conn.commit()
    await conn.close()

    mock_db = AsyncMock()

    mock_resp = AsyncMock()
    mock_resp.status = 404
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=cm)

    result = await fetch_smart_money_injections(mock_session, mock_db, settings)
    assert result == []

    # Injections should still be unprocessed
    conn2 = await aiosqlite.connect(str(settings.INJECTIONS_DB_PATH))
    cursor = await conn2.execute("SELECT COUNT(*) FROM smart_money_injections WHERE processed = 0")
    row = await cursor.fetchone()
    assert row[0] == 1
    await conn2.close()


@pytest.mark.asyncio
async def test_partial_dexscreener_success_marks_only_successful(tmp_path):
    """When DexScreener returns data for some mints but not others, only mark successful ones."""
    settings = _settings(tmp_path)
    conn = await _create_injections_db(str(settings.INJECTIONS_DB_PATH))
    await conn.execute(
        "INSERT INTO smart_money_injections (token_mint, wallet_address, tx_signature) VALUES (?, ?, ?)",
        ("mint_found", "wallet1", "tx1"),
    )
    await conn.execute(
        "INSERT INTO smart_money_injections (token_mint, wallet_address, tx_signature) VALUES (?, ?, ?)",
        ("mint_missing", "wallet2", "tx2"),
    )
    await conn.commit()
    await conn.close()

    mock_db = AsyncMock()

    # DexScreener only returns data for mint_found
    mock_resp = AsyncMock()
    mock_resp.status = 200
    mock_resp.json = AsyncMock(return_value=[{
        "tokenAddress": "mint_found",
        "info": {"name": "Found", "symbol": "FND"},
        "marketCap": 50000,
        "liquidity": {"usd": 20000},
        "volume": {"h24": 100000},
    }])
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_resp)
    cm.__aexit__ = AsyncMock(return_value=False)
    mock_session = MagicMock()
    mock_session.get = MagicMock(return_value=cm)

    result = await fetch_smart_money_injections(mock_session, mock_db, settings)
    assert len(result) == 1
    assert result[0].contract_address == "mint_found"

    # mint_found should be processed, mint_missing should not
    conn2 = await aiosqlite.connect(str(settings.INJECTIONS_DB_PATH))
    conn2.row_factory = aiosqlite.Row
    cursor = await conn2.execute("SELECT token_mint, processed FROM smart_money_injections ORDER BY token_mint")
    rows = [dict(r) for r in await cursor.fetchall()]
    assert len(rows) == 2
    found_row = next(r for r in rows if r["token_mint"] == "mint_found")
    missing_row = next(r for r in rows if r["token_mint"] == "mint_missing")
    assert found_row["processed"] == 1
    assert missing_row["processed"] == 0
    await conn2.close()
