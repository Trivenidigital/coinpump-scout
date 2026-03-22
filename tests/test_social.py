"""Tests for social sentiment enrichment."""
import pytest
from unittest.mock import AsyncMock
from scout.ingestion.social import enrich_social_sentiment
from scout.models import CandidateToken
from scout.config import Settings


def _settings(**overrides):
    defaults = dict(
        TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c", ANTHROPIC_API_KEY="k",
        SOCIAL_ENRICHMENT_ENABLED=False,
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.mark.asyncio
async def test_enrichment_disabled_returns_unchanged():
    """When SOCIAL_ENRICHMENT_ENABLED=False, token is returned unchanged."""
    token = CandidateToken(
        contract_address="0xTEST1234", chain="solana",
        token_name="Test", ticker="TST",
    )
    settings = _settings(SOCIAL_ENRICHMENT_ENABLED=False)
    result = await enrich_social_sentiment(token, AsyncMock(), settings)
    assert result.social_mentions_24h == 0
    assert result.has_twitter is False


@pytest.mark.asyncio
async def test_enrichment_disabled_preserves_existing_fields():
    """When disabled, any pre-existing fields on the token are preserved."""
    token = CandidateToken(
        contract_address="0xTEST1234", chain="solana",
        token_name="Test", ticker="TST",
        has_telegram=True,
    )
    settings = _settings(SOCIAL_ENRICHMENT_ENABLED=False)
    result = await enrich_social_sentiment(token, AsyncMock(), settings)
    # Unchanged — the pre-existing field is kept as-is
    assert result.has_telegram is True
