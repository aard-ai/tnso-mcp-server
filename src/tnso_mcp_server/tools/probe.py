"""Empty-result probing: free diagnosis + bounded single-dimension relaxation.

When a `get_data` query matches no rows, the cause is usually one of two things we
can diagnose for free from already-cached `availableconstraint` data — an invalid
code or an out-of-range period — or a jointly-empty combination of individually
valid codes, which needs a real (but bounded and cached) probe to settle.

The probe budget is deliberately small: the shared `RateLimiter` (4 calls / 60s)
makes large probe sweeps infeasible, so the free `diagnose_filters` pre-check must
eliminate the cheap cases without any network call, and `probe_nonempty` truncates
its fetch with `firstNObservations` and caches every outcome.
"""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field

from ..api.client import ApiClient
from ..api.models import ApiError, NoRecordsError
from ..cache.manager import CacheManager
from . import helpers
from .helpers import build_key

# Dimensions to relax LAST: dropping geography is usually the least useful
# relaxation (it collapses the place the user asked about).
GEO_DIMENSIONS: frozenset[str] = frozenset({"AREA", "CWT"})

# Probe budget defaults; Phase 4 overrides these via env-threaded kwargs.
DEFAULT_PROBE_MAX_CANDIDATES: int = 3
DEFAULT_PROBE_FIRST_N: int = 1


@dataclass
class Candidate:
    """One single-dimension (or drop-time) relaxation of an empty query."""

    key: str
    relaxed_filters: dict[str, list[str]] = field(default_factory=dict)
    change_summary: str = ""
    drop_time: bool = False


def count_data_rows(csv_text: str) -> int:
    """Count SDMX-CSV data rows (non-empty rows minus the header); 0 for header-only/empty.

    Blank lines (which ``csv.reader`` yields as empty rows) are ignored so a trailing
    or interleaved newline never inflates the count into a false "non-empty" verdict.
    """
    reader = csv.reader(io.StringIO(csv_text))
    rows = sum(1 for row in reader if row)
    return max(0, rows - 1)


def _year(period: str | None) -> str:
    """Normalize a TNSO period / time-range bound to its 4-char Buddhist-Era year.

    The cached ``time_range`` bounds are full datetimes (e.g. ``"2557-01-01T00:00:00"``),
    not bare years, so all comparisons must be year-on-year.
    """
    return (period or "")[:4]


def _year_int(year: str) -> int | None:
    """Parse a 4-char year to int, or None when it isn't all digits (e.g. ``""``)."""
    return int(year) if year.isdigit() else None


def _out_of_range(req_start: str, req_end: str, avail_start: str, avail_end: str) -> bool:
    """True when the requested year span falls entirely outside the available span."""
    a_start, a_end = _year_int(avail_start), _year_int(avail_end)
    r_start, r_end = _year_int(req_start), _year_int(req_end)
    if a_start is not None and r_end is not None and r_end < a_start:
        return True
    if a_end is not None and r_start is not None and r_start > a_end:
        return True
    return False


def _period_label(start_period: str | None, end_period: str | None) -> str:
    """Render a human period label for a change summary, e.g. ``2567–2567``."""
    return f"{start_period or 'all'}–{end_period or 'all'}"


def diagnose_filters(
    filters: dict[str, list[str]] | None,
    available: dict[str, list[str]],
    dimension_order: list[str],
    time_range: tuple[str, str] | None,
    start_period: str | None,
    end_period: str | None,
) -> dict:
    """Diagnose, with NO network call, why a filter combination is likely empty.

    Returns ``{"invalid_codes", "unknown_dimensions", "period_out_of_range"}``:

    * ``invalid_codes`` — ``{dim: {"given": [bad codes], "valid_sample": [<=10 valid]}}``.
      A code is invalid only when ``available[dim]`` is non-empty and the code is absent
      from it (an empty/absent availability list means "cannot judge").
    * ``unknown_dimensions`` — filter keys that aren't real dimensions (``build_key`` would
      otherwise silently ignore them).
    * ``period_out_of_range`` — ``{"requested": [sp, ep], "available_years": [start, end]}``
      when the requested span is wholly outside the available years, else ``None``.

    Each set signal proves emptiness; their absence does not prove non-emptiness.
    """
    filters = filters or {}
    order = set(dimension_order)

    invalid_codes: dict[str, dict[str, list[str]]] = {}
    unknown_dimensions: list[str] = []
    for dim, codes in filters.items():
        if dim not in order:
            unknown_dimensions.append(dim)
            continue
        valid = available.get(dim) or []
        if not valid:
            continue  # no availability info for this dim -> cannot judge
        bad = [c for c in (codes or []) if c not in valid]
        if bad:
            invalid_codes[dim] = {"given": bad, "valid_sample": valid[:10]}

    period_out_of_range: dict | None = None
    if time_range:
        avail_start, avail_end = _year(time_range[0]), _year(time_range[1])
        if _out_of_range(_year(start_period), _year(end_period), avail_start, avail_end):
            period_out_of_range = {
                "requested": [start_period, end_period],
                "available_years": [avail_start, avail_end],
            }

    return {
        "invalid_codes": invalid_codes,
        "unknown_dimensions": unknown_dimensions,
        "period_out_of_range": period_out_of_range,
    }


def relaxation_candidates(
    filters: dict[str, list[str]] | None,
    dimension_order: list[str],
    start_period: str | None,
    end_period: str | None,
    *,
    prioritize: set[str],
    period_out_of_range: bool = False,
) -> list[Candidate]:
    """Build an ordered list of single-dimension relaxations of an empty query.

    Each filtered dimension present in ``dimension_order`` yields a candidate that drops
    just that dimension's filter (re-keyed via ``build_key``); one extra "drop time period"
    candidate keeps all filters but signals the period should be cleared when probed.

    Ordering: when ``period_out_of_range`` is True the drop-time candidate comes FIRST
    (the period is the known cause — try it before spending budget elsewhere); otherwise
    dimensions named in ``prioritize`` come first, then non-geo dims, then
    ``GEO_DIMENSIONS``, with drop-time last.
    """
    filters = filters or {}

    def rank(dim: str) -> int:
        if dim in prioritize:
            return 0
        if dim in GEO_DIMENSIONS:
            return 2
        return 1

    filtered_dims = [d for d in dimension_order if d in filters]
    filtered_dims.sort(key=rank)  # stable: ties keep dimension_order

    dim_candidates: list[Candidate] = []
    for dim in filtered_dims:
        relaxed = {k: v for k, v in filters.items() if k != dim}
        dim_candidates.append(
            Candidate(
                key=build_key(dimension_order, relaxed),
                relaxed_filters=relaxed,
                change_summary=f"Removed {dim} filter (was {filters[dim]!r})",
            )
        )

    # A drop-time candidate only helps when a period is actually in effect; with both
    # bounds already None it would just re-probe the (already-empty) primary query and
    # waste one of the scarce rate-limited probe slots.
    time_candidates: list[Candidate] = []
    if start_period or end_period:
        time_candidates.append(
            Candidate(
                key=build_key(dimension_order, filters),
                relaxed_filters=dict(filters),
                change_summary=f"Removed time period (was {_period_label(start_period, end_period)})",
                drop_time=True,
            )
        )

    if period_out_of_range:
        return [*time_candidates, *dim_candidates]
    return [*dim_candidates, *time_candidates]


async def probe_nonempty(
    cache: CacheManager,
    api: ApiClient,
    dataflow_id: str,
    version: str | None,
    key: str,
    start_period: str | None,
    end_period: str | None,
    *,
    first_n: int | None,
) -> dict:
    """Probe whether one key is non-empty; never raises, always returns a cacheable dict.

    Returns one of:

    * ``{"status": "nonempty", "observation_count": int}``
    * ``{"status": "empty", "observation_count": 0}``  (404 NoRecordsFound or 0-row CSV)
    * ``{"status": "inconclusive", "observation_count": None}``  (any other ``ApiError``,
      e.g. the upstream rejects ``firstNObservations``)

    The try/except lives inside the cached ``fetch`` so the non-``None`` sentinel is cached
    too — a repeated probe of the same key never re-hits the API. When ``first_n`` is falsy
    (0/None) the request omits ``firstNObservations`` entirely.
    """

    async def fetch() -> dict:
        """Run one bounded probe; classify the outcome into a cacheable sentinel."""
        try:
            csv_text = await api.get_data_csv(
                dataflow_id,
                version,
                key=key,
                start_period=start_period,
                end_period=end_period,
                first_n_observations=first_n or None,
            )
        except NoRecordsError:
            return {"status": "empty", "observation_count": 0}
        except ApiError:
            return {"status": "inconclusive", "observation_count": None}
        count = count_data_rows(csv_text)
        if count <= 0:
            return {"status": "empty", "observation_count": 0}
        return {"status": "nonempty", "observation_count": count}

    return await cache.get_or_fetch(
        helpers.k_probe(api.agency, dataflow_id, version, key, start_period, end_period, first_n),
        fetch,
        persistent_ttl=helpers.DATA_TTL,
    )
