"""Narrative scoring integration (Claude Haiku direct call)."""

from scout.mirofish.fallback import score_narrative_fallback
from scout.mirofish.seed_builder import build_seed

__all__ = ["score_narrative_fallback", "build_seed"]
