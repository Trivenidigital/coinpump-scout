"""Tests for LLM fallback narrative scorer (OpenAI-compatible)."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from scout.mirofish.fallback import score_narrative_fallback
from scout.models import MiroFishResult


SAMPLE_SEED = {
    "token_name": "TestCoin",
    "ticker": "TST",
    "chain": "solana",
    "market_cap": 50000,
    "age_hours": 60,
    "concept_description": "A meme token",
    "social_snippets": "None detected",
    "prompt": "Token: TestCoin (TST) on solana. Predict: will this narrative spread?",
}


def _mock_openai_response(content: str):
    """Create a mock OpenAI chat completion response."""
    choice = MagicMock()
    choice.message.content = content
    response = MagicMock()
    response.choices = [choice]
    return response


def _make_mock_client(content: str) -> AsyncMock:
    """Create a mock AsyncOpenAI client with a pre-configured response."""
    mock_client = AsyncMock()
    mock_client.chat.completions.create.return_value = _mock_openai_response(content)
    return mock_client


@pytest.mark.asyncio
async def test_fallback_parses_json_response():
    response_json = json.dumps({
        "narrative_score": 65,
        "virality_class": "Medium",
        "summary": "Moderate viral potential.",
    })
    mock_client = _make_mock_client(response_json)

    result = await score_narrative_fallback(SAMPLE_SEED, "test-api-key", client=mock_client)

    assert isinstance(result, MiroFishResult)
    assert result.narrative_score == 65
    assert result.virality_class == "Medium"
    assert result.summary == "Moderate viral potential."


@pytest.mark.asyncio
async def test_fallback_extracts_json_from_markdown():
    """LLM sometimes wraps JSON in ```json code blocks."""
    content = '```json\n{"narrative_score": 80, "virality_class": "High", "summary": "Very viral."}\n```'
    mock_client = _make_mock_client(content)

    result = await score_narrative_fallback(SAMPLE_SEED, "test-api-key", client=mock_client)

    assert result.narrative_score == 80
    assert result.virality_class == "High"


@pytest.mark.asyncio
async def test_fallback_uses_correct_model():
    response_json = json.dumps({
        "narrative_score": 50,
        "virality_class": "Low",
        "summary": "Weak narrative.",
    })
    mock_client = _make_mock_client(response_json)

    await score_narrative_fallback(
        SAMPLE_SEED, "test-api-key", model_name="qwen-plus", client=mock_client,
    )

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    assert call_kwargs["model"] == "qwen-plus"
    assert call_kwargs["max_tokens"] == 300


@pytest.mark.asyncio
async def test_fallback_sends_system_and_user_messages():
    response_json = json.dumps({
        "narrative_score": 70,
        "virality_class": "High",
        "summary": "Strong narrative.",
    })
    mock_client = _make_mock_client(response_json)

    await score_narrative_fallback(SAMPLE_SEED, "test-api-key", client=mock_client)

    call_kwargs = mock_client.chat.completions.create.call_args.kwargs
    messages = call_kwargs["messages"]
    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == SAMPLE_SEED["prompt"]
