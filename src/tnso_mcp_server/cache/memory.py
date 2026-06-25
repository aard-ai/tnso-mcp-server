"""In-memory TTL cache (fast, per-process)."""

from __future__ import annotations

import logging
from typing import Any

from cachetools import TTLCache

logger = logging.getLogger(__name__)


class MemoryCache:
    """Process-local TTL + LRU cache backed by ``cachetools.TTLCache``."""

    def __init__(self, ttl: int = 300, max_size: int = 512) -> None:
        """Create a TTL cache holding up to ``max_size`` items for ``ttl`` seconds."""
        self._cache: TTLCache = TTLCache(maxsize=max_size, ttl=ttl)

    def get(self, key: str) -> Any | None:
        """Return the cached value, or ``None`` if missing or expired."""
        value = self._cache.get(key)
        logger.debug("Memory cache %s: %s", "HIT" if value is not None else "MISS", key)
        return value

    def set(self, key: str, value: Any) -> None:
        """Store ``value`` under ``key`` (evicting the LRU entry if full)."""
        self._cache[key] = value

    def delete(self, key: str) -> None:
        """Remove ``key`` if present; a no-op otherwise."""
        self._cache.pop(key, None)

    def clear(self) -> None:
        """Drop all cached entries."""
        self._cache.clear()
