"""Tool: get_constraints — valid values (with labels) for every dimension of a dataflow.

Combines three sources, in DSD dimension order:
  * datastructure  -> dimension order + which codelist each dimension uses
  * availableconstraint (mode=available) -> codes actually present + time range
  * codelist       -> Thai/English labels for those codes
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.types import TextContent

from ..api.client import ApiClient
from ..api.models import ApiError, GetConstraintsInput
from ..cache.manager import CacheManager
from ..utils.validators import validate_dataflow_id
from .helpers import (
    format_json_response,
    get_cached_codelist,
    get_cached_constraints,
    get_cached_dataflows,
    get_cached_datastructure,
)

logger = logging.getLogger(__name__)


async def handle_get_constraints(
    arguments: dict[str, Any], cache: CacheManager, api: ApiClient
) -> list[TextContent]:
    """Return valid values (with labels) per dimension plus the available time range."""
    params = GetConstraintsInput.model_validate(arguments or {})
    dataflow_id = params.dataflow_id

    if not validate_dataflow_id(dataflow_id):
        return format_json_response({"error": f"Invalid dataflow id: {dataflow_id!r}"})

    dataflows = await get_cached_dataflows(cache, api)
    dataflow = next((d for d in dataflows if d.id == dataflow_id), None)
    if dataflow is None:
        return format_json_response(
            {"error": f"Dataflow {dataflow_id!r} not found. Run discover_dataflows first."}
        )

    dsd = await get_cached_datastructure(cache, api, dataflow.id_datastructure)
    dim_values, time_range = await get_cached_constraints(cache, api, dataflow_id, dataflow.version)

    constraints: list[dict[str, Any]] = []
    for dim in dsd.dimensions:
        name = dim.dimension

        if name == "TIME_PERIOD":
            start, end = time_range or ("", "")
            constraints.append(
                {
                    "type": "range",
                    "dimension": name,
                    "start_period": start,
                    "end_period": end,
                    "note": "Years are Buddhist Era (BE = Gregorian + 543), e.g. 2567 = 2024.",
                }
            )
            continue

        codelist = None
        if dim.codelist:
            try:
                codelist = await get_cached_codelist(cache, api, dim.codelist)
            except ApiError as exc:  # pragma: no cover - network defensive
                # Only tolerate fetch failures; parse/model/cache defects propagate.
                logger.warning("Codelist %s fetch failed: %s", dim.codelist, exc)

        label_by_code = {cv.code: cv for cv in (codelist.values if codelist else [])}

        codes = dim_values.get(name)
        if codes is None:
            # availableconstraint didn't enumerate this dimension; fall back to full codelist.
            codes = [cv.code for cv in (codelist.values if codelist else [])]

        values = []
        for code in codes:
            cv = label_by_code.get(code)
            values.append(
                {"code": code, "name_en": cv.name_en if cv else "", "name_th": cv.name_th if cv else ""}
            )

        constraints.append(
            {"type": "enumerated", "dimension": name, "codelist": dim.codelist, "values": values}
        )

    return format_json_response({"dataflow_id": dataflow_id, "constraints": constraints})
