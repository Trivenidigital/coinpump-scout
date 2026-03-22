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
from scout.ingestion.pool_watcher import watch_new_pools, new_pool_queue
from scout.alerter import send_alert
from scout.config import Settings
from scout.db import Database
from scout.gate import evaluate
from scout.ingestion.birdeye import fetch_trending_birdeye
from scout.ingestion.dexscreener import fetch_trending
from scout.ingestion.geckoterminal import fetch_trending_pools
from scout.ingestion.holder_enricher import enrich_holders
from scout.ingestion.onchain_signals import enrich_onchain_signals
from scout.ingestion.pumpfun import fetch_pumpfun_graduated
from scout.ingestion.smart_money_feed import fetch_smart_money_injections
from scout.ingestion.cryptopanic import enrich_news_sentiment
from scout.ingestion.social import enrich_social_sentiment
from scout.models import CandidateToken
from scout.quality_gate import QualityGate
from scout.safety import is_safe
from scout.scorer import score

logger = structlog.get_logger()

_last_injection_cleanup = datetime.min.replace(tzinfo=timezone.utc)


async def run_cycle(
    settings: Settings,
    db: Database,
    session: aiohttp.ClientSession,
    dry_run: bool = False,
) -> dict:
    """Run one full pipeline cycle.

    Returns stats dict with tokens_scanned, candidates_promoted, alerts_fired, etc.
    """
    global _last_injection_cleanup
    now_utc = datetime.now(timezone.utc)
    if (now_utc - _last_injection_cleanup).total_seconds() > 3600:
        try:
            deleted = await db.cleanup_old_injections()
            if deleted:
                logger.info("Cleaned up old injections", deleted=deleted)
            _last_injection_cleanup = now_utc
        except Exception as e:
            logger.warning("Injection cleanup failed", error=str(e))

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

    # Stage 1b: Smart money injections (Direction 2)
    try:
        sm_candidates = await fetch_smart_money_injections(session, db, settings)
        if sm_candidates:
            logger.info("Smart money feed injected", count=len(sm_candidates))
    except Exception as e:
        logger.warning("Smart money feed failed", error=str(e))
        sm_candidates = []

    # Stage 2: Aggregate
    all_candidates = aggregate(
        list(dex_tokens) + list(gecko_tokens) + list(birdeye_tokens) + list(pumpfun_tokens) + sm_candidates
    )[:settings.MAX_CANDIDATES_PER_CYCLE]

    # Processing lag monitor
    try:
        lag = await db.get_oldest_unprocessed_injection_age_seconds()
        if lag is not None and lag > 300:
            logger.warning("Smart money injections backing up", oldest_age_min=int(lag / 60))
    except Exception as e:
        logger.debug("Injection lag check failed", error=str(e))

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

    # Stage 2c: On-chain signal enrichment
    if settings.ONCHAIN_SIGNALS_ENABLED:
        for i, token in enumerate(enriched):
            enriched[i] = await enrich_onchain_signals(token, session, db, settings)

    # Data quality alert — catch dead signals
    dead_signals: list[str] = []
    total = len(enriched)
    if total > 0:
        has_holder_count = sum(1 for t in enriched if t.holder_count > 20)
        has_holder_growth = sum(1 for t in enriched if t.holder_growth_1h > 0)
        has_unique_buyers = sum(1 for t in enriched if t.unique_buyers_1h > 0)
        has_whale_buys = sum(1 for t in enriched if t.whale_buys > 0)

        if has_holder_count == 0:
            dead_signals.append("holder_count (all capped at 20)")
        if has_holder_growth == 0:
            dead_signals.append("holder_growth_1h")
        if has_unique_buyers == 0:
            dead_signals.append("unique_buyers_1h")
        if has_whale_buys == 0:
            dead_signals.append("whale_buys")

        if dead_signals:
            logger.warning(
                "Dead signals detected — scoring is degraded",
                dead_signals=dead_signals,
                tokens_checked=total,
            )

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

        if points >= settings.MIN_SCORE:
            # Promoted tokens get their snapshot in Stage 4-5 with narrative/conviction data
            scored.append((updated, signals))
            stats["candidates_promoted"] += 1
        else:
            # Non-promoted tokens: log snapshot now (they won't reach Stage 4-5)
            await db.log_signal_snapshot(
                scan_cycle=scan_cycle,
                token=updated,
                quant_score=points,
                signals_fired=signals,
                disqualified=disqualified,
                disqualify_reason=disqualify_reason,
            )
        # Batch commit: flush all high-frequency writes for this token in one round-trip.
        await db.commit()

    # Stage 3b: Quality gate (hard rejection filters before social/news enrichment)
    if scored:
        quality_gate = QualityGate(settings, db)
        quality_filtered = []
        for token, signals in scored:
            result = await quality_gate.evaluate(token)
            if result["pass"]:
                quality_filtered.append((token, signals))
            # else: already logged by quality gate
        scored = quality_filtered

    # Stage 3c: Social + news enrichment (only for tokens that passed quality gate)
    if scored:
        enriched_scored = []
        for token, signals in scored:
            token = await enrich_social_sentiment(token, session, settings)
            token = await enrich_news_sentiment(token, session, settings)
            await db.upsert_candidate(token)
            enriched_scored.append((token, signals))
        scored = enriched_scored

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
            await db.commit()
            continue

        # Stage 6: Safety check + alert
        token_safe = await is_safe(
            gated_token.contract_address, gated_token.chain, session,
            fail_closed=settings.GOPLUS_FAIL_CLOSED,
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
            await db.commit()
            continue

        # Dedup: skip if already alerted recently
        # Exception: high conviction + profitable exit + 20% dip = allow re-entry
        if await db.was_recently_alerted(gated_token.contract_address):
            should_skip = True

            if conviction >= settings.REENTRY_MIN_CONVICTION:
                last_exit = await db.get_last_alert_mcap(gated_token.contract_address)
                if last_exit is not None:
                    exit_mcap = last_exit.get("entry_price_usd", 0)
                    current_mcap = gated_token.market_cap_usd or 0
                    if exit_mcap > 0 and current_mcap > 0:
                        dip_pct = ((exit_mcap - current_mcap) / exit_mcap) * 100
                        if dip_pct >= settings.REENTRY_DIP_PCT:
                            should_skip = False
                            logger.info(
                                "Re-entry allowed: profitable exit + dip",
                                token=gated_token.token_name,
                                conviction=conviction,
                                dip_pct=f"{dip_pct:.1f}%",
                            )

            if should_skip:
                logger.info(
                    "Skipping duplicate alert",
                    token=gated_token.contract_address,
                    token_name=gated_token.token_name,
                )
                await db.log_signal_snapshot(
                    scan_cycle=scan_cycle, token=gated_token,
                    quant_score=gated_token.quant_score or 0,
                    signals_fired=signals,
                    narrative_score=gated_token.narrative_score,
                    conviction_score=conviction,
                    alerted=False,
                )
                await db.commit()
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
                gated_token.contract_address, gated_token.chain, conviction,
                market_cap_usd=gated_token.market_cap_usd or 0,
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
        await db.commit()

    # Log data quality stats in cycle output
    stats["dead_signals"] = len(dead_signals) if dead_signals else 0

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

    # Start pool watcher background task if enabled
    pool_watcher_task = None
    if settings.POOL_WATCHER_ENABLED:
        pool_watcher_task = asyncio.create_task(watch_new_pools(settings))
        logger.info("Pool watcher background task started")

    cycle_count = 0
    cumulative = {"tokens_scanned": 0, "candidates_promoted": 0, "alerts_fired": 0}
    heartbeat_interval = 5  # cycles between heartbeat logs
    try:
        async with aiohttp.ClientSession() as session:
            while not shutdown_event.is_set():
                # Drain new pool signatures from WebSocket watcher (infrastructure only)
                fresh_pools = []
                while not new_pool_queue.empty():
                    try:
                        fresh_pools.append(new_pool_queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                if fresh_pools:
                    logger.info("Fresh pools from WebSocket", count=len(fresh_pools))

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
        if pool_watcher_task is not None:
            pool_watcher_task.cancel()
            try:
                await pool_watcher_task
            except asyncio.CancelledError:
                pass
        await db.close()
        logger.info("Scanner stopped", cycles_completed=cycle_count)


if __name__ == "__main__":
    asyncio.run(main())
