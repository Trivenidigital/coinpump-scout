"""Data ingestion sources for CoinPump Scout."""

from scout.ingestion.birdeye import fetch_trending_birdeye
from scout.ingestion.dexscreener import fetch_trending
from scout.ingestion.geckoterminal import fetch_trending_pools
from scout.ingestion.holder_enricher import enrich_holders

__all__ = [
    "fetch_trending",
    "fetch_trending_birdeye",
    "fetch_trending_pools",
    "enrich_holders",
]
