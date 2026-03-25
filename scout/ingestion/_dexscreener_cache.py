"""Simple TTL cache for DexScreener token responses."""
import time

_cache: dict[str, tuple[list, float]] = {}
_TTL = 60


def get_cached(contract: str) -> list | None:
    entry = _cache.get(contract)
    if entry and (time.monotonic() - entry[1]) < _TTL:
        return entry[0]
    return None


def set_cached(contract: str, data: list) -> None:
    _cache[contract] = (data, time.monotonic())
