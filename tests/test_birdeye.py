"""Tests for Birdeye ingestion."""
import pytest
from unittest.mock import AsyncMock
from scout.ingestion.birdeye import fetch_trending_birdeye
from scout.config import Settings


def _settings(**overrides):
    defaults = dict(
        TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c", ANTHROPIC_API_KEY="k",
        BIRDEYE_API_KEY="",
    )
    defaults.update(overrides)
    return Settings(**defaults)


@pytest.mark.asyncio
async def test_no_api_key_returns_empty():
    """fetch_trending_birdeye returns [] when BIRDEYE_API_KEY is empty."""
    settings = _settings(BIRDEYE_API_KEY="")
    result = await fetch_trending_birdeye(AsyncMock(), settings)
    assert result == []


@pytest.mark.asyncio
async def test_no_api_key_does_not_call_session():
    """With no API key, the session is never called."""
    mock_session = AsyncMock()
    settings = _settings(BIRDEYE_API_KEY="")
    await fetch_trending_birdeye(mock_session, settings)
    mock_session.get.assert_not_called()
