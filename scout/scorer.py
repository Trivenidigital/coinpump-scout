"""Quantitative scoring engine for candidate tokens.

Scoring weights (must always document rationale):
- vol_liq_ratio (>MIN_VOL_LIQ_RATIO): 30 points -- Primary pump precursor
- market_cap_tier (graduated $10K-$500K): 8/5/2 pts -- Sweet spot curve (BL-031)
- holder_growth (>20 new/hour): 25 points -- Organic accumulation
- token_age (bell curve, peak 1-3 days): 10 points -- Early stage optimal window
- social_mentions (>50 in 24h): 15 points -- CT discovery signal (optional)
- buy_pressure (buy_ratio > 65%): 15 points -- Wash trade discriminator (BL-011)
- score_velocity (10% above avg of prev 2 scans): 10 points -- Active accumulation (BL-013)
- unique_buyers (high relative to txns): 15 points -- Organic vs bot (BL-021)
- solana_bonus: 5 points -- Meme premium (BL-030)
- small_txn_ratio (>60% small txns): 5 points -- Organic distribution (BL-024)
- smart_money_buys: +20 per wallet (capped at SMART_MONEY_BOOST_CAP) -- Graduated alpha wallet boost
- whale_buys (>=3): 5 points -- Multiple large buyers (on-chain signal)
- liquidity_locked: 10 points -- Reduced rug risk (on-chain signal)
- volume_spike (>5x): 15 points -- Extreme volume anomaly (on-chain signal)
- volume_spike (>3x): 10 points -- Significant volume anomaly (on-chain signal)
- holder_gini_healthy: 5 points -- Healthy top-holder distribution (on-chain signal)
- whale_txns_1h (>=3): 5 points -- Multiple large SOL transactions (on-chain signal)
- has_twitter: 3 points -- Twitter/X presence via DexScreener socials or SocialData API
- has_telegram: 3 points -- Telegram community presence (legitimacy signal)
- has_github: 2 points -- GitHub repository presence (active development signal)
- on_coingecko: 8 points -- Listed on CoinGecko (strong CEX listing proxy)
- multi_dex (dex_count >= 2): 5 points -- Traded on multiple DEXs (liquidity depth)

Raw max: 224 points -> normalized to 0-100 scale (BL-016)
Co-occurrence multiplier applied after normalization (BL-014)

Hard disqualifiers:
- Liquidity < MIN_LIQUIDITY_USD -> score 0 (BL-010)
- Top-3 wallet concentration > 40% -> score 0 (BL-022)
- Deployer holds > 20% supply -> score 0 (BL-023)
"""

from scout.config import Settings
from scout.models import CandidateToken

# RAW_MAX_NO_HELIUS: achievable max without Helius signals.
# RAW_MAX_FULL: achievable max with all signals including Helius.
# Two-pass gating uses NO_HELIUS for pass 1, FULL for pass 2.
RAW_MAX_NO_HELIUS = 154
RAW_MAX_FULL = 224
RAW_MAX = RAW_MAX_NO_HELIUS  # Default for backwards compat


def score(
    token: CandidateToken,
    settings: Settings,
    previous_scores: list[int] | None = None,
    helius_available: bool = False,
) -> tuple[int, list[str]]:
    """Score a candidate token based on quantitative signals.

    Pure function -- no I/O.

    Args:
        token: The candidate token to score.
        settings: Application settings with scoring thresholds.
        previous_scores: Historical scores from prior scans (for velocity bonus).

    Returns:
        (score, signals_fired) where score is 0-100 and signals_fired
        is a list of signal names that contributed to the score.
    """
    # === Hard disqualifiers (fail fast, return score 0) ===

    # BL-010: Liquidity floor
    if token.liquidity_usd < settings.MIN_LIQUIDITY_USD:
        return (0, [])

    # BL-022: Wash trade detection -- top-3 wallet volume concentration > 40%
    if token.top3_wallet_concentration > 0.40:
        return (0, [])

    # BL-023: Deployer supply concentration > 20% (rug risk)
    if token.deployer_supply_pct > 0.20:
        return (0, [])

    points = 0
    signals: list[str] = []

    # Signal 1: Volume/Liquidity Ratio -- 30 points
    # Primary pump precursor: high volume relative to liquidity indicates
    # strong buying pressure that hasn't yet been reflected in price
    vol_liq_fired = False
    if token.liquidity_usd > 0:
        ratio = token.volume_24h_usd / token.liquidity_usd
        if ratio > settings.MIN_VOL_LIQ_RATIO:
            points += 30
            signals.append("vol_liq_ratio")
            vol_liq_fired = True

    # Signal 2: Market Cap Tier -- 8/5/2 points (BL-031: graduated curve)
    # $10K-$100K is peak score, tapers through $500K
    mcap_pts = _market_cap_tier_score(token.market_cap_usd, settings)
    if mcap_pts > 0:
        points += mcap_pts
        signals.append("market_cap_tier")

    # Signal 3: Holder Growth -- 25 points
    # Organic accumulation: new wallets acquiring the token indicates
    # genuine interest rather than wash trading
    holder_growth_fired = False
    if token.holder_growth_1h > 20:
        points += 25
        signals.append("holder_growth")
        holder_growth_fired = True

    # Signal 4: Token Age -- 10 points (BL-012: bell curve)
    # Peak window is 1-3 days; too early = no liquidity, too late = dead
    age_pts = _token_age_score(token.token_age_days)
    if age_pts > 0:
        points += age_pts
        signals.append("token_age")

    # Signal 5: Social Mentions -- 15 points (optional)
    # CT discovery signal: early social chatter before mainstream awareness
    if token.social_mentions_24h > 50:
        points += 15
        signals.append("social_mentions")

    # Signal 6: Buy Pressure Ratio -- 15 points (BL-011)
    # Best wash-trade discriminator from existing API data
    total_txns = token.buys_1h + token.sells_1h
    if total_txns > 0:
        buy_ratio = token.buys_1h / total_txns
        if buy_ratio > 0.65:
            points += 15
            signals.append("buy_pressure")

    # Signal 7: Score Velocity -- 10 points (BL-013)
    # Relaxed: last score must be 10% above average of previous 2 scans.
    # Old rule (strictly rising for 3 scans) fired too rarely in practice.
    if previous_scores and len(previous_scores) >= 2:
        recent = previous_scores[-1]
        avg_prev = sum(previous_scores[-3:-1]) / min(len(previous_scores[-3:-1]), 2)
        if recent > avg_prev * 1.1:  # 10% above recent average
            points += 10
            signals.append("score_velocity")

    # Signal 8: Unique Buyers -- 15 points (BL-021)
    # High unique buyer count relative to total txns = organic community buying
    # Fallback: if DexScreener buy/sell data missing, use absolute count (>= 20 unique buyers)
    if token.unique_buyers_1h > 0:
        if total_txns > 0:
            buyer_ratio = token.unique_buyers_1h / total_txns
            if buyer_ratio > 0.50:
                points += 15
                signals.append("unique_buyers")
        elif token.unique_buyers_1h >= 20:
            points += 15
            signals.append("unique_buyers")

    # Signal 9: Solana Chain Bonus -- 5 points (BL-030)
    # Meme premium: Solana has disproportionate meme coin activity
    if token.chain == "solana":
        points += 5
        signals.append("solana_bonus")

    # Signal 10: Small Transaction Ratio -- 5 points (BL-024)
    # Organic pre-pump = many small txns. Bot wash = fewer large uniform txns.
    if token.small_txn_ratio > 0.60:
        points += 5
        signals.append("small_txn_ratio")

    # Signal 11: Smart Money Buys -- +20 per wallet, capped (on-chain signal)
    # Graduated boost: more tracked wallets buying = higher confidence.
    # Cap controlled by SMART_MONEY_BOOST_CAP setting.
    if token.smart_money_buys > 0:
        sm_boost = min(token.smart_money_buys * 20, settings.SMART_MONEY_BOOST_CAP)
        points += sm_boost
        signals.append("smart_money_buys")

    # Signal 12: Whale Buys -- 5 points (on-chain signal)
    # Multiple large buyers (>=$1K) suggest strong conviction from big wallets
    if token.whale_buys >= 3:
        points += 5
        signals.append("whale_buys")

    # Signal 13: Liquidity Locked -- 10 points (on-chain signal)
    # Locked/burned LP greatly reduces rug-pull risk
    if token.liquidity_locked:
        points += 10
        signals.append("liquidity_locked")

    # Signal 14: Volume Spike -- 10/15 points (on-chain signal)
    # Abnormally high volume vs historical average signals breakout momentum.
    # >5x is extreme (15 pts), >3x is significant (10 pts). Mutually exclusive.
    if token.volume_spike:
        if token.volume_spike_ratio > 5.0:
            points += 15
            signals.append("volume_spike_5x")
        elif token.volume_spike_ratio > 3.0:
            points += 10
            signals.append("volume_spike_3x")

    # Signal 15: Holder Distribution Health -- 5 points (on-chain signal)
    # Top-5 holders owning < 30% of top-20 total indicates broad distribution,
    # reducing rug-pull risk and suggesting organic community accumulation.
    if token.holder_gini_healthy:
        points += 5
        signals.append("holder_gini_healthy")

    # Signal 16: Whale Transactions -- 5 points (on-chain signal)
    # Multiple large (> 1 SOL) transactions in the last hour indicate strong
    # conviction buying from well-capitalised wallets on a microcap token.
    if token.whale_txns_1h >= 3:
        points += 5
        signals.append("whale_txns_1h")

    # Signal 17: Twitter/X Presence -- 3 points (social presence)
    # Having an active Twitter account linked in DexScreener or detected via
    # SocialData API indicates community engagement and marketing effort.
    if token.has_twitter:
        points += 3
        signals.append("has_twitter")

    # Signal 18: Telegram Presence -- 3 points (social presence)
    # A Telegram community group signals active community management and
    # reduces probability of being a silent rug-pull.
    if token.has_telegram:
        points += 3
        signals.append("has_telegram")

    # Signal 19: GitHub Presence -- 2 points (development activity)
    # Having a GitHub repository suggests active development, which
    # distinguishes legitimate projects from pure meme/pump tokens.
    if token.has_github:
        points += 2
        signals.append("has_github")

    # Signal 20: CoinGecko Listing -- 8 points (strong legitimacy signal)
    # Being listed on CoinGecko strongly correlates with CEX listings and
    # broader market visibility. One of the strongest legitimacy indicators.
    if token.on_coingecko:
        points += 8
        signals.append("on_coingecko")

    # Signal 21: Multi-DEX Listing -- 5 points (liquidity depth)
    # Traded on 2+ DEXs (detected via Jupiter route plans) indicates deeper
    # liquidity, broader market access, and healthier token ecosystem.
    if token.multi_dex and token.dex_count >= 2:
        points += 5
        signals.append("multi_dex")

    # Signal 22: CryptoPanic news mentions -- 7 points (narrative momentum)
    # Token appearing in crypto news indicates growing narrative attention.
    if token.has_news and token.news_mentions >= 1:
        points += 7
        signals.append("has_news")

    # Signal 23: Bullish news sentiment -- 8 points (positive narrative)
    # Bullish sentiment in news is a strong forward indicator.
    if token.news_sentiment > 0.3:
        points += 8
        signals.append("bullish_news")

    # BL-016: Normalize raw sum to 0-100 scale
    denominator = RAW_MAX_FULL if helius_available else RAW_MAX_NO_HELIUS
    normalized = min(100, int(points * 100 / denominator))

    # BL-014: Co-occurrence multiplier
    # Vol/liq alone is the most commonly gamed signal. Penalize when isolated.
    if vol_liq_fired and holder_growth_fired:
        normalized = min(100, int(normalized * 1.2))
    elif vol_liq_fired and not holder_growth_fired:
        normalized = int(normalized * 0.8)

    return (normalized, signals)


def confidence(signals: list[str]) -> str:
    """Return signal confidence level based on number of tiers firing (BL-015).

    HIGH if 3+ signals firing, MEDIUM if 2, LOW if 1 or 0.
    """
    count = len(signals)
    if count >= 3:
        return "HIGH"
    elif count == 2:
        return "MEDIUM"
    return "LOW"


def _token_age_score(age_days: float) -> int:
    """Bell curve scoring for token age (BL-012).

    0 pts for < 12h (0.5 days)
    5 pts for 12-24h (0.5-1 days)
    10 pts for 1-3 days (peak window)
    5 pts for 3-5 days
    0 pts for > 5 days
    """
    if age_days < 0.5:
        return 0
    elif age_days < 1.0:
        return 5
    elif age_days <= 3.0:
        return 10
    elif age_days <= 5.0:
        return 5
    return 0


def _market_cap_tier_score(market_cap_usd: float, settings: Settings) -> int:
    """Graduated market cap scoring (BL-031).

    8 pts for $10K-$100K (peak discovery zone)
    5 pts for $100K-$250K (growing but still early)
    2 pts for $250K-$500K (late but possible)
    0 pts outside range
    """
    if market_cap_usd < settings.MIN_MARKET_CAP:
        return 0
    elif market_cap_usd <= 100_000:
        return 8
    elif market_cap_usd <= 250_000:
        return 5
    elif market_cap_usd <= settings.MAX_MARKET_CAP:
        return 2
    return 0
