"""Two-layer cache facade: memory first, then disk."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from .memory import MemoryCache
from .persistent import PersistentCache

logger = logging.getLogger(__name__)


class CacheManager:
    """Two-layer cache: read memory first, fall back to disk, promote on hit."""

    def __init__(self, memory: MemoryCache, persistent: PersistentCache) -> None:
        """Wrap an in-memory and a persistent cache as a single facade."""
        self.memory = memory
        self.persistent = persistent
        # Single-flight: collapse concurrent misses on the same key onto one fetch.
        self._inflight: dict[str, asyncio.Task[Any]] = {}
        self._inflight_lock = asyncio.Lock()

    def get(self, key: str) -> Any | None:
        """Return the cached value, promoting a disk hit back into memory."""
        value = self.memory.get(key)
        if value is not None:
            return value
        value = self.persistent.get(key)
        if value is not None:
            self.memory.set(key, value)  # promote into memory
        return value

    def set(self, key: str, value: Any, persistent_ttl: int | None = None) -> None:
        """Write a value to both layers; ``persistent_ttl`` bounds the disk copy."""
        self.memory.set(key, value)
        self.persistent.set(key, value, ttl=persistent_ttl)

    def delete(self, key: str) -> None:
        """Remove a key from both cache layers."""
        self.memory.delete(key)
        self.persistent.delete(key)

    def clear(self) -> None:
        """Empty both cache layers."""
        self.memory.clear()
        self.persistent.clear()

    async def get_or_fetch(
        self,
        key: str,
        fetch_func: Callable[[], Awaitable[Any]],
        persistent_ttl: int | None = None,
    ) -> Any:
        """Return the cached value, or call ``fetch_func`` and cache its result.

        Single-flight: if several callers miss the same key concurrently, only the
        first runs ``fetch_func``; the rest await that same result, so we never fan
        a burst of identical requests out to the upstream API (and trip its rate limit).
        """
        value = self.get(key)
        if value is not None:
            return value

        async with self._inflight_lock:
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.ensure_future(fetch_func())
                self._inflight[key] = task

        try:
            value = await task
        finally:
            async with self._inflight_lock:
                if self._inflight.get(key) is task:
                    self._inflight.pop(key, None)

        if value is not None:
            self.set(key, value, persistent_ttl=persistent_ttl)
        return value

    def close(self) -> None:
        """Release the persistent cache's underlying resources."""
        self.persistent.close()
