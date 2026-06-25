import asyncio

from tnso_mcp_server.cache.memory import MemoryCache


def test_memory_set_get():
    m = MemoryCache(ttl=60, max_size=10)
    m.set("a", 1)
    assert m.get("a") == 1
    assert m.get("missing") is None


async def test_get_or_fetch_caches(cache_manager):
    calls = {"n": 0}

    async def fetch():
        calls["n"] += 1
        return {"v": 1}

    first = await cache_manager.get_or_fetch("k", fetch, persistent_ttl=60)
    second = await cache_manager.get_or_fetch("k", fetch, persistent_ttl=60)
    assert first == second == {"v": 1}
    assert calls["n"] == 1  # second call served from cache


async def test_get_or_fetch_single_flight(cache_manager):
    calls = {"n": 0}

    async def slow_fetch():
        calls["n"] += 1
        await asyncio.sleep(0.05)
        return {"v": 1}

    # Five concurrent misses on the same key must collapse to a single fetch.
    results = await asyncio.gather(
        *[cache_manager.get_or_fetch("k", slow_fetch, persistent_ttl=60) for _ in range(5)]
    )
    assert all(r == {"v": 1} for r in results)
    assert calls["n"] == 1
