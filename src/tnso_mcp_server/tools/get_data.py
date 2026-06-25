"""Tool: get_data — fetch observations as a TSV table (+ reproducible URLs).

TNSO serves SDMX-CSV natively, so we request CSV and render TSV. If neither
start_period nor end_period is given we default to the latest available year
(Buddhist Era) to keep responses small, mirroring the ISTAT server's behaviour.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.types import TextContent

from ..api.client import ApiClient
from ..api.models import GetDataInput
from ..cache.manager import CacheManager
from ..utils.blacklist import DataflowBlacklist
from ..utils.validators import validate_dataflow_id
from . import helpers
from .helpers import (
    build_key,
    csv_to_tsv,
    get_cached_constraints,
    get_cached_dataflows,
    get_cached_datastructure,
    k_data,
    text_response,
)

logger = logging.getLogger(__name__)


async def handle_get_data(
    arguments: dict[str, Any],
    cache: CacheManager,
    api: ApiClient,
    blacklist: DataflowBlacklist,
) -> list[TextContent]:
    """Fetch observations as a TSV table with reproducible CSV/curl source URLs."""
    params = GetDataInput.model_validate(arguments or {})
    dataflow_id = params.dataflow_id

    if not validate_dataflow_id(dataflow_id):
        return text_response(f"Invalid dataflow id: {dataflow_id!r}")
    if blacklist.is_blacklisted(dataflow_id):
        return text_response(f"Dataflow {dataflow_id!r} is blacklisted.")

    dataflows = await get_cached_dataflows(cache, api)
    dataflow = next((d for d in dataflows if d.id == dataflow_id), None)
    if dataflow is None:
        return text_response(f"Dataflow {dataflow_id!r} not found. Run discover_dataflows first.")

    dsd = await get_cached_datastructure(cache, api, dataflow.id_datastructure)
    _dim_values, time_range = await get_cached_constraints(cache, api, dataflow_id, dataflow.version)
    dimension_order = [d.dimension for d in dsd.dimensions if d.dimension != "TIME_PERIOD"]

    start_period = params.start_period
    end_period = params.end_period
    default_note = ""
    if not start_period and not end_period and time_range and time_range[1]:
        year = time_range[1][:4]
        start_period = end_period = year
        default_note = f"(defaulted to latest available year {year}; pass start_period/end_period to widen)"

    key = build_key(dimension_order, params.dimension_filters)

    cache_key = k_data(
        api.agency,
        dataflow_id,
        dataflow.version,
        key,
        start_period,
        end_period,
        params.detail,
        params.dimension_at_observation,
    )

    async def fetch() -> str:
        """Fetch the SDMX-CSV payload from the API (runs only on a cache miss)."""
        return await api.get_data_csv(
            dataflow_id,
            dataflow.version,
            key=key,
            start_period=start_period,
            end_period=end_period,
            detail=params.detail,
            dimension_at_observation=params.dimension_at_observation,
        )

    # Read DATA_TTL off the module at call time so configure_cache_ttls() is honoured.
    csv_text = await cache.get_or_fetch(cache_key, fetch, persistent_ttl=helpers.DATA_TTL)
    tsv = csv_to_tsv(csv_text)
    n_rows = max(0, len(tsv.splitlines()) - 1)

    # Both footer URLs carry every filter (period, detail, layout) so they reproduce
    # exactly the table above.
    url_kwargs = {
        "key": key,
        "start_period": start_period,
        "end_period": end_period,
        "detail": params.detail,
        "dimension_at_observation": params.dimension_at_observation,
    }
    browser_url = api.data_csv_url(dataflow_id, dataflow.version, **url_kwargs)
    curl_url = api.data_curl_url(dataflow_id, dataflow.version, **url_kwargs)

    sources = [
        "",
        "--- Data sources ---",
        f"Dataflow: {dataflow_id} — {dataflow.name_en or dataflow.name_th}",
        f"Rows: {n_rows} {default_note}".rstrip(),
        f"Period: {start_period or 'all'} → {end_period or 'all'}  "
        "(years are Buddhist Era = Gregorian + 543)",
        f"CSV (open in browser): {browser_url}",
        f"curl: curl -H 'Accept: application/vnd.sdmx.data+csv;version=1.0.0' '{curl_url}'",
    ]
    return text_response(tsv + "\n" + "\n".join(sources))
