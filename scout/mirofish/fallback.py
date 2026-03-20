"""Claude API fallback for narrative scoring when MiroFish is unavailable."""

import json
import structlog
import re

import anthropic

from scout.exceptions import ScorerError
from scout.models import MiroFishResult

logger = structlog.get_logger()

SYSTEM_PROMPT = (
    "You are a crypto narrative analyst. Score the viral potential of a token's "
    "narrative. Return ONLY a JSON object with these exact fields:\n"
    '{"narrative_score": <int 0-100>, "virality_class": "<Low|Medium|High|Viral>", '
    '"summary": "<2-3 sentence analysis>"}\n'
    "No other text. JSON only."
)


async def score_narrative_fallback(
    seed: dict,
    api_key: str,
    model_name: str = "claude-haiku-4-5",  # CR-019: use canonical model ID
    client: anthropic.AsyncAnthropic | None = None,
) -> MiroFishResult:
    """Score a token's narrative using Claude as a fallback.

    Raises ScorerError on any failure (empty response, malformed JSON, API error).
    """
    if client is None:
        client = anthropic.AsyncAnthropic(api_key=api_key)

    try:
        message = await client.messages.create(
            model=model_name,
            max_tokens=300,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": seed["prompt"]}],
        )
    except Exception as e:
        raise ScorerError(f"Claude API call failed: {e}") from e

    if not message.content:
        raise ScorerError("Claude returned empty response content")

    # Find the first text block (handle non-TextBlock types)
    text = None
    for block in message.content:
        if hasattr(block, "text"):
            text = block.text
            break
    if text is None:
        raise ScorerError("Claude response contained no text blocks")

    try:
        data = _extract_json(text)
    except (json.JSONDecodeError, ValueError) as e:
        raise ScorerError(f"Failed to parse Claude response as JSON: {e}") from e

    return MiroFishResult(
        narrative_score=int(data["narrative_score"]),
        virality_class=str(data["virality_class"]),
        summary=str(data["summary"]),
    )


def _extract_json(text: str) -> dict:
    """Extract JSON from text that may include markdown code blocks."""
    # Try to find JSON in a code block first
    match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
    if match:
        return json.loads(match.group(1).strip())
    # Otherwise try to parse the whole text
    return json.loads(text.strip())
