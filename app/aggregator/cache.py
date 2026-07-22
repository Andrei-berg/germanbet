from __future__ import annotations

from datetime import datetime, timezone, timedelta

_cache: dict[str, tuple[object, datetime]] = {}


def get(key: str) -> object | None:
    entry = _cache.get(key)
    if entry is None:
        return None
    value, expires = entry
    if datetime.now(timezone.utc) > expires:
        del _cache[key]
        return None
    return value


def set(key: str, value: object, ttl_minutes: int = 30) -> None:
    expires = datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes)
    _cache[key] = (value, expires)


def clear() -> None:
    _cache.clear()
