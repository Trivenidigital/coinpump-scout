"""Tests for scout.db module."""

import pytest

from scout.db import Database
from scout.models import CandidateToken


@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()


def _make_token(**overrides) -> CandidateToken:
    defaults = dict(
        contract_address="0xTEST1234",
        chain="solana",
        token_name="Test",
        ticker="TST",
        token_age_days=1.0,
        market_cap_usd=50000.0,
        liquidity_usd=10000.0,
        volume_24h_usd=80000.0,
        holder_count=100,
        holder_growth_1h=20,
    )
    defaults.update(overrides)
    return CandidateToken(**defaults)


async def test_upsert_and_retrieve(db):
    token = _make_token(quant_score=75)
    await db.upsert_candidate(token)
    candidates = await db.get_candidates_above_score(60)
    assert len(candidates) == 1
    assert candidates[0]["contract_address"] == "0xTEST1234"
    assert candidates[0]["quant_score"] == 75


async def test_upsert_updates_existing(db):
    token = _make_token()
    await db.upsert_candidate(token)
    token2 = _make_token(volume_24h_usd=99999.0, quant_score=80)
    await db.upsert_candidate(token2)
    candidates = await db.get_candidates_above_score(0)
    assert len(candidates) == 1
    assert candidates[0]["volume_24h_usd"] == 99999.0


async def test_get_candidates_above_score_filters(db):
    await db.upsert_candidate(_make_token(contract_address="0xaaaa1234", quant_score=50))
    await db.upsert_candidate(_make_token(contract_address="0xbbbb1234", quant_score=70))
    await db.upsert_candidate(_make_token(contract_address="0xcccc1234", quant_score=None))
    results = await db.get_candidates_above_score(60)
    assert len(results) == 1
    assert results[0]["contract_address"] == "0xbbbb1234"


async def test_log_alert_and_daily_count(db):
    await db.log_alert("0xalert", "solana", 85.0)
    await db.log_alert("0xalert2", "ethereum", 72.0)
    count = await db.get_daily_alert_count()
    assert count == 2


async def test_log_mirofish_job_and_daily_count(db):
    await db.log_mirofish_job("0xjob1")
    await db.log_mirofish_job("0xjob2")
    await db.log_mirofish_job("0xjob3")
    count = await db.get_daily_mirofish_count()
    assert count == 3


async def test_get_recent_alerts(db):
    await db.log_alert("0xrecent", "solana", 90.0)
    alerts = await db.get_recent_alerts(days=30)
    assert len(alerts) == 1
    assert alerts[0]["contract_address"] == "0xrecent"


@pytest.mark.asyncio
async def test_upsert_preserves_first_seen_at(db):
    """CR-002: first_seen_at must survive re-upsert."""
    from datetime import datetime, timezone
    from scout.models import CandidateToken

    original_time = datetime(2026, 1, 1, tzinfo=timezone.utc)
    token = CandidateToken(
        contract_address="0xPRESERVE123", chain="solana",
        token_name="Preserve", ticker="PRE",
        first_seen_at=original_time,
    )
    await db.upsert_candidate(token)

    # Upsert again with different data
    updated = token.model_copy(update={
        "market_cap_usd": 99999.0,
        "first_seen_at": datetime.now(timezone.utc),
    })
    await db.upsert_candidate(updated)

    cursor = await db._conn.execute(
        "SELECT first_seen_at FROM candidates WHERE contract_address = ?",
        ("0xPRESERVE123",),
    )
    result = await cursor.fetchone()
    assert result[0] == original_time.isoformat()


@pytest.mark.asyncio
async def test_log_volume_and_get_avg_volume(db):
    """CR-024: log 3 volumes then verify avg is correct."""
    addr = "0xVOL_HISTORY1"
    await db.log_volume(addr, 100.0)
    await db.log_volume(addr, 200.0)
    await db.log_volume(addr, 300.0)
    avg = await db.get_avg_volume(addr)
    assert avg == pytest.approx(200.0)


@pytest.mark.asyncio
async def test_get_avg_volume_returns_none_when_empty(db):
    """CR-024: no volume data -> returns None."""
    result = await db.get_avg_volume("0xNO_VOLUME123")
    assert result is None


@pytest.mark.asyncio
async def test_log_signal_snapshot_stores_all_fields(db):
    """CR-024: log a snapshot, query it back, verify key fields."""
    token = _make_token(
        contract_address="0xSNAPSHOT123",
        chain="solana",
        token_name="Snap",
        ticker="SNP",
        quant_score=42,
    )
    await db.log_signal_snapshot(
        scan_cycle=7,
        token=token,
        quant_score=42,
        signals_fired=["vol_liq_ratio", "holder_growth"],
        disqualified=False,
        disqualify_reason=None,
        narrative_score=60,
        conviction_score=70.5,
        alerted=True,
        safe=True,
    )

    snapshots = await db.get_signal_snapshots(contract_address="0xSNAPSHOT123")
    assert len(snapshots) == 1
    snap = snapshots[0]
    assert snap["scan_cycle"] == 7
    assert snap["contract_address"] == "0xSNAPSHOT123"
    assert snap["quant_score"] == 42
    assert snap["signals_fired"] == "vol_liq_ratio,holder_growth"
    assert snap["narrative_score"] == 60
    assert snap["conviction_score"] == pytest.approx(70.5)
    assert snap["alerted"] == 1
    assert snap["safe"] == 1


@pytest.mark.asyncio
async def test_wal_mode_enabled(tmp_path):
    """Database should use WAL journal mode for concurrent access."""
    from scout.db import Database
    db = Database(tmp_path / "test.db")
    await db.initialize()
    cursor = await db._conn.execute("PRAGMA journal_mode")
    row = await cursor.fetchone()
    assert row[0] == "wal"
    await db.close()


@pytest.mark.asyncio
async def test_smart_money_injections_table_exists(tmp_path):
    """smart_money_injections table should be created on init."""
    from scout.db import Database
    db = Database(tmp_path / "test.db")
    await db.initialize()
    cursor = await db._conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='smart_money_injections'"
    )
    row = await cursor.fetchone()
    assert row is not None
    await db.close()


@pytest.mark.asyncio
async def test_read_unprocessed_injections(tmp_path):
    """Should read unprocessed injections and mark them as processed atomically."""
    from scout.db import Database
    db = Database(tmp_path / "test.db")
    await db.initialize()
    await db._conn.execute(
        "INSERT INTO smart_money_injections (token_mint, wallet_address, tx_signature) VALUES (?, ?, ?)",
        ("mint1", "wallet1", "tx1"),
    )
    await db._conn.execute(
        "INSERT INTO smart_money_injections (token_mint, wallet_address, tx_signature) VALUES (?, ?, ?)",
        ("mint1", "wallet2", "tx2"),
    )
    await db._conn.commit()
    injections = await db.read_and_mark_injections()
    assert len(injections) == 2
    assert injections[0]["token_mint"] == "mint1"
    second_read = await db.read_and_mark_injections()
    assert len(second_read) == 0
    await db.close()


@pytest.mark.asyncio
async def test_injection_dedup_on_tx_signature(tmp_path):
    """Duplicate (token_mint, tx_signature) should be ignored."""
    from scout.db import Database
    db = Database(tmp_path / "test.db")
    await db.initialize()
    await db._conn.execute(
        "INSERT OR IGNORE INTO smart_money_injections (token_mint, wallet_address, tx_signature) VALUES (?, ?, ?)",
        ("mint1", "wallet1", "tx1"),
    )
    await db._conn.execute(
        "INSERT OR IGNORE INTO smart_money_injections (token_mint, wallet_address, tx_signature) VALUES (?, ?, ?)",
        ("mint1", "wallet1", "tx1"),
    )
    await db._conn.commit()
    cursor = await db._conn.execute("SELECT COUNT(*) FROM smart_money_injections")
    row = await cursor.fetchone()
    assert row[0] == 1
    await db.close()


@pytest.mark.asyncio
async def test_cleanup_old_injections(tmp_path):
    """Processed injections older than 7 days should be cleaned up."""
    from scout.db import Database
    db = Database(tmp_path / "test.db")
    await db.initialize()
    await db._conn.execute(
        "INSERT INTO smart_money_injections (token_mint, wallet_address, tx_signature, processed, detected_at) VALUES (?, ?, ?, 1, datetime('now', '-8 days'))",
        ("old_mint", "wallet1", "old_tx"),
    )
    await db._conn.execute(
        "INSERT INTO smart_money_injections (token_mint, wallet_address, tx_signature, processed) VALUES (?, ?, ?, 1)",
        ("new_mint", "wallet1", "new_tx"),
    )
    await db._conn.commit()
    deleted = await db.cleanup_old_injections()
    assert deleted == 1
    await db.close()


@pytest.mark.asyncio
async def test_get_unprocessed_injections_does_not_mark(tmp_path):
    """get_unprocessed_injections should read rows without marking them processed."""
    from scout.db import Database
    db = Database(tmp_path / "test.db")
    await db.initialize()
    await db._conn.execute(
        "INSERT INTO smart_money_injections (token_mint, wallet_address, tx_signature) VALUES (?, ?, ?)",
        ("mint1", "wallet1", "tx1"),
    )
    await db._conn.commit()
    # First read
    injections = await db.get_unprocessed_injections()
    assert len(injections) == 1
    assert injections[0]["token_mint"] == "mint1"
    # Second read should still return the same row (not marked)
    injections2 = await db.get_unprocessed_injections()
    assert len(injections2) == 1
    await db.close()


@pytest.mark.asyncio
async def test_mark_injections_processed_only_marks_specified_ids(tmp_path):
    """mark_injections_processed should only mark the specified IDs."""
    from scout.db import Database
    db = Database(tmp_path / "test.db")
    await db.initialize()
    await db._conn.execute(
        "INSERT INTO smart_money_injections (token_mint, wallet_address, tx_signature) VALUES (?, ?, ?)",
        ("mint1", "wallet1", "tx1"),
    )
    await db._conn.execute(
        "INSERT INTO smart_money_injections (token_mint, wallet_address, tx_signature) VALUES (?, ?, ?)",
        ("mint2", "wallet2", "tx2"),
    )
    await db._conn.commit()
    # Get IDs
    injections = await db.get_unprocessed_injections()
    assert len(injections) == 2
    # Mark only the first one
    await db.mark_injections_processed([injections[0]["id"]])
    # Second read should only return the unmarked one
    remaining = await db.get_unprocessed_injections()
    assert len(remaining) == 1
    assert remaining[0]["token_mint"] == "mint2"
    await db.close()


@pytest.mark.asyncio
async def test_mark_injections_processed_empty_list(tmp_path):
    """mark_injections_processed with empty list should be a no-op."""
    from scout.db import Database
    db = Database(tmp_path / "test.db")
    await db.initialize()
    await db._conn.execute(
        "INSERT INTO smart_money_injections (token_mint, wallet_address, tx_signature) VALUES (?, ?, ?)",
        ("mint1", "wallet1", "tx1"),
    )
    await db._conn.commit()
    await db.mark_injections_processed([])
    remaining = await db.get_unprocessed_injections()
    assert len(remaining) == 1
    await db.close()


@pytest.mark.asyncio
async def test_get_oldest_unprocessed_injection_age_seconds(tmp_path):
    """Should return age in seconds of oldest unprocessed injection."""
    from scout.db import Database
    db = Database(tmp_path / "test.db")
    await db.initialize()
    # Insert an old unprocessed injection
    await db._conn.execute(
        "INSERT INTO smart_money_injections (token_mint, wallet_address, tx_signature, detected_at) VALUES (?, ?, ?, ?)",
        ("mint1", "wallet1", "tx1", "2020-01-01T00:00:00"),
    )
    await db._conn.commit()
    age = await db.get_oldest_unprocessed_injection_age_seconds()
    assert age is not None
    assert age > 0
    await db.close()


@pytest.mark.asyncio
async def test_get_oldest_unprocessed_injection_age_none_when_empty(tmp_path):
    """Should return None when no unprocessed injections exist."""
    from scout.db import Database
    db = Database(tmp_path / "test.db")
    await db.initialize()
    age = await db.get_oldest_unprocessed_injection_age_seconds()
    assert age is None
    await db.close()


@pytest.mark.asyncio
async def test_get_oldest_unprocessed_injection_age_none_all_processed(tmp_path):
    """Should return None when all injections are processed."""
    from scout.db import Database
    db = Database(tmp_path / "test.db")
    await db.initialize()
    await db._conn.execute(
        "INSERT INTO smart_money_injections (token_mint, wallet_address, tx_signature, processed) VALUES (?, ?, ?, 1)",
        ("mint1", "wallet1", "tx1"),
    )
    await db._conn.commit()
    age = await db.get_oldest_unprocessed_injection_age_seconds()
    assert age is None
    await db.close()


@pytest.mark.asyncio
async def test_prune_old_data(db):
    from datetime import datetime, timezone
    await db._conn.execute(
        "INSERT INTO score_history (contract_address, score, scanned_at) VALUES (?, ?, ?)",
        ("0xOLD_TOKEN12", 50, "2020-01-01T00:00:00"),
    )
    await db._conn.execute(
        "INSERT INTO score_history (contract_address, score, scanned_at) VALUES (?, ?, ?)",
        ("0xNEW_TOKEN12", 60, datetime.now(timezone.utc).isoformat()),
    )
    await db._conn.commit()

    await db.prune_old_data(retention_days=30)

    cursor = await db._conn.execute("SELECT COUNT(*) FROM score_history")
    row = await cursor.fetchone()
    assert row[0] == 1
