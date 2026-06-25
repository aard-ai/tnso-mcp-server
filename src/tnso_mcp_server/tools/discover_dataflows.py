"""Tool: discover_dataflows — search TNSO dataflows by keyword."""

from __future__ import annotations

import logging
from typing import Any

from mcp.types import TextContent

from ..api.client import ApiClient
from ..api.models import DiscoverDataflowsInput
from ..cache.manager import CacheManager
from ..utils.blacklist import DataflowBlacklist
from ..utils.validators import validate_keywords
from .helpers import format_json_response, get_cached_availability, get_cached_dataflows

logger = logging.getLogger(__name__)


async def handle_discover_dataflows(
    arguments: dict[str, Any],
    cache: CacheManager,
    api: ApiClient,
    blacklist: DataflowBlacklist,
) -> list[TextContent]:
    """Return dataflows matching the keywords (blacklisted ones excluded).

    Any keyword suffices by default (OR); pass ``match_all`` to require every
    keyword (AND).
    """
    params = DiscoverDataflowsInput.model_validate(arguments or {})
    keywords = validate_keywords(params.keywords)

    dataflows = await get_cached_dataflows(cache, api)
    dataflows = blacklist.filter_dataflows(dataflows)

    if keywords:
        # OR across keywords by default; AND when match_all is set. Either way a
        # single keyword is OR-ed across the dataflow's fields (the haystack).
        reducer = all if params.match_all else any

        def matches(df) -> bool:
            """True if the keywords match the dataflow's searchable text."""
            haystack = " ".join(
                [df.id, df.name_en, df.name_th, df.description_en, df.description_th, df.id_datastructure]
            ).lower()
            return reducer(k in haystack for k in keywords)

        dataflows = [df for df in dataflows if matches(df)]

    # Normalize user input: trim whitespace on dimension ids and codes (upstream values are
    # already stripped), and drop dimensions left with no codes — an empty list would match
    # vacuously (set() <= anything), so an effectively-empty `covers` behaves as no filter.
    coverage: dict[str, list[str]] = {}
    for dim, codes in (params.covers or {}).items():
        cleaned = [c.strip() for c in codes if c and c.strip()]
        if cleaned:
            coverage[dim.strip()] = cleaned
    if coverage:
        # Keep only dataflows whose published availability includes EVERY requested code,
        # for EVERY requested dimension — i.e. the data is actually present, not merely a
        # dimension the DSD declares. One bulk fetch (cached) answers this for all dataflows.
        availability = await get_cached_availability(cache, api)

        def covered(df) -> bool:
            """True if the dataflow's availability covers all requested dim/codes."""
            avail = availability.get(df.id, {})
            return all(set(codes) <= set(avail.get(dim, [])) for dim, codes in coverage.items())

        dataflows = [df for df in dataflows if covered(df)]

    return format_json_response(
        {"count": len(dataflows), "dataflows": [df.model_dump() for df in dataflows]}
    )
