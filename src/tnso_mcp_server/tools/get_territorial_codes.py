"""Tool: get_territorial_codes — resolve Thai geography codes for get_data queries.

ISTAT shipped a bundled DuckDB of the Italian REF_AREA hierarchy; TNSO instead
publishes geography as standard SDMX codelists, so we read them live (cached 30d):

  * region   -> CL_AREA   (statistical regions/areas, e.g. TH2 = Central region)
  * province -> CL_CWT    (77 changwat, e.g. 10 = Bangkok)
  * district -> CL_AMPHOE (amphoe)

These map to the AREA / CWT dimensions used by TNSO dataflows.
"""

from __future__ import annotations

import logging
from typing import Any

from mcp.types import TextContent

from ..api.client import ApiClient
from ..api.models import GetTerritorialCodesInput
from ..cache.manager import CacheManager
from .helpers import format_json_response, get_cached_codelist

logger = logging.getLogger(__name__)

GEO_LEVELS = {
    "region": "CL_AREA",
    "province": "CL_CWT",
    "district": "CL_AMPHOE",
}


async def handle_get_territorial_codes(
    arguments: dict[str, Any], cache: CacheManager, api: ApiClient
) -> list[TextContent]:
    """Look up Thai region/province/district codes, optionally filtered by name."""
    params = GetTerritorialCodesInput.model_validate(arguments or {})
    level = (params.level or "").strip().lower()
    name = (params.name or "").strip().lower()

    if not level and not name:
        return format_json_response(
            {"error": "Provide 'level' (region|province|district) and/or a 'name' to search."}
        )

    if level and level not in GEO_LEVELS:
        return format_json_response(
            {"error": f"Invalid level {level!r}. Use one of: region, province, district."}
        )

    # No level given -> search regions + provinces (skip the large district list by default).
    levels = [level] if level else ["region", "province"]

    results: list[dict[str, str]] = []
    for lv in levels:
        codelist = await get_cached_codelist(cache, api, GEO_LEVELS[lv])
        for cv in codelist.values:
            if name and (
                name not in cv.name_en.lower()
                and name not in cv.name_th.lower()
                and name != cv.code.lower()
            ):
                continue
            results.append({"level": lv, "code": cv.code, "name_en": cv.name_en, "name_th": cv.name_th})

    return format_json_response({"count": len(results), "codes": results})
