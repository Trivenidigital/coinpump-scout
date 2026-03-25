"""Quality Gate — hard rejection filters applied before narrative scoring."""

import structlog
from datetime import datetime, timezone

from scout.config import Settings
from scout.db import Database
from scout.models import CandidateToken

logger = structlog.get_logger()


class QualityGate:
    """Hard reject tokens that fail minimum quality checks.

    All checks must pass. Runs BEFORE MiroFish/Claude to avoid
    wasting API calls on garbage tokens.
    """

    def __init__(self, settings: Settings, db: Database):
        self.settings = settings
        self.db = db

    async def evaluate(self, token: CandidateToken) -> dict:
        """Evaluate token against all quality gates.

        Returns: {"pass": bool, "reason": str | None}
        """
        if not self.settings.QUALITY_GATE_ENABLED:
            return {"pass": True, "reason": None}

        # Gate 1: quant_score >= MIN_QUANT_SCORE
        if (token.quant_score or 0) < self.settings.MIN_QUANT_SCORE:
            return self._reject("quant_score_below_minimum", token)

        # Gate 2: top-3 concentration < MAX_TOP3_CONCENTRATION
        if token.top3_wallet_concentration > (self.settings.MAX_TOP3_CONCENTRATION / 100.0):
            return self._reject(f"top3_concentration_{token.top3_wallet_concentration:.2f}", token)

        # Gate 3: unique buyers >= MIN_UNIQUE_BUYERS
        # H3: Skip when HELIUS_API_KEY is not set (unique_buyers will always be 0)
        if self.settings.HELIUS_API_KEY and self.settings.HELIUS_API_KEY.strip():
            if token.unique_buyers_1h < self.settings.MIN_UNIQUE_BUYERS:
                return self._reject(f"unique_buyers_{token.unique_buyers_1h}", token)

        # Gate 4: token age < MAX_TOKEN_AGE_HOURS
        if token.token_age_days * 24 > self.settings.MAX_TOKEN_AGE_HOURS:
            return self._reject(f"token_too_old_{token.token_age_days:.1f}d", token)

        # Gate 5: volume acceleration > MIN_VOL_ACCELERATION
        vol_accel = await self._check_volume_acceleration(token)
        if vol_accel < self.settings.MIN_VOL_ACCELERATION:
            return self._reject(f"low_vol_acceleration_{vol_accel:.1f}x", token)

        # Gate 6: holder growth > MIN_HOLDER_GROWTH_PER_HOUR
        # Skip when HELIUS_API_KEY is not set — holder_count is capped at ~20
        # by Rugcheck, making growth always 0 and rejecting everything.
        if self.settings.HELIUS_API_KEY and self.settings.HELIUS_API_KEY.strip():
            growth = await self._check_holder_growth(token)
            if growth is not None and growth < self.settings.MIN_HOLDER_GROWTH_PER_HOUR:
                return self._reject(f"slow_holder_growth_{growth:.1f}/hr", token)

        logger.info("Quality gate PASSED", token=token.token_name, ticker=token.ticker)
        return {"pass": True, "reason": None}

    async def _check_volume_acceleration(self, token: CandidateToken) -> float:
        """Compare current volume snapshot to previous snapshot.

        Uses volume_24h_usd as the snapshot value — the diff between scans
        approximates recent volume activity. Returns acceleration ratio.
        """
        prev_vol = await self.db.get_prev_vol_gate_snapshot(token.contract_address)
        current_vol = token.volume_24h_usd

        # Always store current snapshot for next comparison
        await self.db.log_vol_gate_snapshot(token.contract_address, current_vol)

        if prev_vol is None:
            # First time seeing this token, allow through
            return float('inf')

        # Avoid division by tiny/zero values producing extreme ratios
        if prev_vol < 1.0 or current_vol <= 0:
            return 0.0

        return current_vol / prev_vol

    async def _check_holder_growth(self, token: CandidateToken) -> float | None:
        """Calculate holder growth rate per hour from snapshots.

        Returns None if no historical snapshot exists (allow through).
        """
        if token.holder_count <= 0:
            return 0.0

        prev = await self.db.get_holder_snapshot_older_than(token.contract_address, minutes=15)
        if prev is None:
            return None  # No history, allow through

        prev_count, recorded_at = prev
        # Calculate real elapsed time from the snapshot timestamp
        snapshot_time = datetime.fromisoformat(recorded_at)
        now = datetime.now(timezone.utc)
        elapsed_hours = max(0.1, (now - snapshot_time).total_seconds() / 3600)
        growth = token.holder_count - prev_count
        return growth / elapsed_hours

    def _reject(self, reason: str, token: CandidateToken) -> dict:
        logger.info(
            "Quality gate REJECTED",
            token=token.token_name,
            ticker=token.ticker,
            reason=reason,
        )
        return {"pass": False, "reason": reason}
