"""GoPlus Security API token safety check."""

import logging

import aiohttp

logger = logging.getLogger(__name__)

# GoPlus uses chain IDs: 1 = ethereum, 56 = bsc, etc.
# For named chains, they also accept the name directly.
CHAIN_ID_MAP = {
    "ethereum": "1",
    "base": "8453",
    "polygon": "137",
    "solana": "solana",
}

GOPLUS_BASE = "https://api.gopluslabs.io/api/v1/token_security"


async def is_safe(contract_address: str, chain: str, session: aiohttp.ClientSession) -> bool:
    """Check if a token is safe via GoPlus Security API.

    Returns True if:
    - honeypot = 0
    - is_blacklisted = 0
    - buy_tax < 10%
    - sell_tax < 10%

    On API failure: log warning, return True (fail open — don't block alerts).
    """
    chain_id = CHAIN_ID_MAP.get(chain, chain)
    url = f"{GOPLUS_BASE}/{chain_id}"

    try:
        async with session.get(url, params={"contract_addresses": contract_address}) as resp:
            if resp.status != 200:
                logger.warning("GoPlus API returned %d for %s", resp.status, contract_address)
                return True
            data = await resp.json()
    except (aiohttp.ClientError, Exception) as e:
        logger.warning("GoPlus API error for %s: %s", contract_address, e)
        return True

    result = data.get("result", {}).get(contract_address.lower(), {})
    if not result:
        # Also check without lowercasing for Solana addresses
        result = data.get("result", {}).get(contract_address, {})
    if not result:
        logger.warning("GoPlus: no result for %s", contract_address)
        return True

    if result.get("is_honeypot") == "1":
        return False
    if result.get("is_blacklisted") == "1":
        return False
    if float(result.get("buy_tax", "0") or "0") >= 0.10:
        return False
    if float(result.get("sell_tax", "0") or "0") >= 0.10:
        return False

    return True
