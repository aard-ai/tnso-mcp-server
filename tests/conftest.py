from __future__ import annotations

import pytest

from tnso_mcp_server.cache.manager import CacheManager
from tnso_mcp_server.cache.memory import MemoryCache
from tnso_mcp_server.cache.persistent import PersistentCache


@pytest.fixture
def cache_manager(tmp_path):
    """A real two-layer CacheManager backed by a temp dir (avoids shadowing pytest's
    built-in ``cache`` fixture)."""
    manager = CacheManager(
        MemoryCache(ttl=60, max_size=100),
        PersistentCache(cache_dir=str(tmp_path / "cache")),
    )
    yield manager
    manager.close()
