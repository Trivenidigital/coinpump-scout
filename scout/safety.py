"""GoPlus Security API token safety check."""

import asyncio

import aiohttp
import structlog

logger = structlog.get_logger()

# GoPlus uses chain IDs: 1 = ethereum, 56 = bsc, etc.
# For named chains, they also accept the name directly.
CHAIN_ID_MAP = {
    "ethereum": "1",
    "base": "8453",
    "polygon": "137",
    "solana": "solana",
}

GOPLUS_BASE = "https://api.gopluslabs.io/api/v1/token_security"

# --- GoPlus flag differences: scout vs sniper ---
# Scout (pre-alert) checks:  is_honeypot, is_blacklisted, buy_tax >= 10%, sell_tax >= 10%
# Sniper (pre-buy) checks:   is_mintable, is_honeypot, can_take_back_ownership, transfer_pausable
#
# The scout focuses on trade-ability (honeypot, blacklist, tax) because it gates
# alerts — we don't want to surface tokens users can't trade.
# The sniper focuses on rug-pull vectors (mintable, ownership takeback, pausable)
# because it gates actual buys — it must block tokens that could rug post-purchase.
# See solana-sniper/sniper/safety.py _DANGER_FLAGS for the sniper's list.


async def is_safe(
    contract_address: str,
    chain: str,
    session: aiohttp.ClientSession,
    *,
    fail_closed: bool = True,
) -> bool:
    """Check if a token is safe via GoPlus Security API.

    Returns True if:
    - honeypot = 0
    - is_blacklisted = 0
    - buy_tax < 10%
    - sell_tax < 10%

    On network/HTTP error: return True (fail open — don't block alerts).
    On empty result (unknown token/chain): behaviour is controlled by fail_closed.
        fail_closed=True (default): return False — can't verify safety, block alert.
        fail_closed=False: return True — allow alert for tokens GoPlus hasn't indexed yet.
    """
    chain_id = CHAIN_ID_MAP.get(chain, chain)
    url = f"{GOPLUS_BASE}/{chain_id}"

    try:
        async with session.get(url, params={"contract_addresses": contract_address}) as resp:
            if resp.status != 200:
                logger.warning("GoPlus API returned error", status=resp.status, contract_address=contract_address)
                return True
            data = await resp.json()
    except (aiohttp.ClientError, asyncio.TimeoutError) as e:
        logger.warning("GoPlus API error", contract_address=contract_address, error=str(e))
        return True

    if not isinstance(data, dict):
        logger.warning("GoPlus: unexpected response format", contract_address=contract_address)
        return True
    result_map = data.get("result") or {}
    result = result_map.get(contract_address.lower(), {})
    if not result:
        # Also check without lowercasing for Solana addresses
        result = result_map.get(contract_address, {})
    if not result:
        # No data for this token — unsupported chain or unknown token
        logger.warning("GoPlus: no result for token", contract_address=contract_address, fail_closed=fail_closed)
        return not fail_closed  # True if fail_open, False if fail_closed

    if result.get("is_honeypot") == "1":
        return False
    if result.get("is_blacklisted") == "1":
        return False
    if float(result.get("buy_tax", "0") or "0") >= 0.10:
        return False
    if float(result.get("sell_tax", "0") or "0") >= 0.10:
        return False

    return True
