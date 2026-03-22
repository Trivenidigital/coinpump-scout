"""Watch for new liquidity pool creation on Solana via WebSocket."""

import asyncio
import json

import structlog
import websockets

from scout.config import Settings

logger = structlog.get_logger()

# Raydium AMM program IDs
RAYDIUM_AMM_V4 = "675kPX9MHTjS2zt1qfr1NYHuzeLXfQM9H24wFSUt1Mp8"
RAYDIUM_CPMM = "CPMMoo8L3F4NbTegBCKVNunggL7H1ZpdTHKxQB5qKP1C"
METEORA_DAMM = "pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA"

# Store detected new pools
new_pool_queue: asyncio.Queue = asyncio.Queue(maxsize=100)


async def watch_new_pools(settings: Settings) -> None:
    """Connect to Solana WebSocket and watch for new pool creation.

    Subscribes to logs mentioning Raydium/Meteora program IDs.
    When a new pool is detected, pushes the transaction signature to new_pool_queue.
    """
    ws_url = settings.SOLANA_WS_URL

    while True:
        try:
            async with websockets.connect(ws_url) as ws:
                # Subscribe to logs mentioning Raydium/Meteora programs
                for program_id in [RAYDIUM_AMM_V4, RAYDIUM_CPMM, METEORA_DAMM]:
                    subscribe = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "logsSubscribe",
                        "params": [
                            {"mentions": [program_id]},
                            {"commitment": "confirmed"},
                        ],
                    }
                    await ws.send(json.dumps(subscribe))

                logger.info("WebSocket connected, watching for new pools")

                async for msg in ws:
                    try:
                        data = json.loads(msg)
                        if "params" not in data:
                            continue

                        logs = (
                            data.get("params", {})
                            .get("result", {})
                            .get("value", {})
                            .get("logs", [])
                        )
                        signature = (
                            data.get("params", {})
                            .get("result", {})
                            .get("value", {})
                            .get("signature", "")
                        )

                        # Look for pool initialization logs
                        for log in logs:
                            if "InitializeInstruction" in log or "initialize" in log.lower():
                                logger.info("New pool detected", signature=signature)
                                try:
                                    new_pool_queue.put_nowait(signature)
                                except asyncio.QueueFull:
                                    pass
                                break
                    except Exception:
                        continue

        except Exception as e:
            logger.warning("WebSocket disconnected, reconnecting in 5s", error=str(e))
            await asyncio.sleep(5)
