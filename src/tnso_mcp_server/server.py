"""MCP server wiring: configuration, tool registry, and dispatch."""

from __future__ import annotations

import logging
import os
from typing import Any

from dotenv import load_dotenv
from mcp.server import Server
from mcp.types import TextContent, Tool

from .api.client import DEFAULT_AGENCY, DEFAULT_BASE_URL, ApiClient
from .api.models import ApiError
from .cache.manager import CacheManager
from .cache.memory import MemoryCache
from .cache.persistent import PersistentCache
from .tools.check_data_availability import handle_check_data_availability
from .tools.discover_dataflows import handle_discover_dataflows
from .tools.get_cache_diagnostics import handle_get_cache_diagnostics
from .tools.get_codelist_description import handle_get_codelist_description
from .tools.get_concepts import handle_get_concepts
from .tools.get_constraints import handle_get_constraints
from .tools.get_data import handle_get_data
from .tools.get_structure import handle_get_structure
from .tools.get_territorial_codes import handle_get_territorial_codes
from .tools.helpers import configure_cache_ttls
from .tools.probe import DEFAULT_PROBE_FIRST_N, DEFAULT_PROBE_MAX_CANDIDATES
from .utils.blacklist import DataflowBlacklist
from .utils.logging import setup_logging

logger = logging.getLogger(__name__)


def _tool_definitions() -> list[Tool]:
    """Return the MCP tool schemas advertised to clients."""
    return [
        Tool(
            name="discover_dataflows",
            description=(
                "Discover available TNSO (Thailand National Statistical Office) dataflows. "
                "Optionally filter by comma-separated keywords (matched against id, Thai/English "
                "name and description). Start here to find a dataset, then call get_constraints."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "keywords": {
                        "type": "string",
                        "description": "Comma-separated keywords, e.g. 'population, aging' or 'labour'.",
                    }
                },
            },
        ),
        Tool(
            name="get_structure",
            description=(
                "Get the data structure (DSD) for a datastructure id: the ordered list of "
                "dimensions and the codelist each uses. Fast path when you already know the codes."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "datastructure_id": {"type": "string", "description": "DSD id, e.g. 'DSD_01DI_IND_AGING'."}
                },
                "required": ["datastructure_id"],
            },
        ),
        Tool(
            name="get_constraints",
            description=(
                "Get all valid dimension values (with Thai/English labels) and the available time "
                "range for a dataflow. One call returns everything needed to build a get_data query. "
                "Time periods are Buddhist Era (BE = Gregorian + 543)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataflow_id": {"type": "string", "description": "Dataflow id, e.g. 'DF_01DI_IND_AGING'."}
                },
                "required": ["dataflow_id"],
            },
        ),
        Tool(
            name="get_codelist_description",
            description="Get Thai and English labels for every code in a codelist (e.g. 'CL_CWT').",
            inputSchema={
                "type": "object",
                "properties": {
                    "codelist_id": {"type": "string", "description": "Codelist id, e.g. 'CL_SEX'."}
                },
                "required": ["codelist_id"],
            },
        ),
        Tool(
            name="get_concepts",
            description=(
                "Resolve a TNSO SDMX concept id to its Thai or English name (searches all TNSO "
                "concept schemes; result cached). Use lang='th' or lang='en'."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "concept_id": {"type": "string", "description": "Concept id, e.g. 'POP_IND'."},
                    "lang": {"type": "string", "enum": ["th", "en"], "default": "en"},
                },
                "required": ["concept_id"],
            },
        ),
        Tool(
            name="get_data",
            description=(
                "Fetch observations for a dataflow as a TSV table, with reproducible CSV/curl URLs. "
                "Supports dimension filtering and a time range. Omit dimensions from "
                "dimension_filters to get all their values; pass multiple codes per dimension as an "
                "array. If no period is given, the latest available year is returned. "
                "If the query matches no data, returns a diagnosis (invalid codes / out-of-range "
                "period) and up to 2 verified non-empty alternative queries. "
                "IMPORTANT: start_period/end_period are Buddhist Era years (e.g. 2567 = 2024)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataflow_id": {"type": "string", "description": "Dataflow id, e.g. 'DF_01DI_IND_AGING'."},
                    "dimension_filters": {
                        "description": "Map of dimension id -> array of codes, e.g. {\"CWT\": [\"10\"], \"SEX\": [\"_T\"]}. May also be that object JSON-encoded as a string.",
                        "anyOf": [
                            {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}}},
                            {"type": "string"},
                        ],
                    },
                    "start_period": {"type": "string", "description": "Buddhist-era start, e.g. '2560'."},
                    "end_period": {"type": "string", "description": "Buddhist-era end, e.g. '2567'."},
                    "detail": {
                        "type": "string",
                        "enum": ["full", "dataonly", "serieskeysonly", "nodata"],
                        "default": "full",
                    },
                    "dimension_at_observation": {"type": "string"},
                },
                "required": ["dataflow_id"],
            },
        ),
        Tool(
            name="check_data_availability",
            description=(
                "Pre-flight check: does a specific dimension/period combination return any rows? "
                "Returns {available, status, observation_count, diagnosis}. If the combo provably "
                "cannot match (invalid codes or out-of-range period) it answers without a network "
                "call; otherwise it runs one cheap bounded probe. Use before get_data to avoid "
                "empty results. Periods are Buddhist Era (BE = Gregorian + 543)."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "dataflow_id": {"type": "string", "description": "Dataflow id, e.g. 'DF_01DI_IND_AGING'."},
                    "dimension_filters": {
                        "description": "Map of dimension id -> array of codes, e.g. {\"CWT\": [\"10\"], \"SEX\": [\"_T\"]}. May also be that object JSON-encoded as a string.",
                        "anyOf": [
                            {"type": "object", "additionalProperties": {"type": "array", "items": {"type": "string"}}},
                            {"type": "string"},
                        ],
                    },
                    "start_period": {"type": "string", "description": "Buddhist-era start, e.g. '2560'."},
                    "end_period": {"type": "string", "description": "Buddhist-era end, e.g. '2567'."},
                },
                "required": ["dataflow_id"],
            },
        ),
        Tool(
            name="get_cache_diagnostics",
            description=(
                "Check persistent-cache health (exists, size, writability). Pass debug=true to "
                "additionally reveal the cache path and stored keys (host-sensitive). For debugging."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "debug": {
                        "type": "boolean",
                        "description": "Include the host cache path and cached keys (default false).",
                        "default": False,
                    }
                },
            },
        ),
        Tool(
            name="get_territorial_codes",
            description=(
                "Look up Thai geography codes used by the AREA / CWT dimensions: level='region' "
                "(CL_AREA), 'province' (CL_CWT, 77 changwat), or 'district' (CL_AMPHOE). Filter by "
                "'name' (Thai or English substring). Use this to find codes before calling get_data."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "level": {"type": "string", "enum": ["region", "province", "district"]},
                    "name": {"type": "string", "description": "Place-name substring (Thai or English)."},
                },
            },
        ),
    ]


def create_server() -> Server:
    """Build the MCP server: load config, wire cache + API client, register tools."""
    load_dotenv()
    setup_logging(os.getenv("LOG_LEVEL", "INFO"), os.getenv("LOG_DIR", "./log"))

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
        availableconstraint_timeout=float(os.getenv("AVAILABLECONSTRAINT_TIMEOUT_SECONDS", "180")),
        max_retries=int(os.getenv("API_MAX_RETRIES", "3")),
    )
    blacklist = DataflowBlacklist()

    # Empty-result probe budget. PROBE_FIRST_N_OBSERVATIONS=0 disables the
    # firstNObservations payload-trim for upstreams that reject the parameter.
    probe_max_candidates = int(
        os.getenv("PROBE_MAX_CANDIDATES", str(DEFAULT_PROBE_MAX_CANDIDATES))
    )
    probe_first_n = int(os.getenv("PROBE_FIRST_N_OBSERVATIONS", str(DEFAULT_PROBE_FIRST_N)))

    server: Server = Server("tnso-mcp-server")

    @server.list_tools()
    async def list_tools() -> list[Tool]:
        """MCP handler: list the available tools."""
        return _tool_definitions()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any] | None) -> list[TextContent]:
        """MCP handler: dispatch a tool call by name and return its text content."""
        args = arguments or {}
        try:
            if name == "discover_dataflows":
                return await handle_discover_dataflows(args, cache, api, blacklist)
            if name == "get_structure":
                return await handle_get_structure(args, cache, api)
            if name == "get_constraints":
                return await handle_get_constraints(args, cache, api)
            if name == "get_codelist_description":
                return await handle_get_codelist_description(args, cache, api)
            if name == "get_concepts":
                return await handle_get_concepts(args, cache, api)
            if name == "get_data":
                return await handle_get_data(
                    args,
                    cache,
                    api,
                    blacklist,
                    max_candidates=probe_max_candidates,
                    first_n=probe_first_n,
                )
            if name == "check_data_availability":
                return await handle_check_data_availability(
                    args, cache, api, blacklist, first_n=probe_first_n
                )
            if name == "get_cache_diagnostics":
                return await handle_get_cache_diagnostics(args, cache, api)
            if name == "get_territorial_codes":
                return await handle_get_territorial_codes(args, cache, api)
            return [TextContent(type="text", text=f"Unknown tool: {name}")]
        except ApiError as exc:
            # ApiError messages are curated and safe to surface (e.g. "no records found").
            logger.warning("Tool %s API error: %s", name, exc)
            return [TextContent(type="text", text=f"Error in {name}: {exc}")]
        except Exception:
            # Unexpected errors can carry internal details (paths, URLs, parser state):
            # log the full traceback server-side, return a generic message to the client.
            logger.exception("Tool %s failed", name)
            return [
                TextContent(
                    type="text",
                    text=f"Error in {name}: an internal error occurred. See server logs for details.",
                )
            ]

    return server
