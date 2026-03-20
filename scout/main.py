"""CoinPump Scout -- main pipeline entry point."""

import argparse
import asyncio
import logging
import signal
import sys
from datetime import datetime, timezone

import aiohttp
import structlog

from scout.aggregator import aggregate
from scout.alerter import send_alert
from scout.config import Settings
from scout.db import Database
from scout.gate import evaluate
from scout.ingestion.birdeye import fetch_trending_birdeye
from scout.ingestion.dexscreener import fetch_trending
from scout.ingestion.geckoterminal import fetch_trending_pools
from scout.ingestion.holder_enricher import enrich_holders
from scout.ingestion.pumpfun import fetch_pumpfun_graduated
from scout.models import CandidateToken
from scout.safety import is_safe
from scout.scorer import score

logger = structlog.get_logger()


async def run_cycle(
    settings: Settings,
    db: Database,
    session: aiohttp.ClientSession,
    dry_run: bool = False,
) -> dict:
    """Run one full pipeline cycle.

    Returns stats dict with tokens_scanned, candidates_promoted, alerts_fired, etc.
    """
    stats = {"tokens_scanned": 0, "candidates_promoted": 0, "alerts_fired": 0}
    scan_cycle = int(datetime.now(timezone.utc).timestamp())

    # Stage 1: Parallel ingestion
    ingestion_coros = [
        fetch_trending(session, settings),
        fetch_trending_pools(session, settings),
        fetch_trending_birdeye(session, settings),
    ]
    if settings.PUMPFUN_ENABLED:
        ingestion_coros.append(fetch_pumpfun_graduated(session, settings))

    ingestion_results = await asyncio.gather(
        *ingestion_coros, return_exceptions=True,
    )

    # Unpack results (positional)
    dex_tokens = ingestion_results[0]
    gecko_tokens = ingestion_results[1]
    birdeye_tokens = ingestion_results[2]
    pumpfun_tokens: list[CandidateToken] | Exception = (
        ingestion_results[3] if settings.PUMPFUN_ENABLED else []
    )

    # Handle exceptions from gather
    if isinstance(dex_tokens, Exception):
        logger.warning("DexScreener ingestion failed", error=str(dex_tokens))
        dex_tokens = []
    if isinstance(gecko_tokens, Exception):
        logger.warning("GeckoTerminal ingestion failed", error=str(gecko_tokens))
        gecko_tokens = []
    if isinstance(birdeye_tokens, Exception):
        logger.warning("Birdeye ingestion failed", error=str(birdeye_tokens))
        birdeye_tokens = []
    if isinstance(pumpfun_tokens, Exception):
        logger.warning("PumpFun ingestion failed", error=str(pumpfun_tokens))
        pumpfun_tokens = []

    # Stage 2: Aggregate
    all_candidates = aggregate(
        list(dex_tokens) + list(gecko_tokens) + list(birdeye_tokens) + list(pumpfun_tokens)
    )
    stats["tokens_scanned"] = len(all_candidates)

    # Enrich holders sequentially to respect Helius rate limits
    enriched = []
    for token in all_candidates:
        enriched.append(await enrich_holders(token, session, settings))

    # BL-020: Compute holder_growth_1h from previous snapshots
    for i, token in enumerate(enriched):
        if token.holder_count > 0:
            prev = await db.get_previous_holder_count(token.contract_address)
            await db.log_holder_snapshot(token.contract_address, token.holder_count)
            if prev is not None:
                growth = token.holder_count - prev
                enriched[i] = token.model_copy(update={"holder_growth_1h": max(0, growth)})

    # Stage 3: Score
    scored = []
    for token in enriched:
        previous_scores = await db.get_recent_scores(token.contract_address)
        points, signals = score(token, settings, previous_scores=previous_scores)
        await db.log_score(token.contract_address, points)
        updated = token.model_copy(update={"quant_score": points})
        await db.upsert_candidate(updated)

        # Determine disqualification reason
        disqualified = points == 0
        disqualify_reason = None
        if disqualified:
            if token.liquidity_usd < settings.MIN_LIQUIDITY_USD:
                disqualify_reason = f"liquidity_below_{settings.MIN_LIQUIDITY_USD}"
            elif token.top3_wallet_concentration > 0.40:
                disqualify_reason = f"wash_trade_concentration_{token.top3_wallet_concentration:.2f}"
            elif token.deployer_supply_pct > 0.20:
                disqualify_reason = f"deployer_supply_{token.deployer_supply_pct:.2f}"

        # Log snapshot for every token (for analysis)
        await db.log_signal_snapshot(
            scan_cycle=scan_cycle,
            token=updated,
            quant_score=points,
            signals_fired=signals,
            disqualified=disqualified,
            disqualify_reason=disqualify_reason,
        )

        if points >= settings.MIN_SCORE:
            scored.append((updated, signals))
            stats["candidates_promoted"] += 1

    # Stages 4-5: Gate (MiroFish + conviction)
    for token, signals in scored:
        should_alert, conviction, gated_token = await evaluate(
            token, db, session, settings, signals_fired=signals,
        )

        if not should_alert:
            # Update snapshot with narrative/conviction even if not alerting
            await db.log_signal_snapshot(
                scan_cycle=scan_cycle, token=gated_token,
                quant_score=gated_token.quant_score or 0,
                signals_fired=signals,
                narrative_score=gated_token.narrative_score,
                conviction_score=conviction,
                alerted=False,
            )
            continue

        # Stage 6: Safety check + alert
        token_safe = await is_safe(
            gated_token.contract_address, gated_token.chain, session
        )
        if not token_safe:
            logger.warning(
                "Token failed safety check", token=gated_token.contract_address
            )
            await db.log_signal_snapshot(
                scan_cycle=scan_cycle, token=gated_token,
                quant_score=gated_token.quant_score or 0,
                signals_fired=signals,
                narrative_score=gated_token.narrative_score,
                conviction_score=conviction,
                alerted=False, safe=False,
            )
            continue

        if dry_run:
            logger.info(
                "DRY RUN: would alert",
                token=gated_token.token_name,
                conviction=conviction,
            )
        else:
            await send_alert(gated_token, signals, session, settings)
            await db.log_alert(
                gated_token.contract_address, gated_token.chain, conviction
            )
            stats["alerts_fired"] += 1

        # Log full snapshot for alerted tokens
        await db.log_signal_snapshot(
            scan_cycle=scan_cycle, token=gated_token,
            quant_score=gated_token.quant_score or 0,
            signals_fired=signals,
            narrative_score=gated_token.narrative_score,
            conviction_score=conviction,
            alerted=True, safe=True,
        )

    return stats


async def main() -> None:
    """Main entry point with CLI arg parsing and graceful shutdown."""
    parser = argparse.ArgumentParser(description="CoinPump Scout scanner")
    parser.add_argument(
        "--dry-run", action="store_true", help="Run without sending alerts"
    )
    parser.add_argument(
        "--cycles", type=int, default=0, help="Number of cycles (0=infinite)"
    )
    args = parser.parse_args()

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.add_log_level,
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
    )

    settings = Settings()
    db = Database(settings.DB_PATH)
    await db.initialize()

    shutdown_event = asyncio.Event()

    def _shutdown(sig, frame):
        logger.info("Shutdown signal received", signal=sig)
        shutdown_event.set()

    # Register signal handlers for graceful shutdown
    signal.signal(signal.SIGINT, _shutdown)
    try:
        signal.signal(signal.SIGTERM, _shutdown)
    except (OSError, ValueError):
        pass  # SIGTERM not supported on Windows

    cycle_count = 0
    cumulative = {"tokens_scanned": 0, "candidates_promoted": 0, "alerts_fired": 0}
    heartbeat_interval = 5  # cycles between heartbeat logs
    try:
        async with aiohttp.ClientSession() as session:
            while not shutdown_event.is_set():
                try:
                    stats = await run_cycle(
                        settings, db, session, dry_run=args.dry_run
                    )
                    logger.info("Cycle complete", **stats)
                    for k in cumulative:
                        cumulative[k] += stats.get(k, 0)
                except Exception as e:
                    logger.error("Cycle failed", error=str(e))

                cycle_count += 1

                # BL-033: Heartbeat logging every N cycles
                if cycle_count % heartbeat_interval == 0:
                    mirofish_today = await db.get_daily_mirofish_count()
                    logger.info(
                        "Heartbeat",
                        cycles_completed=cycle_count,
                        mirofish_jobs_today=mirofish_today,
                        **cumulative,
                    )

                if args.cycles > 0 and cycle_count >= args.cycles:
                    break

                # Wait for next cycle or shutdown
                try:
                    await asyncio.wait_for(
                        shutdown_event.wait(),
                        timeout=settings.SCAN_INTERVAL_SECONDS,
                    )
                except asyncio.TimeoutError:
                    pass  # Normal -- interval elapsed
    finally:
        await db.close()
        logger.info("Scanner stopped", cycles_completed=cycle_count)


if __name__ == "__main__":
    asyncio.run(main())
