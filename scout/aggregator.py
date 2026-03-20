"""Candidate token aggregation and deduplication."""

from scout.models import CandidateToken

# Numeric fields where we prefer the larger (nonzero) value when merging duplicates
_PREFER_MAX_FIELDS = [
    "buys_1h", "sells_1h", "volume_24h_usd", "liquidity_usd",
    "holder_count", "holder_growth_1h", "market_cap_usd",
    "social_mentions_24h", "unique_buyers_1h", "token_age_days",
    "smart_money_buys", "whale_buys", "whale_txns_1h", "dex_count",
    "news_mentions", "social_score",
]

# Boolean fields where True wins
_PREFER_TRUE_FIELDS = [
    "liquidity_locked", "volume_spike", "holder_gini_healthy",
    "has_twitter", "has_telegram", "has_github",
    "on_coingecko", "multi_dex", "has_news",
]


def aggregate(candidates: list[CandidateToken]) -> list[CandidateToken]:
    """Merge and deduplicate candidates by contract_address.

    When the same token appears from multiple sources, merge fields:
    - Numeric fields: prefer the larger nonzero value
    - Boolean fields: prefer True
    - String fields: prefer longer/nonempty from first occurrence
    """
    seen: dict[str, CandidateToken] = {}
    for token in candidates:
        key = token.contract_address
        if key not in seen:
            seen[key] = token
            continue

        existing = seen[key]
        updates: dict = {}

        for field in _PREFER_MAX_FIELDS:
            new_val = getattr(token, field)
            old_val = getattr(existing, field)
            if new_val > old_val:
                updates[field] = new_val

        for field in _PREFER_TRUE_FIELDS:
            if getattr(token, field) and not getattr(existing, field):
                updates[field] = True

        if len(token.token_name) > len(existing.token_name):
            updates["token_name"] = token.token_name
        if len(token.ticker) > len(existing.ticker):
            updates["ticker"] = token.ticker

        if token.volume_spike_ratio > existing.volume_spike_ratio:
            updates["volume_spike_ratio"] = token.volume_spike_ratio
        if abs(token.news_sentiment) > abs(existing.news_sentiment):
            updates["news_sentiment"] = token.news_sentiment

        if token.top3_wallet_concentration > 0 and existing.top3_wallet_concentration == 0:
            updates["top3_wallet_concentration"] = token.top3_wallet_concentration
        if token.deployer_supply_pct > 0 and existing.deployer_supply_pct == 0:
            updates["deployer_supply_pct"] = token.deployer_supply_pct
        if token.small_txn_ratio > 0 and existing.small_txn_ratio == 0:
            updates["small_txn_ratio"] = token.small_txn_ratio

        if updates:
            seen[key] = existing.model_copy(update=updates)

    return list(seen.values())
