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
from ..api.models import GetDataInput, NoRecordsError
from ..cache.manager import CacheManager
from ..utils.blacklist import DataflowBlacklist
from ..utils.validators import validate_dataflow_id
from . import helpers, probe
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
    *,
    max_candidates: int = probe.DEFAULT_PROBE_MAX_CANDIDATES,
    first_n: int | None = probe.DEFAULT_PROBE_FIRST_N,
) -> list[TextContent]:
    """Fetch observations as a TSV table with reproducible CSV/curl source URLs.

    On an empty result (404 NoRecordsFound or a 0-row CSV), diagnose the likely cause
    for free and probe a bounded set of single-dimension relaxations, returning a
    diagnosis plus up to 2 verified non-empty alternatives instead of a bare error.
    """
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
    available, time_range = await get_cached_constraints(cache, api, dataflow_id, dataflow.version)
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
    # Two distinct "empty" paths must both route to recovery: a 404 NoRecordsFound
    # (raised as NoRecordsError) and a 200 response whose CSV is header-only.
    empty = False
    try:
        csv_text = await cache.get_or_fetch(cache_key, fetch, persistent_ttl=helpers.DATA_TTL)
    except NoRecordsError:
        empty = True

    if not empty:
        tsv = csv_to_tsv(csv_text)
        n_rows = max(0, len(tsv.splitlines()) - 1)
        empty = n_rows == 0

    if empty:
        return await _render_empty_recovery(
            cache,
            api,
            params,
            dataflow,
            dimension_order,
            available,
            time_range,
            start_period,
            end_period,
            default_note=default_note,
            max_candidates=max_candidates,
            first_n=first_n,
        )

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


def _diagnosis_lines(diagnosis: dict) -> list[str]:
    """Render the free diagnosis (invalid codes / unknown dims / out-of-range period)."""
    lines: list[str] = []
    for dim, info in diagnosis["invalid_codes"].items():
        sample = ", ".join(info["valid_sample"])
        lines.append(
            f"  Invalid {dim} code(s) {info['given']}: not available. Valid e.g.: {sample}"
        )
    if diagnosis["unknown_dimensions"]:
        unknown = ", ".join(diagnosis["unknown_dimensions"])
        lines.append(f"  Unknown dimension(s) (not part of this dataflow): {unknown}")
    if diagnosis["period_out_of_range"]:
        por = diagnosis["period_out_of_range"]
        req = por["requested"]
        avail = por["available_years"]
        lines.append(
            f"  Requested period {req[0] or 'all'}–{req[1] or 'all'} is outside the available "
            f"range {avail[0]}–{avail[1]}."
        )
    return lines


async def _render_empty_recovery(
    cache: CacheManager,
    api: ApiClient,
    params: GetDataInput,
    dataflow: Any,
    dimension_order: list[str],
    available: dict[str, list[str]],
    time_range: tuple[str, str] | None,
    start_period: str | None,
    end_period: str | None,
    *,
    default_note: str = "",
    max_candidates: int,
    first_n: int | None,
) -> list[TextContent]:
    """Diagnose an empty result for free, then probe bounded relaxations for working ones.

    Issues at most ``max_candidates`` probes (the shared RateLimiter serializes them) and
    returns the diagnosis plus up to 2 verified non-empty alternatives, each with full
    (un-truncated) CSV/curl URLs the caller can re-run.
    """
    dataflow_id = dataflow.id
    diagnosis = probe.diagnose_filters(
        params.dimension_filters, available, dimension_order, time_range, start_period, end_period
    )
    prioritize = set(diagnosis["invalid_codes"]) | set(diagnosis["unknown_dimensions"])
    period_out_of_range = bool(diagnosis["period_out_of_range"])
    candidates = probe.relaxation_candidates(
        params.dimension_filters,
        dimension_order,
        start_period,
        end_period,
        prioritize=prioritize,
        period_out_of_range=period_out_of_range,
    )

    working: list[tuple] = []
    probes_issued = 0
    for cand in candidates:
        if probes_issued >= max_candidates or len(working) >= 2:
            break
        # When the period is the diagnosed cause, every probe must drop it — otherwise
        # the dimension relaxations re-probe the known-bad period and are guaranteed empty.
        drop_period = cand.drop_time or period_out_of_range
        sp = None if drop_period else start_period
        ep = None if drop_period else end_period
        result = await probe.probe_nonempty(
            cache, api, dataflow_id, dataflow.version, cand.key, sp, ep, first_n=first_n
        )
        probes_issued += 1
        if result["status"] == "nonempty":
            working.append((cand, result, sp, ep))

    primary_key = build_key(dimension_order, params.dimension_filters)
    lines = [
        f"No data found for {dataflow_id} — {dataflow.name_en or dataflow.name_th}.",
        f"Empty query: key={primary_key}, filters={params.dimension_filters or {}}, "
        f"period {start_period or 'all'}→{end_period or 'all'} "
        "(years are Buddhist Era = Gregorian + 543).",
    ]
    if default_note:
        lines.append(default_note)

    diag_lines = _diagnosis_lines(diagnosis)
    if diag_lines:
        lines.append("")
        lines.append("--- Diagnosis ---")
        lines.extend(diag_lines)

    if working:
        lines.append("")
        lines.append("--- Verified alternatives (these return data) ---")
        for i, (cand, result, sp, ep) in enumerate(working, start=1):
            url_kwargs = {
                "key": cand.key,
                "start_period": sp,
                "end_period": ep,
                "detail": params.detail,
                "dimension_at_observation": params.dimension_at_observation,
            }
            browser_url = api.data_csv_url(dataflow_id, dataflow.version, **url_kwargs)
            curl_url = api.data_curl_url(dataflow_id, dataflow.version, **url_kwargs)
            lines.append(
                f"{i}. {cand.change_summary} — {result['observation_count']}+ observation(s)"
            )
            lines.append(f"   CSV (open in browser): {browser_url}")
            lines.append(
                f"   curl: curl -H 'Accept: application/vnd.sdmx.data+csv;version=1.0.0' '{curl_url}'"
            )
    else:
        lines.append("")
        lines.append(
            f"No non-empty alternative found within the probe budget ({probes_issued} probe(s)). "
            "Run get_constraints to see valid codes and the available time range."
        )

    return text_response("\n".join(lines))
