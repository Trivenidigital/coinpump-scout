"""Smart money feed — ingestion source for tokens detected by tracked wallet buys.

Direction 2 of smart money integration: reads from injections.db
(written by sniper's copy_trader) and creates CandidateToken objects for the
full scout pipeline.

Uses a separate injections.db file to prevent cross-process SQLite corruption.
Sniper only does INSERT OR IGNORE (new rows), scout only does UPDATE SET processed=1.
"""

from collections import defaultdict

import aiohttp
import aiosqlite
import structlog

from scout.config import Settings
from scout.db import Database
from scout.models import CandidateToken

logger = structlog.get_logger()

DEXSCREENER_TOKENS_URL = "https://api.dexscreener.com/tokens/v1/solana"
_DEXSCREENER_BATCH_LIMIT = 30


async def fetch_smart_money_injections(
    session: aiohttp.ClientSession,
    db: Database,
    settings: Settings,
) -> list[CandidateToken]:
    """Read unprocessed smart money injections and create CandidateToken objects.

    Groups injections by token_mint, counts unique wallets per token,
    fetches metadata from DexScreener batch endpoint.

    Only marks injections as processed after successful DexScreener fetch,
    so failed ones are retried next cycle.

    Reads/writes to settings.INJECTIONS_DB_PATH (separate from scout's main DB).
    """
    inj_db_path = str(settings.INJECTIONS_DB_PATH)
    try:
        async with aiosqlite.connect(inj_db_path) as conn:
            conn.row_factory = aiosqlite.Row
            await conn.execute("PRAGMA busy_timeout=5000")

            cursor = await conn.execute(
                "SELECT id, token_mint, wallet_address FROM smart_money_injections WHERE processed = 0"
            )
            injections = [dict(r) for r in await cursor.fetchall()]
            if not injections:
                return []

            # Group by token_mint, collect IDs and unique wallets per mint
            token_wallets: dict[str, set[str]] = defaultdict(set)
            token_ids: dict[str, list[int]] = defaultdict(list)
            for inj in injections:
                token_wallets[inj["token_mint"]].add(inj["wallet_address"])
                token_ids[inj["token_mint"]].append(inj["id"])

            mints = list(token_wallets.keys())
            logger.info("Smart money injections to process", count=len(mints))

            # Batch fetch metadata from DexScreener (chunks of _DEXSCREENER_BATCH_LIMIT)
            dex_by_mint: dict[str, dict] = {}
            for i in range(0, len(mints), _DEXSCREENER_BATCH_LIMIT):
                batch = mints[i : i + _DEXSCREENER_BATCH_LIMIT]
                batch_url = f"{DEXSCREENER_TOKENS_URL}/{','.join(batch)}"
                try:
                    async with session.get(
                        batch_url,
                        timeout=aiohttp.ClientTimeout(total=15),
                    ) as resp:
                        if resp.status != 200:
                            logger.warning("DexScreener batch fetch failed", status=resp.status, batch_start=i)
                            continue
                        data = await resp.json()
                except Exception as e:
                    logger.warning("DexScreener fetch error", error=str(e), batch_start=i)
                    continue

                if not isinstance(data, list):
                    data = [data] if data else []

                for item in data:
                    addr = item.get("tokenAddress") or (item.get("baseToken") or {}).get("address", "")
                    if addr:
                        dex_by_mint[addr] = item

            # Build candidates and mark successfully fetched injections as processed
            candidates: list[CandidateToken] = []
            processed_ids: list[int] = []

            for mint, wallets in token_wallets.items():
                dex_data = dex_by_mint.get(mint)
                if not dex_data:
                    logger.debug("No DexScreener data for injected token", mint=mint[:20])
                    continue

                base_token = dex_data.get("baseToken") or {}
                info = dex_data.get("info") or {}
                name = base_token.get("name") or info.get("name", "Unknown")
                ticker = base_token.get("symbol") or info.get("symbol", "???")
                mcap = dex_data.get("marketCap", 0) or 0
                liq = (dex_data.get("liquidity") or {}).get("usd", 0) or 0
                vol = (dex_data.get("volume") or {}).get("h24", 0) or 0

                candidate = CandidateToken(
                    contract_address=mint,
                    chain="solana",
                    token_name=name,
                    ticker=ticker,
                    market_cap_usd=float(mcap),
                    liquidity_usd=float(liq),
                    volume_24h_usd=float(vol),
                    smart_money_buys=len(wallets),
                )
                candidates.append(candidate)
                processed_ids.extend(token_ids[mint])
                logger.info(
                    "Smart money injection -> candidate",
                    token=name,
                    ticker=ticker,
                    wallets=len(wallets),
                    mcap=mcap,
                )

            # Mark only successfully processed injections
            if processed_ids:
                placeholders = ",".join("?" for _ in processed_ids)
                await conn.execute(
                    f"UPDATE smart_money_injections SET processed = 1 WHERE id IN ({placeholders})",
                    processed_ids,
                )
                await conn.commit()

            return candidates
    except Exception as e:
        logger.warning("Injections DB read failed", error=str(e))
        return []
