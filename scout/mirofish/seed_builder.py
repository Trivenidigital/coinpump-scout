"""Build MiroFish simulation seed payloads from CandidateToken data."""

from scout.models import CandidateToken
from scout.scorer import confidence


def build_seed(token: CandidateToken, signals_fired: list[str] | None = None) -> dict:
    """Build a simulation seed document for MiroFish.

    Returns a structured dict with token metadata and a formatted prompt
    string matching PRD Section 8.2 seed format.

    BL-015: Includes signal_confidence and signals_fired list to enrich
    the narrative simulation with quantitative context.
    """
    age_hours = int(token.token_age_days * 24)

    signals = signals_fired or []
    signal_confidence = confidence(signals)

    social_str = (
        f"{token.social_mentions_24h} mentions in 24h"
        if token.social_mentions_24h > 0
        else "no social data available"
    )
    community_str = ", ".join(filter(None, [
        "Twitter" if token.has_twitter else "",
        "Telegram" if token.has_telegram else "",
        "GitHub" if token.has_github else "",
    ])) or "no community links"
    news_str = (
        f"{token.news_mentions} news mentions, "
        f"{'bullish' if token.news_sentiment > 0.3 else 'neutral/bearish'} sentiment"
        if token.has_news
        else "no news coverage"
    )

    prompt = (
        f"Crypto token: '{token.token_name}' (ticker: {token.ticker}) "
        f"on {token.chain}.\n"
        f"Market cap: ${token.market_cap_usd:,.0f}. "
        f"Age: {age_hours}h old.\n"
        f"Community presence: {community_str}.\n"
        f"Social signals: {social_str}.\n"
        f"News: {news_str}.\n"
        f"On-chain signal strength: {signal_confidence} "
        f"({len(signals)} signals: {', '.join(signals) or 'none'}).\n\n"
        f"Score ONLY the NARRATIVE and MEME potential of this token's "
        f"NAME and CONCEPT. Ask yourself:\n"
        f"- Is '{token.token_name}' funny, creative, or culturally relevant?\n"
        f"- Does it tap into a current trend, meme, or community movement?\n"
        f"- Would crypto Twitter organically share or discuss this?\n"
        f"- Does the name have viral or community appeal?\n"
        f"Ignore on-chain signals entirely — score the NAME/CONCEPT only."
    )

    return {
        "token_name": token.token_name,
        "ticker": token.ticker,
        "chain": token.chain,
        "market_cap": token.market_cap_usd,
        "age_hours": age_hours,
        "community": community_str,
        "social": social_str,
        "news": news_str,
        "signal_confidence": signal_confidence,
        "signals_fired": signals,
        "prompt": prompt,
    }
