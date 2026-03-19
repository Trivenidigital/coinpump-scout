# CoinPump Scout

Python 3.12 async pipeline for pre-pump crypto token detection with MiroFish
narrative simulation.

## Commands
- `uv run python -m scout.main` — start scanner
- `uv run pytest` — run test suite
- `uv run pytest tests/test_scorer.py -v` — run single test file
- `uv run python -m scout.main --dry-run` — one cycle, no alerts fired
- `docker compose up -d` — start MiroFish (required for Stage 4)
- `curl http://localhost:5001/health` — verify MiroFish is live

## Architecture (6 stages)

Stage 1: Ingestion (dexscreener.py, geckoterminal.py, holder_enricher.py)
Stage 2: Aggregator (dedup by contract_address, normalize to CandidateToken)
Stage 3: Scorer (quant signals -> score 0-100, gate at MIN_SCORE env var)
Stage 4: MiroFish (narrative simulation -> narrative_score 0-100, fallback: Claude haiku)
Stage 5: Gate (conviction_score = quant*0.6 + narrative*0.4, fire if >= 70)
Stage 6: Alerter (Telegram + Discord, GoPlus safety check before alert)

## Coding conventions

- ALL I/O is async (aiohttp sessions, aiosqlite, asyncio.gather for parallel polls)
- Pydantic v2 BaseSettings for ALL config — no os.getenv() calls in business logic
- CandidateToken is a Pydantic BaseModel — validate at ingestion boundary
- Type hints on every function signature — no bare `dict` or `Any` in public APIs
- Errors: raise domain exceptions (ScorerError, MiroFishTimeoutError, etc.) — never swallow
- Tests: pytest-asyncio for all async tests, pytest-mock for external API mocking
- No print() statements — use Python logging with structlog for JSON output
- uv for dependency management — never pip install directly

## MiroFish integration rules

- MiroFish timeout = 180s (MIROFISH_TIMEOUT_SEC env var)
- On timeout or connection error -> ALWAYS fall back to fallback.py (Claude haiku)
- Never block alert delivery waiting for MiroFish
- Max 50 MiroFish jobs/day (cost guard — enforce in gate.py)

## Key files

- config.py: single source of truth for all env vars
- models.py: CandidateToken schema — add fields here first before using elsewhere
- scorer.py: scoring weights live here — document every weight change in git commit

<important if="modifying scorer.py">
The scoring weights (vol_liq_ratio, holder_growth, market_cap_range, token_age,
social_mentions) must always sum rationale in a docstring. Never change a weight
without adding a comment explaining why. This is the model's core logic.
</important>

<important if="modifying mirofish/client.py">
Always use the async context manager pattern for aiohttp sessions. Never create a
session outside of an async with block. The fallback to Claude haiku must remain
in place — do not remove it even temporarily.
</important>

## What NOT to do

- Do NOT add any order execution code — this is alerts only
- Do NOT store raw API responses in DB — normalize to CandidateToken first
- Do NOT use synchronous requests library — aiohttp only
- Do NOT hardcode contract addresses or token names anywhere
