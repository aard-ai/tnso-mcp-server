"""CLI: look up a TNSO SDMX concept id and print JSON.

Run standalone or (as the get_concepts tool does) via a subprocess:
    python -m tnso_mcp_server.cli.get_concepts_cli POP_IND
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

from dotenv import load_dotenv

from ..api.client import DEFAULT_AGENCY, DEFAULT_BASE_URL, ApiClient
from ..cache.manager import CacheManager
from ..cache.memory import MemoryCache
from ..cache.persistent import PersistentCache
from ..tools.helpers import configure_cache_ttls, get_cached_conceptschemes


async def _run(concept_id: str) -> dict:
    """Search every TNSO concept scheme for ``concept_id`` and return a result dict."""
    load_dotenv()
    configure_cache_ttls(
        int(os.getenv("DATAFLOWS_CACHE_TTL_SECONDS", "604800")),
        int(os.getenv("METADATA_CACHE_TTL_SECONDS", "2592000")),
        int(os.getenv("OBSERVED_DATA_CACHE_TTL_SECONDS", "86400")),
    )
    memory = MemoryCache(
        ttl=int(os.getenv("MEMORY_CACHE_TTL_SECONDS", "300")),
        max_size=int(os.getenv("MAX_MEMORY_CACHE_ITEMS", "512")),
    )
    persistent = PersistentCache(cache_dir=os.getenv("PERSISTENT_CACHE_DIR", "./cache"))
    cache = CacheManager(memory, persistent)
    api = ApiClient(
        base_url=os.getenv("TNSO_API_BASE_URL", DEFAULT_BASE_URL),
        agency=os.getenv("TNSO_AGENCY", DEFAULT_AGENCY),
        timeout=float(os.getenv("API_TIMEOUT_SECONDS", "30")),
        max_retries=int(os.getenv("API_MAX_RETRIES", "3")),
    )
    try:
        schemes = await get_cached_conceptschemes(cache, api)
        for scheme in schemes:
            for concept in scheme.concepts:
                if concept.id == concept_id:
                    return {
                        "concept_id": concept_id,
                        "found": True,
                        "name_en": concept.name_en,
                        "name_th": concept.name_th,
                        "scheme_id": scheme.id,
                    }
        return {"concept_id": concept_id, "found": False}
    finally:
        await api.aclose()
        cache.close()


def main() -> None:
    """Parse the concept id argument, run the lookup, and print the result as JSON."""
    parser = argparse.ArgumentParser(description="Look up a TNSO SDMX concept by id.")
    parser.add_argument("concept_id", help="Concept id, e.g. POP_IND")
    args = parser.parse_args()
    result = asyncio.run(_run(args.concept_id))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
