"""Shared helpers for tool handlers: response formatting, cache wrappers, data utils."""

from __future__ import annotations

import csv
import io
import json
import logging
from datetime import date
from typing import Any

from mcp.types import TextContent

from ..api.client import ApiClient
from ..api.models import (
    CodelistInfo,
    ConceptSchemeInfo,
    DataflowInfo,
    DatastructureInfo,
)
from ..cache.manager import CacheManager

logger = logging.getLogger(__name__)

# Module-level TTLs (seconds); overridden by configure_cache_ttls() at startup.
DATAFLOWS_TTL = 604_800       # 7 days
METADATA_TTL = 2_592_000      # 30 days
DATA_TTL = 86_400             # 1 day

# Thailand uses the Buddhist Era calendar: BE = CE + 543.
BUDDHIST_OFFSET = 543


def configure_cache_ttls(dataflows: int, metadata: int, data: int) -> None:
    """Override the module-level cache TTLs (seconds) from configuration at startup."""
    global DATAFLOWS_TTL, METADATA_TTL, DATA_TTL
    DATAFLOWS_TTL, METADATA_TTL, DATA_TTL = dataflows, metadata, data


# --------------------------------------------------------------------------- #
# Response formatting
# --------------------------------------------------------------------------- #
def _json_default(obj: Any) -> Any:
    """``json.dumps`` fallback: serialize pydantic models via ``model_dump()``."""
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    raise TypeError(f"Object of type {type(obj)} is not JSON serializable")


def format_json_response(obj: Any) -> list[TextContent]:
    """Render ``obj`` as pretty JSON TextContent (Thai text kept unescaped)."""
    # ensure_ascii=False keeps Thai text readable.
    text = json.dumps(obj, ensure_ascii=False, indent=2, default=_json_default)
    return [TextContent(type="text", text=text)]


def text_response(text: str) -> list[TextContent]:
    """Wrap a plain string as MCP TextContent."""
    return [TextContent(type="text", text=text)]


# --------------------------------------------------------------------------- #
# Cache keys
# --------------------------------------------------------------------------- #
def k_dataflows(agency: str) -> str:
    """Cache key for the agency-wide dataflow list."""
    return f"dataflows:{agency}"


def k_datastructure(agency: str, dsd: str) -> str:
    """Cache key for a data structure definition (DSD)."""
    return f"datastructure:{agency}:{dsd}"


def k_codelist(agency: str, codelist_id: str) -> str:
    """Cache key for a codelist."""
    return f"codelist:{agency}:{codelist_id}"


def k_constraints(agency: str, dataflow_id: str, version: str | None) -> str:
    """Cache key for a dataflow's available constraints (versioned, so different
    dataflow versions never collide)."""
    return f"constraints:{agency}:{dataflow_id}:{version or 'latest'}"


def k_conceptschemes(agency: str) -> str:
    """Cache key for the agency-wide concept schemes."""
    return f"conceptschemes:{agency}"


def k_data(
    agency: str,
    dataflow_id: str,
    version: str | None,
    key: str,
    sp: Any,
    ep: Any,
    detail: str,
    dim_at_obs: str | None,
) -> str:
    """Cache key for a data query.

    Includes the dataflow version and every request-shaping input (key, period,
    detail, dimensionAtObservation) so differently shaped requests never reuse
    each other's cached payload.
    """
    return (
        f"data:{agency}:{dataflow_id}:{version or 'latest'}:{key}:"
        f"{sp}:{ep}:{detail}:{dim_at_obs or ''}"
    )


def k_probe(
    agency: str,
    dataflow_id: str,
    version: str | None,
    key: str,
    sp: Any,
    ep: Any,
    first_n: Any,
    detail: Any = "full",
    dimension_at_observation: Any = None,
) -> str:
    """Cache key for a bounded non-empty probe.

    Distinct ``probe:`` namespace from ``k_data`` so a probe's truncated
    (``firstNObservations``) payload never satisfies a later full ``get_data``.
    ``detail`` / ``dimension_at_observation`` are part of the key because they change
    the response shape (e.g. ``serieskeysonly`` returns no observations), so two probes
    of the same key under different detail levels must not share a cache entry.
    """
    return (
        f"probe:{agency}:{dataflow_id}:{version or 'latest'}:{key}:{sp}:{ep}:{first_n}"
        f":{detail}:{dimension_at_observation or '_'}"
    )


# --------------------------------------------------------------------------- #
# Cached fetchers — store plain dicts/lists, reconstruct models on read.
# --------------------------------------------------------------------------- #
async def get_cached_dataflows(cache: CacheManager, api: ApiClient) -> list[DataflowInfo]:
    """Return the dataflow list from cache, fetching + caching on a miss."""
    async def fetch() -> list[dict]:
        """Fetch the dataflow list and serialize to plain dicts for caching."""
        return [d.model_dump() for d in await api.get_dataflows()]

    raw = await cache.get_or_fetch(k_dataflows(api.agency), fetch, persistent_ttl=DATAFLOWS_TTL)
    return [DataflowInfo.model_validate(d) for d in (raw or [])]


async def get_cached_datastructure(cache: CacheManager, api: ApiClient, dsd: str) -> DatastructureInfo:
    """Return a DSD from cache, fetching + caching on a miss."""
    async def fetch() -> dict:
        """Fetch the DSD and serialize to a plain dict for caching."""
        return (await api.get_datastructure(dsd)).model_dump()

    raw = await cache.get_or_fetch(k_datastructure(api.agency, dsd), fetch, persistent_ttl=METADATA_TTL)
    return DatastructureInfo.model_validate(raw)


async def get_cached_codelist(cache: CacheManager, api: ApiClient, codelist_id: str) -> CodelistInfo:
    """Return a codelist from cache, fetching + caching on a miss."""
    async def fetch() -> dict:
        """Fetch the codelist and serialize to a plain dict for caching."""
        return (await api.get_codelist(codelist_id)).model_dump()

    raw = await cache.get_or_fetch(k_codelist(api.agency, codelist_id), fetch, persistent_ttl=METADATA_TTL)
    return CodelistInfo.model_validate(raw)


async def get_cached_constraints(
    cache: CacheManager, api: ApiClient, dataflow_id: str, version: str | None
) -> tuple[dict[str, list[str]], tuple[str, str] | None]:
    """Return ``(dim_values, time_range)`` from cache, fetching + caching on a miss."""
    async def fetch() -> dict:
        """Fetch available constraints and serialize to a cache-friendly dict."""
        dim_values, time_range = await api.get_availableconstraint(dataflow_id, version)
        return {"dim_values": dim_values, "time_range": list(time_range) if time_range else None}

    raw = await cache.get_or_fetch(
        k_constraints(api.agency, dataflow_id, version), fetch, persistent_ttl=METADATA_TTL
    )
    tr = tuple(raw["time_range"]) if raw.get("time_range") else None
    return raw.get("dim_values", {}), tr


async def get_cached_conceptschemes(cache: CacheManager, api: ApiClient) -> list[ConceptSchemeInfo]:
    """Return all concept schemes from cache, fetching + caching on a miss."""
    async def fetch() -> list[dict]:
        """Fetch all concept schemes and serialize to plain dicts for caching."""
        return [c.model_dump() for c in await api.get_conceptschemes()]

    raw = await cache.get_or_fetch(k_conceptschemes(api.agency), fetch, persistent_ttl=METADATA_TTL)
    return [ConceptSchemeInfo.model_validate(c) for c in (raw or [])]


# --------------------------------------------------------------------------- #
# Data utilities
# --------------------------------------------------------------------------- #
def csv_to_tsv(csv_text: str) -> str:
    """Convert SDMX-CSV to a TSV table (tabs are stripped from cell contents)."""
    reader = csv.reader(io.StringIO(csv_text))
    lines: list[str] = []
    for row in reader:
        lines.append("\t".join(cell.replace("\t", " ").replace("\n", " ") for cell in row))
    return "\n".join(lines)


def build_key(dimension_order: list[str], filters: dict[str, list[str]] | None) -> str:
    """Build an SDMX REST key: dot-separated dimension values, ``+`` joins alternatives.

    ``dimension_order`` must EXCLUDE the time dimension (time is a query param, not
    part of the key). Returns ``"all"`` when no filters are supplied.
    """
    filters = filters or {}
    parts = ["+".join(filters.get(dim) or []) for dim in dimension_order]
    key = ".".join(parts)
    return key if key.strip(".") else "all"


def current_buddhist_year() -> int:
    """Return the current year in the Buddhist Era calendar (Gregorian + 543)."""
    return date.today().year + BUDDHIST_OFFSET
