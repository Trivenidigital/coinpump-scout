"""Quantitative scoring engine for candidate tokens.

Scoring weights (must always document rationale):
- vol_liq_ratio (>MIN_VOL_LIQ_RATIO): 30 points -- Primary pump precursor
- market_cap_range (MIN-MAX_MARKET_CAP): 20 points -- Pre-discovery range
- holder_growth (>20 new/hour): 25 points -- Organic accumulation
- token_age (bell curve, peak 1-3 days): 10 points -- Early stage optimal window
- social_mentions (>50 in 24h): 15 points -- CT discovery signal (optional)
- buy_pressure (buy_ratio > 65%): 15 points -- Wash trade discriminator (BL-011)
- score_velocity (rising across 3 scans): 10 points -- Active accumulation (BL-013)

Raw max: 125 points -> normalized to 0-100 scale (BL-016)
Co-occurrence multiplier applied after normalization (BL-014)

Hard disqualifiers (BL-010):
- Liquidity < MIN_LIQUIDITY_USD -> score 0, skip all signals
"""

from scout.config import Settings
from scout.models import CandidateToken

RAW_MAX = 125


def score(
    token: CandidateToken,
    settings: Settings,
    previous_scores: list[int] | None = None,
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
    # BL-010: Hard disqualifier -- liquidity floor
    if token.liquidity_usd < settings.MIN_LIQUIDITY_USD:
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

    # Signal 2: Market Cap Range -- 20 points
    # Pre-discovery sweet spot: large enough to have real liquidity,
    # small enough to have significant upside potential
    if settings.MIN_MARKET_CAP <= token.market_cap_usd <= settings.MAX_MARKET_CAP:
        points += 20
        signals.append("market_cap_range")

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
    # 0 pts for < 12h, 5 pts for 12-24h, 10 pts for 1-3d, 5 pts for 3-5d, 0 pts for > 5d
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
    # DexScreener provides txns.h1.buys and txns.h1.sells
    total_txns = token.buys_1h + token.sells_1h
    if total_txns > 0:
        buy_ratio = token.buys_1h / total_txns
        if buy_ratio > 0.65:
            points += 15
            signals.append("buy_pressure")

    # Signal 7: Score Velocity -- 10 points (BL-013)
    # Rising score across consecutive scans indicates active accumulation
    if previous_scores and len(previous_scores) >= 3:
        last_3 = previous_scores[-3:]
        if last_3[0] < last_3[1] < last_3[2]:
            points += 10
            signals.append("score_velocity")

    # BL-016: Normalize raw sum to 0-100 scale
    normalized = min(100, int(points * 100 / RAW_MAX))

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
