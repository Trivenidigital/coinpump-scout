"""Conviction gate: combines quant and narrative scores to decide alerts."""

import structlog

import aiohttp

from scout.config import Settings
from scout.db import Database
from scout.exceptions import ScorerError
from scout.mirofish.fallback import score_narrative_fallback
from scout.mirofish.seed_builder import build_seed
from scout.models import CandidateToken

logger = structlog.get_logger()


async def evaluate(
    token: CandidateToken,
    db: Database,
    session: aiohttp.ClientSession,
    settings: Settings,
    signals_fired: list[str] | None = None,
) -> tuple[bool, float, CandidateToken]:
    """Evaluate a candidate token through the conviction gate.

    Returns:
        (should_alert, conviction_score, updated_token)
    """
    quant_score = token.quant_score or 0
    narrative_score = None

    # Run narrative scoring (direct Claude Haiku call) if quant_score passes MIN_SCORE
    if quant_score >= settings.MIN_SCORE:
        try:
            narrative_score = await _get_narrative_score(
                token, settings, signals_fired=signals_fired,
            )
        except ScorerError as e:
            logger.warning("Narrative scoring failed, using quant-only",
                           contract_address=token.contract_address, error=str(e))
            narrative_score = None

    # Compute conviction score
    if narrative_score is not None:
        conviction = (quant_score * settings.QUANT_WEIGHT) + (narrative_score * settings.NARRATIVE_WEIGHT)
    else:
        # No narrative score — use quant directly with separate threshold (M3).
        # Applying QUANT_WEIGHT (0.6) here double-penalizes: the score is already
        # on a 0-100 scale, and the threshold is already lower.
        conviction = float(quant_score)

    # M3: Use higher threshold when narrative is unavailable
    threshold = settings.CONVICTION_THRESHOLD if narrative_score is not None else settings.QUANT_ONLY_CONVICTION_THRESHOLD
    should_alert = conviction >= threshold

    # Update token with scores
    updated = token.model_copy(update={
        "narrative_score": narrative_score,
        "conviction_score": conviction,
    })

    return (should_alert, conviction, updated)


async def _get_narrative_score(
    token: CandidateToken,
    settings: Settings,
    signals_fired: list[str] | None = None,
) -> int | None:
    """Score narrative using Claude Haiku directly.

    Raises ScorerError on failure.
    """
    seed = build_seed(token, signals_fired=signals_fired)

    try:
        result = await score_narrative_fallback(seed, settings.ANTHROPIC_API_KEY)
        return result.narrative_score
    except Exception as e:
        raise ScorerError(f"Narrative scoring failed: {e}") from e
