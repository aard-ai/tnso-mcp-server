"""Tool: check_data_availability — pre-flight check that a filter combo is non-empty.

Confirms whether a specific dimension/period combination returns any rows *before*
committing to a full `get_data`. When the free `diagnose_filters` pre-check proves the
combo cannot match (a dimension with no valid codes at all, or an out-of-range period)
it answers ``available: false`` with NO network probe — saving a rate-limited call.
Partially-invalid code lists and unknown dimensions are diagnosed but still probed.
Otherwise it runs exactly one bounded, cached probe.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.types import TextContent

from ..api.client import ApiClient
from ..api.models import CheckDataAvailabilityInput
from ..cache.manager import CacheManager
from ..utils.blacklist import DataflowBlacklist
from ..utils.validators import validate_dataflow_id
from . import probe
from .helpers import (
    build_key,
    format_json_response,
    get_cached_constraints,
    get_cached_dataflows,
    get_cached_datastructure,
)

logger = logging.getLogger(__name__)


def _note(status: str, diagnosis: dict, observation_count: int | None) -> str:
    """One-line human summary of the availability verdict."""
    # Build a trailing warning from signals that were diagnosed but did NOT prove emptiness:
    # unknown dimensions (dropped by build_key) and partially-invalid code lists (the
    # dimension still has valid members, so the combo was probed, not short-circuited).
    notes: list[str] = []
    unknown = diagnosis["unknown_dimensions"]
    if unknown:
        notes.append(
            f"unknown dimension(s) {', '.join(unknown)} were ignored (not part of this dataflow)"
        )
    partial_invalid = [
        dim for dim in diagnosis["invalid_codes"] if dim not in diagnosis["fully_invalid_dims"]
    ]
    if status != "provably_empty" and partial_invalid:
        notes.append(
            f"some requested codes in {', '.join(partial_invalid)} are invalid but other "
            "codes are valid, so the combination was still probed"
        )
    warn = (" Note: " + "; ".join(notes) + ".") if notes else ""

    if status == "provably_empty":
        reasons = []
        if diagnosis["fully_invalid_dims"]:
            reasons.append(
                f"no valid codes requested for {', '.join(diagnosis['fully_invalid_dims'])}"
            )
        if diagnosis["period_out_of_range"]:
            reasons.append("requested period is outside the available range")
        return (
            "This combination cannot match any data ("
            + "; ".join(reasons)
            + "). No network probe was issued."
            + warn
        )
    if status == "nonempty":
        return f"This combination returns data ({observation_count} observation(s) sampled).{warn}"
    if status == "empty":
        return f"This combination is valid but currently returns no data.{warn}"
    return f"Could not confirm availability (upstream did not answer the probe cleanly).{warn}"


async def handle_check_data_availability(
    arguments: dict[str, Any],
    cache: CacheManager,
    api: ApiClient,
    blacklist: DataflowBlacklist,
    *,
    first_n: int | None = probe.DEFAULT_PROBE_FIRST_N,
) -> list[TextContent]:
    """Return whether a specific filter combination is non-empty (one bounded probe at most)."""
    params = CheckDataAvailabilityInput.model_validate(arguments or {})
    dataflow_id = params.dataflow_id

    if not validate_dataflow_id(dataflow_id):
        return format_json_response({"error": f"Invalid dataflow id: {dataflow_id!r}"})
    if blacklist.is_blacklisted(dataflow_id):
        return format_json_response({"error": f"Dataflow {dataflow_id!r} is blacklisted."})

    dataflows = await get_cached_dataflows(cache, api)
    dataflow = next((d for d in dataflows if d.id == dataflow_id), None)
    if dataflow is None:
        return format_json_response(
            {"error": f"Dataflow {dataflow_id!r} not found. Run discover_dataflows first."}
        )

    dsd = await get_cached_datastructure(cache, api, dataflow.id_datastructure)
    available, time_range = await get_cached_constraints(cache, api, dataflow_id, dataflow.version)
    dimension_order = [d.dimension for d in dsd.dimensions if d.dimension != "TIME_PERIOD"]

    diagnosis = probe.diagnose_filters(
        params.dimension_filters,
        available,
        dimension_order,
        time_range,
        params.start_period,
        params.end_period,
    )

    # Only signals that actually empty the SDMX query prove emptiness: a dimension whose
    # every requested code is invalid (so it selects nothing) and an out-of-range period.
    # A partially-invalid code list still has valid members that may return rows (codes in
    # one dimension are OR-ed), and unknown dimensions are dropped by build_key — both are
    # diagnosed but still probed.
    provably_empty = bool(diagnosis["fully_invalid_dims"] or diagnosis["period_out_of_range"])

    if provably_empty:
        status = "provably_empty"
        observation_count: int | None = None
    else:
        key = build_key(dimension_order, params.dimension_filters)
        result = await probe.probe_nonempty(
            cache,
            api,
            dataflow_id,
            dataflow.version,
            key,
            params.start_period,
            params.end_period,
            first_n=first_n,
        )
        status = result["status"]
        observation_count = result["observation_count"]

    return format_json_response(
        {
            "dataflow_id": dataflow_id,
            "available": status == "nonempty",
            "status": status,
            "observation_count": observation_count,
            "time_range": list(time_range) if time_range else None,
            "diagnosis": diagnosis,
            "note": _note(status, diagnosis, observation_count),
        }
    )
