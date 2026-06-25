"""Persistent disk cache (survives restarts)."""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

from diskcache import Cache

logger = logging.getLogger(__name__)


class PersistentCache:
    """Disk-backed cache (``diskcache.Cache``) that survives process restarts."""

    def __init__(self, cache_dir: str = "./cache") -> None:
        """Open the cache at ``cache_dir``, falling back to a temp dir if unwritable."""
        self.cache_dir = cache_dir
        try:
            os.makedirs(cache_dir, exist_ok=True)
            self._cache = Cache(cache_dir)
        except (OSError, PermissionError):
            fallback = os.path.join(tempfile.gettempdir(), "tnso_mcp_cache")
            logger.warning("Cache dir %s not writable; falling back to %s.", cache_dir, fallback)
            self.cache_dir = fallback
            os.makedirs(fallback, exist_ok=True)
            self._cache = Cache(fallback)

    def get(self, key: str) -> Any | None:
        """Return the cached value, or ``None`` if missing or expired."""
        return self._cache.get(key)

    def set(self, key: str, value: Any, ttl: int | None = None) -> None:
        """Store ``value`` under ``key``, expiring after ``ttl`` seconds if given."""
        self._cache.set(key, value, expire=ttl)

    def delete(self, key: str) -> None:
        """Remove ``key`` from the cache."""
        self._cache.delete(key)

    def clear(self) -> None:
        """Drop all cached entries."""
        self._cache.clear()

    def close(self) -> None:
        """Close the underlying disk cache (release file handles)."""
        self._cache.close()

    # --- diagnostics ---
    def keys(self) -> list[str]:
        """Return all cache keys (diagnostics only — may be large)."""
        return list(self._cache.iterkeys())

    def size(self) -> int:
        """Return the number of entries currently stored."""
        return len(self._cache)
