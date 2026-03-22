# CoinPump Scout — Full Project Scaffold Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a Python 3.12 async pipeline that detects pre-pump cryptocurrency tokens by combining quantitative DEX signals with MiroFish multi-agent narrative simulation, delivering alerts via Telegram/Discord.

**Architecture:** 6-stage async pipeline — Ingestion (DexScreener + GeckoTerminal + holder enrichment) → Aggregation → Scoring → MiroFish narrative simulation (with Claude haiku fallback) → Conviction gate → Alert delivery. All I/O is async. SQLite for persistence. Pydantic v2 for config and models.

**Tech Stack:** Python 3.12, aiohttp, pydantic v2, pydantic-settings, aiosqlite, anthropic SDK, structlog, pytest + pytest-asyncio + aioresponses, uv, Docker

---

## File Structure

```
coinpump-scout/
├── CLAUDE.md                          # Project conventions (≤180 lines)
├── .env.example                       # All env vars with descriptions
├── .gitignore                         # Python + env ignores
├── pyproject.toml                     # uv-compatible, Python 3.12
├── README.md                          # Brief setup instructions
├── Dockerfile                         # Python 3.12-slim, uv, non-root
├── docker-compose.yml                 # MiroFish + Scout services
│
├── scout/
│   ├── __init__.py
│   ├── main.py                        # Entrypoint: asyncio.run(main())
│   ├── config.py                      # Pydantic BaseSettings
│   ├── models.py                      # CandidateToken + MiroFishResult
│   ├── exceptions.py                  # Domain exceptions
│   ├── db.py                          # aiosqlite async DB layer
│   │
│   ├── ingestion/
│   │   ├── __init__.py
│   │   ├── dexscreener.py             # DexScreener API poller
│   │   ├── geckoterminal.py           # GeckoTerminal API poller
│   │   └── holder_enricher.py         # Helius/Moralis holder data
│   │
│   ├── aggregator.py                  # Merge, dedup, normalize
│   ├── scorer.py                      # Quantitative scoring engine
│   │
│   ├── mirofish/
│   │   ├── __init__.py
│   │   ├── client.py                  # Async MiroFish REST client
│   │   ├── seed_builder.py            # Build simulation seed payload
│   │   └── fallback.py                # Claude haiku fallback scorer
│   │
│   ├── gate.py                        # Conviction gate
│   ├── alerter.py                     # Telegram + Discord delivery
│   └── safety.py                      # GoPlus rug/honeypot check
│
├── tests/
│   ├── conftest.py                    # Shared fixtures
│   ├── test_config.py
│   ├── test_models.py
│   ├── test_db.py
│   ├── test_dexscreener.py
│   ├── test_geckoterminal.py
│   ├── test_holder_enricher.py
│   ├── test_aggregator.py
│   ├── test_scorer.py
│   ├── test_safety.py
│   ├── test_seed_builder.py
│   ├── test_mirofish_client.py
│   ├── test_fallback.py
│   ├── test_gate.py
│   ├── test_alerter.py
│   └── test_main.py
│
└── .claude/
    ├── commands/
    │   ├── scan.md
    │   ├── score.md
    │   ├── backtest.md
    │   └── status.md
    └── agents/
        ├── ingestion-agent.md
        ├── scorer-agent.md
        └── mirofish-agent.md
```

---

## Task 1: Project Skeleton + Tooling

**Files:**
- Create: `pyproject.toml`
- Create: `.gitignore`
- Create: `.env.example` (stub — expanded in Task 2)
- Create: `scout/__init__.py`
- Create: `tests/__init__.py` (empty)
- Create: `tests/conftest.py` (empty for now)

- [ ] **Step 1: Initialize uv project**

```bash
cd /c/projects/coinpump-scout
export PATH="$HOME/.local/bin:$PATH"
uv init --name coinpump-scout --python 3.12
```

Note: If Python 3.12 isn't installed locally, uv will download it automatically.

- [ ] **Step 2: Write pyproject.toml**

Replace the generated `pyproject.toml` with:

```toml
[project]
name = "coinpump-scout"
version = "0.1.0"
description = "Pre-pump crypto token detection with MiroFish narrative simulation"
requires-python = ">=3.12"
dependencies = [
    "aiohttp>=3.10,<4",
    "aiosqlite>=0.20,<1",
    "pydantic>=2.6,<3",
    "pydantic-settings>=2.2,<3",
    "anthropic>=0.40,<1",
    "structlog>=24.1,<25",
    "python-dotenv>=1.0,<2",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0,<9",
    "pytest-asyncio>=0.23,<1",
    "aioresponses>=0.7,<1",
    "pytest-mock>=3.12,<4",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 3: Write .gitignore**

Standard Python .gitignore plus `.env`, `scout.db`, `__pycache__/`, `.venv/`, `*.egg-info/`, `.pytest_cache/`.

- [ ] **Step 4: Create scout package**

```bash
mkdir -p scout/ingestion scout/mirofish tests .claude/commands .claude/agents
touch scout/__init__.py scout/ingestion/__init__.py scout/mirofish/__init__.py
```

- [ ] **Step 5: Install dependencies**

```bash
export PATH="$HOME/.local/bin:$PATH"
uv sync --all-extras
```

- [ ] **Step 6: Verify pytest runs**

```bash
uv run pytest --tb=short -q
```

Expected: `no tests ran` (0 errors).

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml .gitignore scout/ tests/ .claude/ uv.lock
git commit -m "chore: initialize project skeleton with uv, deps, and package structure"
```

---

## Task 2: CLAUDE.md + .env.example

**Files:**
- Create: `CLAUDE.md`
- Create: `.env.example`

- [ ] **Step 1: Write CLAUDE.md**

Must be ≤180 lines. Contents specified in the user prompt — project description, commands, 6-stage architecture, coding conventions, MiroFish integration rules, key files, what NOT to do. Copy the exact content from the prompt's STEP 2 section.

- [ ] **Step 2: Write .env.example**

Every variable from the PRD config table (Table 7), with descriptive comments and no real values:

```env
# === Scanner Config ===
SCAN_INTERVAL_SECONDS=60        # Polling interval for data sources
MIN_SCORE=60                    # Minimum quant score to trigger MiroFish
CONVICTION_THRESHOLD=70         # Minimum conviction score to fire alert
QUANT_WEIGHT=0.6                # Weight of quant score in conviction formula
NARRATIVE_WEIGHT=0.4            # Weight of narrative score in conviction formula

# === Token Filters ===
MIN_MARKET_CAP=10000            # Minimum market cap (USD)
MAX_MARKET_CAP=500000           # Maximum market cap (USD)
MAX_TOKEN_AGE_DAYS=7            # Maximum token age in days
MIN_VOL_LIQ_RATIO=5.0          # Minimum volume/liquidity ratio
CHAINS=solana,base,ethereum     # Comma-separated chains to scan

# === MiroFish ===
MIROFISH_URL=http://localhost:5001
MIROFISH_TIMEOUT_SEC=180

# === Alerts ===
TELEGRAM_BOT_TOKEN=             # Required: Telegram bot token
TELEGRAM_CHAT_ID=               # Required: Telegram channel/chat ID
DISCORD_WEBHOOK_URL=            # Optional: Discord webhook URL

# === Holder Enrichment (optional) ===
HELIUS_API_KEY=                 # Helius API key for Solana holder data
MORALIS_API_KEY=                # Moralis API key for EVM holder data

# === Database ===
DB_PATH=scout.db

# === Claude Fallback ===
ANTHROPIC_API_KEY=              # Required for MiroFish fallback scorer
```

- [ ] **Step 3: Commit**

```bash
git add CLAUDE.md .env.example
git commit -m "docs: add CLAUDE.md project bible and .env.example"
```

---

## Task 3: Domain Exceptions + Config

**Files:**
- Create: `scout/exceptions.py`
- Create: `scout/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write exceptions.py**

```python
class ScoutError(Exception):
    """Base exception for CoinPump Scout."""

class ScorerError(ScoutError):
    """Error in scoring logic."""

class MiroFishTimeoutError(ScoutError):
    """MiroFish simulation timed out."""

class MiroFishConnectionError(ScoutError):
    """Cannot connect to MiroFish service."""

class AlertDeliveryError(ScoutError):
    """Failed to deliver alert."""

class SafetyCheckError(ScoutError):
    """Error checking token safety."""
```

- [ ] **Step 2: Write failing test for config**

```python
# tests/test_config.py
from scout.config import Settings

def test_settings_loads_defaults():
    s = Settings(
        TELEGRAM_BOT_TOKEN="test-token",
        TELEGRAM_CHAT_ID="test-chat",
        ANTHROPIC_API_KEY="test-key",
    )
    assert s.SCAN_INTERVAL_SECONDS == 60
    assert s.MIN_SCORE == 60
    assert s.CONVICTION_THRESHOLD == 70
    assert s.QUANT_WEIGHT == 0.6
    assert s.NARRATIVE_WEIGHT == 0.4
    assert s.CHAINS == ["solana", "base", "ethereum"]
    assert s.DB_PATH == "scout.db"

def test_settings_chains_parsing():
    s = Settings(
        TELEGRAM_BOT_TOKEN="t", TELEGRAM_CHAT_ID="c", ANTHROPIC_API_KEY="k",
        CHAINS="solana,polygon",
    )
    assert s.CHAINS == ["solana", "polygon"]
```

- [ ] **Step 3: Run test — verify it fails**

```bash
uv run pytest tests/test_config.py -v
```

Expected: FAIL (module not found).

- [ ] **Step 4: Implement config.py**

```python
from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Scanner
    SCAN_INTERVAL_SECONDS: int = 60
    MIN_SCORE: int = 60
    CONVICTION_THRESHOLD: int = 70
    QUANT_WEIGHT: float = 0.6
    NARRATIVE_WEIGHT: float = 0.4

    # Token filters
    MIN_MARKET_CAP: float = 10_000
    MAX_MARKET_CAP: float = 500_000
    MAX_TOKEN_AGE_DAYS: int = 7
    MIN_VOL_LIQ_RATIO: float = 5.0
    CHAINS: list[str] = ["solana", "base", "ethereum"]

    # MiroFish
    MIROFISH_URL: str = "http://localhost:5001"
    MIROFISH_TIMEOUT_SEC: int = 180

    # Alerts
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str
    DISCORD_WEBHOOK_URL: str = ""

    # Holder enrichment
    HELIUS_API_KEY: str = ""
    MORALIS_API_KEY: str = ""

    # Database
    DB_PATH: str = "scout.db"

    # Claude fallback
    ANTHROPIC_API_KEY: str

    @field_validator("CHAINS", mode="before")
    @classmethod
    def parse_chains(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [c.strip() for c in v.split(",") if c.strip()]
        return v
```

- [ ] **Step 5: Run test — verify it passes**

```bash
uv run pytest tests/test_config.py -v
```

Expected: 2 passed.

- [ ] **Step 6: Commit**

```bash
git add scout/exceptions.py scout/config.py tests/test_config.py
git commit -m "feat: add domain exceptions and pydantic settings config"
```

---

## Task 4: CandidateToken Model

**Files:**
- Create: `scout/models.py`
- Create: `tests/test_models.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_models.py
from datetime import datetime, timezone
from scout.models import CandidateToken, MiroFishResult

def test_candidate_token_creation():
    token = CandidateToken(
        contract_address="0xabc123",
        chain="solana",
        token_name="TestToken",
        ticker="TEST",
        token_age_days=2.5,
        market_cap_usd=50000.0,
        liquidity_usd=10000.0,
        volume_24h_usd=80000.0,
        holder_count=300,
        holder_growth_1h=25,
    )
    assert token.contract_address == "0xabc123"
    assert token.quant_score is None
    assert token.first_seen_at is not None

def test_candidate_token_from_dexscreener():
    raw = {
        "baseToken": {"address": "0xdef", "name": "Meme", "symbol": "MEME"},
        "chainId": "solana",
        "pairCreatedAt": 1710000000000,
        "fdv": 100000,
        "liquidity": {"usd": 20000},
        "volume": {"h24": 150000},
    }
    token = CandidateToken.from_dexscreener(raw)
    assert token.contract_address == "0xdef"
    assert token.chain == "solana"
    assert token.ticker == "MEME"

def test_candidate_token_from_geckoterminal():
    raw = {
        "id": "solana_0xgecko",
        "attributes": {
            "name": "GeckoToken / SOL",
            "base_token_price_usd": "0.001",
            "fdv_usd": "75000",
            "reserve_in_usd": "15000",
            "volume_usd": {"h24": "60000"},
            "pool_created_at": "2026-03-17T10:00:00Z",
        },
        "relationships": {
            "base_token": {"data": {"id": "solana_0xgeckoaddr"}},
        },
    }
    token = CandidateToken.from_geckoterminal(raw, chain="solana")
    assert token.contract_address == "0xgeckoaddr"
    assert token.chain == "solana"

def test_mirofish_result():
    result = MiroFishResult(narrative_score=85, virality_class="High", summary="Strong narrative")
    assert result.narrative_score == 85
```

- [ ] **Step 2: Run test — verify failure**

```bash
uv run pytest tests/test_models.py -v
```

- [ ] **Step 3: Implement models.py**

`CandidateToken` as a Pydantic BaseModel with all PRD Section 6.1 fields. Include `from_dexscreener()` and `from_geckoterminal()` classmethods that parse raw API responses. `MiroFishResult` with narrative_score, virality_class, summary.

Key design:
- `first_seen_at` defaults to `datetime.now(timezone.utc)`
- `quant_score`, `narrative_score`, `conviction_score`, `mirofish_report`, `virality_class`, `alerted_at` all default to `None`
- `from_dexscreener` parses DexScreener's nested JSON (`baseToken.address`, `fdv`, `liquidity.usd`, `volume.h24`, `pairCreatedAt` → age calc)
- `from_geckoterminal` parses GeckoTerminal's `attributes` + `relationships` structure
- Both classmethods set `holder_count=0` and `holder_growth_1h=0` as defaults (enriched later)

- [ ] **Step 4: Run test — verify passes**

```bash
uv run pytest tests/test_models.py -v
```

- [ ] **Step 5: Commit**

```bash
git add scout/models.py tests/test_models.py
git commit -m "feat: add CandidateToken and MiroFishResult models with factory classmethods"
```

---

## Task 5: Database Layer

**Files:**
- Create: `scout/db.py`
- Create: `tests/test_db.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_db.py
import pytest
from scout.db import Database
from scout.models import CandidateToken

@pytest.fixture
async def db(tmp_path):
    database = Database(str(tmp_path / "test.db"))
    await database.initialize()
    yield database
    await database.close()

async def test_upsert_and_retrieve(db):
    token = CandidateToken(
        contract_address="0xtest", chain="solana", token_name="Test",
        ticker="TST", token_age_days=1, market_cap_usd=50000,
        liquidity_usd=10000, volume_24h_usd=80000,
        holder_count=100, holder_growth_1h=20, quant_score=75,
    )
    await db.upsert_candidate(token)
    candidates = await db.get_candidates_above_score(60)
    assert len(candidates) == 1
    assert candidates[0]["contract_address"] == "0xtest"

async def test_upsert_dedup(db):
    token = CandidateToken(
        contract_address="0xsame", chain="solana", token_name="Same",
        ticker="SAME", token_age_days=1, market_cap_usd=50000,
        liquidity_usd=10000, volume_24h_usd=80000,
        holder_count=100, holder_growth_1h=20,
    )
    await db.upsert_candidate(token)
    token.volume_24h_usd = 99999
    await db.upsert_candidate(token)
    candidates = await db.get_candidates_above_score(0)
    assert len(candidates) == 1

async def test_log_alert_and_count(db):
    await db.log_alert("0xalert", "solana", 85.0)
    count = await db.get_daily_alert_count()
    assert count == 1

async def test_get_daily_mirofish_count(db):
    await db.log_mirofish_job("0xjob1")
    await db.log_mirofish_job("0xjob2")
    count = await db.get_daily_mirofish_count()
    assert count == 2
```

- [ ] **Step 2: Run test — verify failure**

- [ ] **Step 3: Implement db.py**

Uses aiosqlite async context manager pattern. Tables:
- `candidates` — mirrors CandidateToken fields, `contract_address` as PRIMARY KEY
- `alerts` — `id INTEGER PRIMARY KEY AUTOINCREMENT, contract_address, chain, conviction_score, alerted_at`
- `mirofish_jobs` — `id, contract_address, created_at` (for daily cap tracking)
- `outcomes` — `id, contract_address, alert_price, check_price, check_time, price_change_pct`

Methods:
- `initialize()` — create tables if not exist
- `upsert_candidate(token)` — INSERT OR REPLACE
- `get_candidates_above_score(min_score)` — SELECT where quant_score >= min_score
- `log_alert(contract_address, chain, conviction_score)` — insert into alerts
- `get_daily_alert_count()` — COUNT alerts where alerted_at is today (UTC)
- `get_daily_mirofish_count()` — COUNT mirofish_jobs where created_at is today
- `log_mirofish_job(contract_address)` — insert into mirofish_jobs
- `get_recent_alerts(days=30)` — SELECT alerts from last N days
- `close()` — close connection

- [ ] **Step 4: Run test — verify passes**

- [ ] **Step 5: Commit**

```bash
git add scout/db.py tests/test_db.py
git commit -m "feat: add async SQLite database layer with candidates, alerts, mirofish_jobs tables"
```

---

## Task 6: DexScreener Ingestion

**Files:**
- Create: `scout/ingestion/dexscreener.py`
- Create: `tests/test_dexscreener.py`

- [ ] **Step 1: Write failing test**

Test with aioresponses mocking the DexScreener API. Mock `/dex/search?q=trending` returning a payload with 2 pairs. Verify returns `List[CandidateToken]` with correct fields parsed. Test 429 backoff by mocking a 429 then 200 response.

- [ ] **Step 2: Run test — verify failure**

- [ ] **Step 3: Implement dexscreener.py**

```python
async def fetch_trending(session: aiohttp.ClientSession, settings: Settings) -> list[CandidateToken]:
```

Polls DexScreener endpoints. Filters by market cap range and token age from settings. Exponential backoff on 429. Dependency injection: takes `session` and `settings` as params. Returns list of CandidateToken via `from_dexscreener()`.

- [ ] **Step 4: Run test — verify passes**

- [ ] **Step 5: Commit**

```bash
git add scout/ingestion/dexscreener.py tests/test_dexscreener.py
git commit -m "feat: add DexScreener trending token poller with backoff"
```

---

## Task 7: GeckoTerminal Ingestion

**Files:**
- Create: `scout/ingestion/geckoterminal.py`
- Create: `tests/test_geckoterminal.py`

- [ ] **Step 1: Write failing test**

Mock GeckoTerminal `/networks/{network}/trending_pools` for solana chain. Verify returns `List[CandidateToken]` with correct parsing via `from_geckoterminal()`.

- [ ] **Step 2: Run test — verify failure**

- [ ] **Step 3: Implement geckoterminal.py**

```python
async def fetch_trending_pools(session: aiohttp.ClientSession, settings: Settings) -> list[CandidateToken]:
```

Iterates over `settings.CHAINS`, polls each chain's trending pools. Filters by market cap/age. Returns combined list.

- [ ] **Step 4: Run test — verify passes**

- [ ] **Step 5: Commit**

```bash
git add scout/ingestion/geckoterminal.py tests/test_geckoterminal.py
git commit -m "feat: add GeckoTerminal trending pools poller"
```

---

## Task 8: Holder Enricher

**Files:**
- Create: `scout/ingestion/holder_enricher.py`
- Create: `tests/test_holder_enricher.py`

- [ ] **Step 1: Write failing test**

Test Solana path (mock Helius API), EVM path (mock Moralis API), and graceful degradation (no API key → return unenriched token).

- [ ] **Step 2: Run test — verify failure**

- [ ] **Step 3: Implement holder_enricher.py**

```python
async def enrich_holders(token: CandidateToken, session: aiohttp.ClientSession, settings: Settings) -> CandidateToken:
```

If chain == "solana" and HELIUS_API_KEY set → call Helius. If EVM chain and MORALIS_API_KEY set → call Moralis. Otherwise return token unchanged. Updates `holder_count` and `holder_growth_1h`.

- [ ] **Step 4: Run test — verify passes**

- [ ] **Step 5: Commit**

```bash
git add scout/ingestion/holder_enricher.py tests/test_holder_enricher.py
git commit -m "feat: add holder enrichment via Helius (Solana) and Moralis (EVM)"
```

---

## Task 9: Aggregator

**Files:**
- Create: `scout/aggregator.py`
- Create: `tests/test_aggregator.py`

- [ ] **Step 1: Write failing test**

Test dedup: two CandidateTokens with same contract_address from different sources → one output (last-write-wins on price/volume). Test empty input → empty output.

- [ ] **Step 2: Run test — verify failure**

- [ ] **Step 3: Implement aggregator.py**

```python
def aggregate(candidates: list[CandidateToken]) -> list[CandidateToken]:
```

Pure function. Dedup by contract_address using a dict (last-write-wins). Returns deduplicated list.

- [ ] **Step 4: Run test — verify passes**

- [ ] **Step 5: Commit**

```bash
git add scout/aggregator.py tests/test_aggregator.py
git commit -m "feat: add candidate aggregator with dedup by contract address"
```

---

## Task 10: Quantitative Scorer

**Files:**
- Create: `scout/scorer.py`
- Create: `tests/test_scorer.py`

- [ ] **Step 1: Write failing tests**

Test each signal independently:
- `vol_liq_ratio > 5` → 30 points
- `market_cap $10K–$500K` → 20 points
- `holder_growth > 20/hr` → 25 points
- `token_age < 7 days` → 10 points
- `social_mentions > 50` → 15 points (optional, 0 if not available)

Test combined scoring: all signals fire → 100 points. Edge cases: zero liquidity (ratio undefined → 0 points), zero holders, age exactly 7 days.

- [ ] **Step 2: Run test — verify failure**

- [ ] **Step 3: Implement scorer.py**

```python
def score(token: CandidateToken, settings: Settings) -> tuple[int, list[str]]:
    """Score a candidate token. Returns (score, signals_fired).

    Scoring weights:
    - vol_liq_ratio (>5×): 30 points — Primary pump precursor
    - market_cap ($10K–$500K): 20 points — Pre-discovery range
    - holder_growth (>20 new/hr): 25 points — Organic accumulation
    - token_age (<7 days): 10 points — Early stage
    - social_mentions (>50 in 24h): 15 points — CT discovery signal
    """
```

Pure Python — no I/O. Uses settings for configurable thresholds. Returns tuple of (score, list of signal names that fired).

- [ ] **Step 4: Run test — verify passes**

- [ ] **Step 5: Commit**

```bash
git add scout/scorer.py tests/test_scorer.py
git commit -m "feat: add 5-signal quantitative scoring engine"
```

---

## Task 11: GoPlus Safety Check

**Files:**
- Create: `scout/safety.py`
- Create: `tests/test_safety.py`

- [ ] **Step 1: Write failing test**

Test safe token (all checks pass → True). Test honeypot (→ False). Test high sell tax (→ False). Test API failure (→ True, fail open).

- [ ] **Step 2: Run test — verify failure**

- [ ] **Step 3: Implement safety.py**

```python
async def is_safe(contract_address: str, chain: str, session: aiohttp.ClientSession) -> bool:
```

Calls GoPlus Security API. Returns True if honeypot=0 AND is_blacklisted=0 AND buy_tax < 10% AND sell_tax < 10%. On API failure → log warning, return True.

- [ ] **Step 4: Run test — verify passes**

- [ ] **Step 5: Commit**

```bash
git add scout/safety.py tests/test_safety.py
git commit -m "feat: add GoPlus token safety check with fail-open behavior"
```

---

## Task 12: MiroFish Seed Builder

**Files:**
- Create: `scout/mirofish/seed_builder.py`
- Create: `tests/test_seed_builder.py`

- [ ] **Step 1: Write failing test**

Test that build_seed returns a dict with required keys: token_name, ticker, chain, market_cap, age_hours, concept_description, social_snippets, and a formatted prompt string matching PRD Section 8.2 format.

- [ ] **Step 2: Run test — verify failure**

- [ ] **Step 3: Implement seed_builder.py**

```python
def build_seed(token: CandidateToken) -> dict:
```

Pure function. Builds structured dict from CandidateToken fields. Includes the prompt string from PRD 8.2:
`"Token: {name} ({ticker}) on {chain}. Concept: {description}. Market cap: ${market_cap}. First seen: {hours_ago}h ago. Early social signals: {social_snippets}. Predict: will this narrative spread organically..."`

- [ ] **Step 4: Run test — verify passes**

- [ ] **Step 5: Commit**

```bash
git add scout/mirofish/seed_builder.py tests/test_seed_builder.py
git commit -m "feat: add MiroFish simulation seed builder"
```

---

## Task 13: MiroFish Client

**Files:**
- Create: `scout/mirofish/client.py`
- Create: `tests/test_mirofish_client.py`

- [ ] **Step 1: Write failing test**

Test successful simulation → MiroFishResult. Test timeout → raises MiroFishTimeoutError. Test malformed JSON response → raises MiroFishConnectionError.

- [ ] **Step 2: Run test — verify failure**

- [ ] **Step 3: Implement client.py**

```python
async def simulate(seed: dict, session: aiohttp.ClientSession, settings: Settings) -> MiroFishResult:
```

POST to `{MIROFISH_URL}/simulate` with seed payload. Timeout = MIROFISH_TIMEOUT_SEC. Parse response into MiroFishResult. On `asyncio.TimeoutError` → raise `MiroFishTimeoutError`. On connection error → raise `MiroFishConnectionError`. Always uses `async with` for session safety.

- [ ] **Step 4: Run test — verify passes**

- [ ] **Step 5: Commit**

```bash
git add scout/mirofish/client.py tests/test_mirofish_client.py
git commit -m "feat: add async MiroFish REST client with timeout handling"
```

---

## Task 14: Claude Fallback Scorer

**Files:**
- Create: `scout/mirofish/fallback.py`
- Create: `tests/test_fallback.py`

- [ ] **Step 1: Write failing test**

Mock the anthropic SDK `client.messages.create()`. Test that it returns MiroFishResult parsed from Claude's JSON response. Test extraction of JSON from text that includes non-JSON content (Claude sometimes wraps JSON in markdown).

- [ ] **Step 2: Run test — verify failure**

- [ ] **Step 3: Implement fallback.py**

```python
async def score_narrative_fallback(seed: dict, api_key: str) -> MiroFishResult:
```

Uses `anthropic.AsyncAnthropic` client. Model: `claude-haiku-4-5`, max_tokens=300. System prompt instructs Claude to return JSON only: `{ "narrative_score": int, "virality_class": str, "summary": str }`. Parses response, extracts JSON (handles markdown code block wrapping). Returns MiroFishResult.

- [ ] **Step 4: Run test — verify passes**

- [ ] **Step 5: Commit**

```bash
git add scout/mirofish/fallback.py tests/test_fallback.py
git commit -m "feat: add Claude haiku fallback narrative scorer"
```

---

## Task 15: Conviction Gate

**Files:**
- Create: `scout/gate.py`
- Create: `tests/test_gate.py`

- [ ] **Step 1: Write failing tests**

Test conviction exactly at boundary: score=69.9 → reject, score=70.0 → accept. Test daily MiroFish cap: 50 jobs already run → skip MiroFish, use quant-only score. Test with narrative_score=None → use quant-only.

- [ ] **Step 2: Run test — verify failure**

- [ ] **Step 3: Implement gate.py**

```python
async def evaluate(
    token: CandidateToken,
    db: Database,
    session: aiohttp.ClientSession,
    settings: Settings,
) -> tuple[bool, float]:
```

1. Check daily MiroFish count from DB. If < 50 and quant_score >= MIN_SCORE → run MiroFish simulation (with fallback on error).
2. Compute conviction_score = quant × QUANT_WEIGHT + narrative × NARRATIVE_WEIGHT. If no narrative_score, use quant_score alone.
3. Return (should_alert: bool, conviction_score: float).

- [ ] **Step 4: Run test — verify passes**

- [ ] **Step 5: Commit**

```bash
git add scout/gate.py tests/test_gate.py
git commit -m "feat: add conviction gate with MiroFish daily cap enforcement"
```

---

## Task 16: Alert Delivery

**Files:**
- Create: `scout/alerter.py`
- Create: `tests/test_alerter.py`

- [ ] **Step 1: Write failing test**

Mock Telegram Bot API and Discord webhook POST. Verify alert message contains: disclaimer, token name/ticker/chain, market cap, conviction breakdown, signals, virality class, DEXScreener link.

- [ ] **Step 2: Run test — verify failure**

- [ ] **Step 3: Implement alerter.py**

```python
async def send_alert(token: CandidateToken, signals: list[str], session: aiohttp.ClientSession, settings: Settings) -> None:
```

Formats alert message with all required fields. Sends to Telegram via Bot API (`/sendMessage`). If DISCORD_WEBHOOK_URL set, also POST to Discord webhook. Raises AlertDeliveryError on failure. Always prepends disclaimer.

- [ ] **Step 4: Run test — verify passes**

- [ ] **Step 5: Commit**

```bash
git add scout/alerter.py tests/test_alerter.py
git commit -m "feat: add Telegram and Discord alert delivery"
```

---

## Task 17: Main Pipeline Loop

**Files:**
- Create: `scout/main.py`
- Create: `tests/test_main.py`

- [ ] **Step 1: Write failing test**

Test `--dry-run --cycles 1` mode: mock all external APIs, verify pipeline runs one full cycle without errors, no alerts sent. Test graceful shutdown by triggering SIGINT during a mock run.

- [ ] **Step 2: Run test — verify failure**

- [ ] **Step 3: Implement main.py**

```python
async def run_cycle(settings: Settings, db: Database, session: aiohttp.ClientSession, dry_run: bool = False) -> dict:
    """Run one full pipeline cycle. Returns stats dict."""

async def main():
    """Entry point with CLI args parsing."""
```

- Parse `--dry-run` and `--cycles` CLI args with argparse
- Initialize structlog JSON logging
- Create aiohttp session, Database, Settings
- Main loop: `asyncio.gather()` for parallel ingestion (DexScreener + GeckoTerminal)
- Aggregate → enrich holders → score → gate → safety check → alert
- Heartbeat log every 5 minutes: tokens scanned, candidates promoted, alerts fired, MiroFish jobs today
- Graceful shutdown on SIGINT/SIGTERM via `asyncio.Event`
- Sleep `SCAN_INTERVAL_SECONDS` between cycles

- [ ] **Step 4: Run test — verify passes**

- [ ] **Step 5: Run smoke test**

```bash
uv run python -m scout.main --dry-run --cycles 1
```

Expected: runs one cycle, logs output, exits cleanly.

- [ ] **Step 6: Commit**

```bash
git add scout/main.py tests/test_main.py
git commit -m "feat: add main async pipeline loop with dry-run support"
```

---

## Task 18: README.md

**Files:**
- Create: `README.md`

- [ ] **Step 1: Write README.md**

Brief setup instructions:
- Prerequisites (Python 3.12, uv, Docker for MiroFish)
- Quick start: `cp .env.example .env`, edit values, `uv sync`, `uv run python -m scout.main`
- Docker: `docker compose up -d`
- Testing: `uv run pytest`
- Architecture overview (one-paragraph + stage list)

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add README with setup instructions"
```

---

## Task 19: Docker + Compose

**Files:**
- Create: `Dockerfile`
- Create: `docker-compose.yml`

- [ ] **Step 1: Write Dockerfile**

```dockerfile
FROM python:3.12-slim AS base
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv
RUN useradd -m -s /bin/bash scout
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --no-dev --frozen
COPY scout/ scout/
USER scout
CMD ["uv", "run", "python", "-m", "scout.main"]
```

- [ ] **Step 2: Write docker-compose.yml**

Two services: `mirofish` (build from cloned repo, port 5001) and `scout` (build from local Dockerfile, depends_on mirofish). Same network.

- [ ] **Step 3: Commit**

```bash
git add Dockerfile docker-compose.yml
git commit -m "chore: add Dockerfile and docker-compose for MiroFish + Scout"
```

---

## Task 20: Slash Commands + Sub-Agents

**Files:**
- Create: `.claude/commands/scan.md`
- Create: `.claude/commands/score.md`
- Create: `.claude/commands/backtest.md`
- Create: `.claude/commands/status.md`
- Create: `.claude/agents/ingestion-agent.md`
- Create: `.claude/agents/scorer-agent.md`
- Create: `.claude/agents/mirofish-agent.md`

- [ ] **Step 1: Write all 4 slash commands**

Content as specified in user prompt STEP 5.

- [ ] **Step 2: Write all 3 sub-agents**

Content as specified in user prompt STEP 5.

- [ ] **Step 3: Commit**

```bash
git add .claude/
git commit -m "chore: add slash commands and sub-agent definitions"
```

---

## Task 21: Final Verification

- [ ] **Step 1: Run full test suite**

```bash
uv run pytest --tb=short -q
```

All tests must pass.

- [ ] **Step 2: Run dry-run smoke test**

```bash
uv run python -m scout.main --dry-run --cycles 1
```

Must run without error.

- [ ] **Step 3: Verify config loads**

```bash
uv run python -c "from scout.config import Settings; s = Settings(TELEGRAM_BOT_TOKEN='t', TELEGRAM_CHAT_ID='c', ANTHROPIC_API_KEY='k'); print(s.model_dump())"
```

Must print config dict cleanly.

- [ ] **Step 4: Verify CLAUDE.md is ≤180 lines**

```bash
wc -l CLAUDE.md
```

- [ ] **Step 5: Verify .env.example has all config vars**

Cross-check every field in `Settings` class appears in `.env.example`.

- [ ] **Step 6: Verify all public functions have type hints**

Grep for `def ` in scout/ and verify signatures have annotations.

- [ ] **Step 7: Final commit if any fixes needed**

```bash
git add -A && git commit -m "fix: final verification fixes"
```
