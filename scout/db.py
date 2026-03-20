"""Async SQLite database layer for CoinPump Scout."""

from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

from scout.models import CandidateToken

# Columns that map 1:1 from CandidateToken to the candidates table.
_CANDIDATE_COLUMNS = [
    "contract_address",
    "chain",
    "token_name",
    "ticker",
    "token_age_days",
    "market_cap_usd",
    "liquidity_usd",
    "volume_24h_usd",
    "holder_count",
    "holder_growth_1h",
    "social_mentions_24h",
    "buys_1h",
    "sells_1h",
    "unique_buyers_1h",
    "top3_wallet_concentration",
    "deployer_supply_pct",
    "small_txn_ratio",
    # On-chain signals
    "smart_money_buys",
    "whale_buys",
    "liquidity_locked",
    "volume_spike",
    "volume_spike_ratio",
    "holder_gini_healthy",
    "whale_txns_1h",
    # Social presence
    "social_score",
    "has_twitter",
    "has_telegram",
    "has_github",
    # Market signals
    "on_coingecko",
    "multi_dex",
    "dex_count",
    # News sentiment
    "news_mentions",
    "news_sentiment",
    "has_news",
    # Pipeline scores
    "quant_score",
    "narrative_score",
    "conviction_score",
    "mirofish_report",
    "virality_class",
    "alerted_at",
    "first_seen_at",
]


class Database:
    """Thin async wrapper around an aiosqlite connection."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn: aiosqlite.Connection | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Open connection and create tables."""
        self._conn = await aiosqlite.connect(self._db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._create_tables()

    async def close(self) -> None:
        """Close the database connection."""
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Schema
    # ------------------------------------------------------------------

    async def _create_tables(self) -> None:
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS candidates (
                contract_address TEXT PRIMARY KEY,
                chain            TEXT NOT NULL,
                token_name       TEXT NOT NULL,
                ticker           TEXT NOT NULL,
                token_age_days   REAL    DEFAULT 0,
                market_cap_usd   REAL    DEFAULT 0,
                liquidity_usd    REAL    DEFAULT 0,
                volume_24h_usd   REAL    DEFAULT 0,
                holder_count     INTEGER DEFAULT 0,
                holder_growth_1h INTEGER DEFAULT 0,
                social_mentions_24h INTEGER DEFAULT 0,
                buys_1h          INTEGER DEFAULT 0,
                sells_1h         INTEGER DEFAULT 0,
                unique_buyers_1h INTEGER DEFAULT 0,
                top3_wallet_concentration REAL DEFAULT 0,
                deployer_supply_pct REAL DEFAULT 0,
                small_txn_ratio  REAL DEFAULT 0,
                smart_money_buys INTEGER DEFAULT 0,
                whale_buys       INTEGER DEFAULT 0,
                liquidity_locked INTEGER DEFAULT 0,
                volume_spike     INTEGER DEFAULT 0,
                volume_spike_ratio REAL DEFAULT 0,
                holder_gini_healthy INTEGER DEFAULT 0,
                whale_txns_1h    INTEGER DEFAULT 0,
                social_score     REAL DEFAULT 0,
                has_twitter      INTEGER DEFAULT 0,
                has_telegram     INTEGER DEFAULT 0,
                has_github       INTEGER DEFAULT 0,
                on_coingecko     INTEGER DEFAULT 0,
                multi_dex        INTEGER DEFAULT 0,
                dex_count        INTEGER DEFAULT 0,
                news_mentions    INTEGER DEFAULT 0,
                news_sentiment   REAL DEFAULT 0,
                has_news         INTEGER DEFAULT 0,
                quant_score      INTEGER,
                narrative_score  INTEGER,
                conviction_score REAL,
                mirofish_report  TEXT,
                virality_class   TEXT,
                alerted_at       TEXT,
                first_seen_at    TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_address  TEXT NOT NULL,
                chain             TEXT NOT NULL,
                conviction_score  REAL NOT NULL,
                alerted_at        TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS mirofish_jobs (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_address  TEXT NOT NULL,
                created_at        TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS score_history (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_address  TEXT NOT NULL,
                score             INTEGER NOT NULL,
                scanned_at        TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS holder_snapshots (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_address  TEXT NOT NULL,
                holder_count      INTEGER NOT NULL,
                recorded_at       TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS signal_snapshots (
                id                    INTEGER PRIMARY KEY AUTOINCREMENT,
                scan_cycle            INTEGER NOT NULL,
                contract_address      TEXT NOT NULL,
                chain                 TEXT NOT NULL,
                token_name            TEXT NOT NULL,
                ticker                TEXT NOT NULL,
                token_age_days        REAL DEFAULT 0,
                market_cap_usd        REAL DEFAULT 0,
                liquidity_usd         REAL DEFAULT 0,
                volume_24h_usd        REAL DEFAULT 0,
                holder_count          INTEGER DEFAULT 0,
                holder_growth_1h      INTEGER DEFAULT 0,
                buys_1h               INTEGER DEFAULT 0,
                sells_1h              INTEGER DEFAULT 0,
                unique_buyers_1h      INTEGER DEFAULT 0,
                top3_wallet_concentration REAL DEFAULT 0,
                deployer_supply_pct   REAL DEFAULT 0,
                small_txn_ratio       REAL DEFAULT 0,
                social_mentions_24h   INTEGER DEFAULT 0,
                quant_score           INTEGER DEFAULT 0,
                signals_fired         TEXT,
                disqualified          INTEGER DEFAULT 0,
                disqualify_reason     TEXT,
                narrative_score       INTEGER,
                conviction_score      REAL,
                alerted               INTEGER DEFAULT 0,
                safe                  INTEGER,
                scanned_at            TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_signal_snapshots_contract
                ON signal_snapshots (contract_address);
            CREATE INDEX IF NOT EXISTS idx_signal_snapshots_scanned
                ON signal_snapshots (scanned_at);
            CREATE INDEX IF NOT EXISTS idx_signal_snapshots_cycle
                ON signal_snapshots (scan_cycle);

            CREATE TABLE IF NOT EXISTS volume_history (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_address  TEXT NOT NULL,
                volume_24h        REAL NOT NULL,
                recorded_at       TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_volume_history_contract
                ON volume_history (contract_address);

            CREATE INDEX IF NOT EXISTS idx_score_history_contract
                ON score_history (contract_address, scanned_at DESC);
            CREATE INDEX IF NOT EXISTS idx_holder_snapshots_contract
                ON holder_snapshots (contract_address, recorded_at DESC);

            CREATE TABLE IF NOT EXISTS outcomes (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                contract_address  TEXT NOT NULL,
                alert_price       REAL,
                check_price       REAL,
                check_time        TEXT,
                price_change_pct  REAL
            );
            """
        )

    # ------------------------------------------------------------------
    # Candidates
    # ------------------------------------------------------------------

    async def upsert_candidate(self, token: CandidateToken) -> None:
        """Upsert candidate by contract_address, preserving first_seen_at."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")

        update_cols = [c for c in _CANDIDATE_COLUMNS if c != "first_seen_at"]
        placeholders = ", ".join("?" for _ in _CANDIDATE_COLUMNS)
        cols = ", ".join(_CANDIDATE_COLUMNS)
        update_set = ", ".join(f"{c} = excluded.{c}" for c in update_cols)

        values = []
        for col in _CANDIDATE_COLUMNS:
            v = getattr(token, col)
            if isinstance(v, datetime):
                v = v.isoformat()
            values.append(v)

        await self._conn.execute(
            f"""INSERT INTO candidates ({cols}) VALUES ({placeholders})
                ON CONFLICT(contract_address) DO UPDATE SET {update_set}""",
            values,
        )
        await self._conn.commit()

    async def get_candidates_above_score(self, min_score: int) -> list[dict]:
        """Get candidates with quant_score >= min_score."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        cursor = await self._conn.execute(
            "SELECT * FROM candidates WHERE quant_score IS NOT NULL AND quant_score >= ?",
            (min_score,),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Alerts
    # ------------------------------------------------------------------

    async def log_alert(
        self, contract_address: str, chain: str, conviction_score: float
    ) -> None:
        """Log a fired alert."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "INSERT INTO alerts (contract_address, chain, conviction_score, alerted_at) VALUES (?, ?, ?, ?)",
            (contract_address, chain, conviction_score, now),
        )
        await self._conn.commit()

    async def get_daily_alert_count(self) -> int:
        """Count alerts fired today (UTC)."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM alerts WHERE date(alerted_at) = ?",
            (today,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def get_recent_alerts(self, days: int = 30) -> list[dict]:
        """Get alerts from the last N days."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        cursor = await self._conn.execute(
            "SELECT * FROM alerts WHERE date(alerted_at) >= date('now', ?)",
            (f"-{days} days",),
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # MiroFish jobs
    # ------------------------------------------------------------------

    async def log_mirofish_job(self, contract_address: str) -> None:
        """Log a MiroFish simulation job."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "INSERT INTO mirofish_jobs (contract_address, created_at) VALUES (?, ?)",
            (contract_address, now),
        )
        await self._conn.commit()

    # ------------------------------------------------------------------
    # Score history (BL-013)
    # ------------------------------------------------------------------

    async def log_score(self, contract_address: str, score: int) -> None:
        """Log a quant score for velocity tracking."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "INSERT INTO score_history (contract_address, score, scanned_at) VALUES (?, ?, ?)",
            (contract_address, score, now),
        )
        await self._conn.commit()

    async def get_recent_scores(self, contract_address: str, limit: int = 3) -> list[int]:
        """Get the most recent scores for a token, oldest first."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        cursor = await self._conn.execute(
            "SELECT score FROM score_history WHERE contract_address = ? ORDER BY scanned_at DESC LIMIT ?",
            (contract_address, limit),
        )
        rows = await cursor.fetchall()
        return [row[0] for row in reversed(rows)]

    # ------------------------------------------------------------------
    # Holder snapshots (BL-020)
    # ------------------------------------------------------------------

    async def log_holder_snapshot(self, contract_address: str, holder_count: int) -> None:
        """Record a holder count snapshot for growth tracking."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "INSERT INTO holder_snapshots (contract_address, holder_count, recorded_at) VALUES (?, ?, ?)",
            (contract_address, holder_count, now),
        )
        await self._conn.commit()

    async def get_previous_holder_count(self, contract_address: str) -> int | None:
        """Get the most recent holder count snapshot for a token.

        Returns None if no previous snapshot exists (first scan).
        """
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        cursor = await self._conn.execute(
            "SELECT holder_count FROM holder_snapshots WHERE contract_address = ? ORDER BY recorded_at DESC LIMIT 1",
            (contract_address,),
        )
        row = await cursor.fetchone()
        return row[0] if row else None

    # ------------------------------------------------------------------
    # Volume history (on-chain signal enrichment)
    # ------------------------------------------------------------------

    async def log_volume(self, contract_address: str, volume_24h: float) -> None:
        """Record a 24h volume data point for spike detection."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            "INSERT INTO volume_history (contract_address, volume_24h, recorded_at) VALUES (?, ?, ?)",
            (contract_address, volume_24h, now),
        )
        await self._conn.commit()

    async def get_avg_volume(self, contract_address: str, lookback: int = 3) -> float | None:
        """Get the average 24h volume from the last *lookback* recordings.

        Returns None if no previous recordings exist.
        """
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        cursor = await self._conn.execute(
            "SELECT volume_24h FROM volume_history WHERE contract_address = ? ORDER BY recorded_at DESC LIMIT ?",
            (contract_address, lookback),
        )
        rows = await cursor.fetchall()
        if not rows:
            return None
        return sum(row[0] for row in rows) / len(rows)

    # ------------------------------------------------------------------
    # Signal snapshots (analytics)
    # ------------------------------------------------------------------

    async def log_signal_snapshot(
        self,
        scan_cycle: int,
        token: "CandidateToken",
        quant_score: int,
        signals_fired: list[str],
        disqualified: bool = False,
        disqualify_reason: str | None = None,
        narrative_score: int | None = None,
        conviction_score: float | None = None,
        alerted: bool = False,
        safe: bool | None = None,
    ) -> None:
        """Log a complete signal snapshot for every token in every scan cycle."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        now = datetime.now(timezone.utc).isoformat()
        await self._conn.execute(
            """INSERT INTO signal_snapshots
               (scan_cycle, contract_address, chain, token_name, ticker,
                token_age_days, market_cap_usd, liquidity_usd, volume_24h_usd,
                holder_count, holder_growth_1h, buys_1h, sells_1h,
                unique_buyers_1h, top3_wallet_concentration, deployer_supply_pct,
                small_txn_ratio, social_mentions_24h,
                quant_score, signals_fired, disqualified, disqualify_reason,
                narrative_score, conviction_score, alerted, safe, scanned_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                scan_cycle,
                token.contract_address, token.chain, token.token_name, token.ticker,
                token.token_age_days, token.market_cap_usd, token.liquidity_usd,
                token.volume_24h_usd, token.holder_count, token.holder_growth_1h,
                token.buys_1h, token.sells_1h, token.unique_buyers_1h,
                token.top3_wallet_concentration, token.deployer_supply_pct,
                token.small_txn_ratio, token.social_mentions_24h,
                quant_score, ",".join(signals_fired),
                1 if disqualified else 0, disqualify_reason,
                narrative_score, conviction_score,
                1 if alerted else 0, (1 if safe else 0) if safe is not None else None,
                now,
            ),
        )
        await self._conn.commit()

    async def get_signal_snapshots(
        self,
        contract_address: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        """Query signal snapshots for analysis."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        if contract_address:
            cursor = await self._conn.execute(
                "SELECT * FROM signal_snapshots WHERE contract_address = ? ORDER BY scanned_at DESC LIMIT ?",
                (contract_address, limit),
            )
        else:
            cursor = await self._conn.execute(
                "SELECT * FROM signal_snapshots ORDER BY scanned_at DESC LIMIT ?",
                (limit,),
            )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]

    async def get_daily_mirofish_count(self) -> int:
        """Count MiroFish jobs run today (UTC)."""
        if self._conn is None:
            raise RuntimeError("Database not initialized. Call initialize() first.")
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        cursor = await self._conn.execute(
            "SELECT COUNT(*) FROM mirofish_jobs WHERE date(created_at) = ?",
            (today,),
        )
        row = await cursor.fetchone()
        return row[0] if row else 0

    async def prune_old_data(self, retention_days: int = 30) -> None:
        """Delete time-series data older than retention_days."""
        if self._conn is None:
            raise RuntimeError("Database not initialized.")
        cutoff = f"-{retention_days} days"
        for table, col in [
            ("score_history", "scanned_at"),
            ("holder_snapshots", "recorded_at"),
            ("volume_history", "recorded_at"),
            ("signal_snapshots", "scanned_at"),
        ]:
            await self._conn.execute(
                f"DELETE FROM {table} WHERE {col} < datetime('now', ?)",
                (cutoff,),
            )
        await self._conn.commit()
