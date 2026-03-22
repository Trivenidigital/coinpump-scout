"""Tests for CryptoPanic news sentiment."""
import pytest
from unittest.mock import AsyncMock
from scout.ingestion.cryptopanic import check_cryptopanic_sentiment, enrich_news_sentiment
from scout.models import CandidateToken
from scout.config import Settings


def _settings(**overrides):
    defaults = dict(
        TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c", ANTHROPIC_API_KEY="k",
        CRYPTOPANIC_API_KEY="",
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.mark.asyncio
async def test_no_api_key_returns_defaults():
    """When CRYPTOPANIC_API_KEY is empty, returns safe defaults without HTTP call."""
    settings = _settings(CRYPTOPANIC_API_KEY="")
    result = await check_cryptopanic_sentiment("TST", AsyncMock(), settings)
    assert result["has_news"] is False
    assert result["news_mentions"] == 0
    assert result["news_sentiment"] == 0.0


@pytest.mark.asyncio
async def test_enrich_no_key_returns_unchanged():
    """enrich_news_sentiment returns token unchanged when no API key configured."""
    token = CandidateToken(
        contract_address="0xTEST1234", chain="solana",
        token_name="Test", ticker="TST",
    )
    settings = _settings(CRYPTOPANIC_API_KEY="")
    result = await enrich_news_sentiment(token, AsyncMock(), settings)
    assert result.has_news is False
    assert result.news_mentions == 0


@pytest.mark.asyncio
async def test_enrich_no_key_does_not_make_http_calls():
    """With no API key, the session is never called."""
    token = CandidateToken(
        contract_address="0xTEST1234", chain="solana",
        token_name="Test", ticker="TST",
    )
    mock_session = AsyncMock()
    settings = _settings(CRYPTOPANIC_API_KEY="")
    await enrich_news_sentiment(token, mock_session, settings)
    mock_session.get.assert_not_called()
