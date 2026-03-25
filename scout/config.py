"""Application configuration via Pydantic BaseSettings."""

from pathlib import Path

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Scanner
    SCAN_INTERVAL_SECONDS: int = 60
    MIN_SCORE: int = 30
    CONVICTION_THRESHOLD: int = 45
    QUANT_WEIGHT: float = 0.6
    NARRATIVE_WEIGHT: float = 0.4
    MAX_CANDIDATES_PER_CYCLE: int = 50

    # Token filters
    MIN_MARKET_CAP: float = 10_000
    MAX_MARKET_CAP: float = 500_000
    MAX_TOKEN_AGE_DAYS: int = 7
    MIN_VOL_LIQ_RATIO: float = 5.0
    MIN_LIQUIDITY_USD: float = 15_000
    CHAINS: list[str] = ["solana"]
    PUMPFUN_ENABLED: bool = True

    # Quality Gate
    QUALITY_GATE_ENABLED: bool = True
    MIN_QUANT_SCORE: int = 1
    MIN_VOL_ACCELERATION: float = 2.0
    MIN_UNIQUE_BUYERS: int = 10
    MAX_TOP3_CONCENTRATION: float = 40.0  # percentage
    MAX_DEPLOYER_SUPPLY_PCT: float = 20.0  # percentage
    MIN_TOKEN_AGE_MINUTES: int = 10  # Block tokens < 10min old (rug bait window)
    MAX_TOKEN_AGE_HOURS: int = 24
    MIN_HOLDER_GROWTH_PER_HOUR: int = 5

    # Whale detection
    SOL_PRICE_ESTIMATE_USD: float = 150.0
    WHALE_USD_THRESHOLD: float = 1_000.0

    # Well-known addresses (Solana)
    USDC_MINT_SOLANA: str = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"

    # Quant-only conviction: higher threshold when narrative is unavailable (M3)
    QUANT_ONLY_CONVICTION_THRESHOLD: int = 50

    # Alerts
    TELEGRAM_BOT_TOKEN: str
    TELEGRAM_CHAT_ID: str
    DISCORD_WEBHOOK_URL: str = ""

    # Re-entry settings
    REENTRY_MIN_CONVICTION: float = 40.0  # Min conviction to allow re-entry
    REENTRY_DIP_PCT: float = 20.0         # Must dip 20% from exit mcap

    # Missed trade recovery
    SNIPER_DB_PATH: str = "/opt/sniper/sniper.db"
    MISSED_TRADE_RECHECK_HOURS: int = 2   # Min hours before re-alerting missed trades

    # Birdeye (optional — Solana token data)
    BIRDEYE_API_KEY: str = ""

    # Holder enrichment (optional)
    HELIUS_API_KEY: str = ""
    MORALIS_API_KEY: str = ""

    # Social enrichment
    LUNARCRUSH_API_KEY: str = ""
    SOCIAL_ENRICHMENT_ENABLED: bool = True
    TWITTER_SCOUT_ENABLED: bool = True
    SOCIALDATA_API_KEY: str = ""

    # CryptoPanic news sentiment
    CRYPTOPANIC_API_KEY: str = ""

    # On-chain signal enrichment
    ONCHAIN_SIGNALS_ENABLED: bool = True
    SMART_MONEY_WALLETS: str = ""  # Comma-separated tracked wallet addresses
    SMART_MONEY_BOOST_CAP: int = 80  # Max total smart money score boost

    # Entry timing
    ENTRY_PEAK_PENALTY_ENABLED: bool = True
    ENTRY_MCAP_RUNUP_BLOCK: float = 300.0  # % 24h gain to block
    ENTRY_MCAP_RUNUP_CAP: float = 300_000  # mcap threshold for runup block

    # Safety
    GOPLUS_FAIL_CLOSED: bool = True  # False = fail open on unknown tokens (allows new tokens GoPlus hasn't indexed)

    # Solana WebSocket pool watcher
    SOLANA_WS_URL: str = "wss://api.mainnet-beta.solana.com"
    POOL_WATCHER_ENABLED: bool = False

    # Database
    DB_PATH: Path = Path("scout.db")
    INJECTIONS_DB_PATH: Path = Path("injections.db")
    SNIPER_DB_PATH: Path = Path("../solana-sniper/sniper.db")

    # Claude fallback
    ANTHROPIC_API_KEY: str

    @field_validator("CHAINS", mode="before")
    @classmethod
    def parse_chains(cls, v: str | list[str]) -> list[str]:
        if isinstance(v, str):
            return [c.strip() for c in v.split(",") if c.strip()]
        return v

    @model_validator(mode="after")
    def validate_weights_sum(self) -> "Settings":
        total = self.QUANT_WEIGHT + self.NARRATIVE_WEIGHT
        if abs(total - 1.0) > 1e-9:
            msg = f"QUANT_WEIGHT ({self.QUANT_WEIGHT}) + NARRATIVE_WEIGHT ({self.NARRATIVE_WEIGHT}) = {total}, must sum to 1.0"
            raise ValueError(msg)
        return self
