# Query Probing + Guided Empty-Result Recovery — Implementation Plan

## Overview

Make `get_data` self-healing when a query returns no rows: diagnose the likely cause (invalid codes / out-of-range period) for free from already-cached constraints, then probe a small, bounded set of single-dimension relaxations and return the ones that actually contain data — with ready-to-run CSV/curl URLs. Also add a standalone `check_data_availability` tool that confirms whether a specific filter combination is non-empty *before* a full fetch. This closes the #1 LLM-SDMX failure mode: "syntactically valid query, zero rows."

Derived from `thoughts/shared/research/2026-06-25_05-58-38_surfer-vs-tnso-architecture-comparison.md` (idea #1), adapted to TNSO's actual constraints (whole-dataflow `availableconstraint`, 404-as-empty, and a hard 4-calls/60s rate limiter).

## Current State Analysis

- **Empty results surface as an exception, not an empty table.** `ApiClient._get` raises `ApiError("No records found. …", status_code=404)` when the upstream returns HTTP 404 with `NoRecordsFound`/`No Results Found` in the body (`src/tnso_mcp_server/api/client.py:180-187`). This propagates out of `handle_get_data` to the `except ApiError as exc:` dispatcher in `src/tnso_mcp_server/server.py:231-242`, which renders `Error in get_data: No records found. …`. The user gets a dead end with no next step. (Verified live earlier: `get_data(dimension_filters={"SEX":["_T"],"AREA":["_T"]})` returns exactly this error.)
- **A second, distinct empty path exists:** some empty queries can return HTTP 200 with a header-only CSV. `handle_get_data` computes `n_rows = max(0, len(tsv.splitlines()) - 1)` (`src/tnso_mcp_server/tools/get_data.py:94`) and currently returns the table even when `n_rows == 0`. Recovery must handle BOTH the `ApiError` 404 path and the 0-data-row CSV path.
- **`availableconstraint` is whole-dataflow, not per-key.** `ApiClient.get_availableconstraint` calls `availableconstraint/{flow}/all/all?mode=available` (`src/tnso_mcp_server/api/client.py:349-364`) and returns `(dim_values: {dim: [codes]}, time_range: (start,end)|None)` — the *union* of codes present per dimension plus the overall time span. This is a **necessary-but-not-sufficient** signal: a filter code absent from `dim_values[dim]` is definitely empty (free diagnosis), but a combination of individually-present codes may still be jointly empty (needs a real probe).
- **Constraints are already fetched inside `get_data`.** `handle_get_data` already calls `_dim_values, time_range = await get_cached_constraints(...)` (`src/tnso_mcp_server/tools/get_data.py:55`) and currently discards `_dim_values`. Recovery can reuse it with **no extra network call** (cached 30 days, `METADATA_TTL`).
- **Key building is reusable.** `build_key(dimension_order, filters)` dot-joins per-dimension values, `+`-joins alternatives, returns `"all"` when empty (`src/tnso_mcp_server/tools/helpers.py:184-193`). `dimension_order` excludes `TIME_PERIOD` (`get_data.py:56`). Relaxing a dimension = remove its entry from the filters dict and re-run `build_key`.
- **Data fetch + URL builders.** `get_data_csv(...)` (`src/tnso_mcp_server/api/client.py:422-435`) and the footer URL builders `data_csv_url`/`data_curl_url` (`:234-271`) all funnel period/detail params through `_data_query(...)` (`:215-232`). None currently support `firstNObservations`.
- **Hard rate limit.** `RateLimiter(max_calls=4, time_window=60.0)` (`src/tnso_mcp_server/api/client.py:82-85,128`) gates *every* `_get`. The 5th call inside 60s blocks up to ~60s. This makes the gateway's 20-probe budget infeasible here — probe budget must be small (default 3) and the free pre-check must eliminate invalid-code cases without any network call.
- **Caching facade.** `cache.get_or_fetch(key, fetch, persistent_ttl)` is single-flight and two-layer (`src/tnso_mcp_server/cache/manager.py:51-82`); cache keys are built by `k_*` helpers (`src/tnso_mcp_server/tools/helpers.py:65-110`). Probes will be cached under a new `k_probe`.
- **Error type.** `ApiError(message, status_code=0)` (`src/tnso_mcp_server/api/models.py:171-175`). The "no records" case is a 404 but is indistinguishable from other `ApiError`s except by message/`status_code`.
- **Tool surface & dispatch.** Tools are declared in `_tool_definitions()` and dispatched in `call_tool` (`src/tnso_mcp_server/server.py:33-170`, `:207-228`). Input models live in `src/tnso_mcp_server/api/models.py` under "Tool input models" (`:80-165`).
- **Test harness.** `tests/test_tools.py` drives handlers with a `FakeApi` (`:21-110`) + the real temp-backed `cache_manager` fixture (`tests/conftest.py:10-19`); tests are plain `async def` (asyncio auto-mode). `FakeApi.get_data_csv` records `last_data_call` and returns a fixed 1-row CSV (`:69-83`). `FakeApi.get_availableconstraint` returns `({"POP_IND":["DEM_IND101"],"CWT":["10","58"]}, ("2557","2567"))` (`:66-67`). Schema-layer tests live in `tests/test_schema_validation.py` (driven from `_tool_definitions()`).

## Desired End State

1. **`get_data` on an empty result** returns a structured, human-readable message that includes: (a) the empty query restated; (b) a free diagnosis (invalid codes per dimension with valid alternatives drawn from `availableconstraint`; and/or requested period outside the available range); (c) up to 2 *verified non-empty* alternative queries, each with a one-line change summary and a reproducible CSV + curl URL. Bounded by a configurable probe budget (default 3 probes).
   - **Verify:** live `get_data(dataflow_id="DF_01DI_IND_AGING", dimension_filters={"SEX":["_T"],"AREA":["_T"]})` now returns a diagnosis + at least one working alternative (e.g. `AREA=TH`), not a bare error.
2. **`check_data_availability` tool** accepts `dataflow_id` + `dimension_filters` + optional period and returns the single response contract `{dataflow_id, available: bool, status, observation_count: int|null, time_range, diagnosis, note}`. When the free pre-check proves the combo cannot match (invalid codes or definitely out-of-range period), it returns `available: false` **without a network probe**; otherwise it runs exactly one bounded probe.
   - **Verify:** live `check_data_availability(dataflow_id="DF_01DI_IND_AGING", dimension_filters={"SEX":["_T"],"AREA":["TH"],"CWT":["_T"]})` returns `available: true`; the same with `AREA=["_T"]` returns `available: false` plus a `diagnosis`.
3. **No regression:** all existing tools behave identically on the happy path; `uv run pytest -m "not integration"` stays green. Recovery issues **at most `max_candidates` extra probe calls** beyond the calls `get_data` already makes. (The shared `RateLimiter` serializes every upstream call, so the limit is never *exceeded* — but a cold-cache empty query incurs added latency: dataflows + DSD + constraints + primary `/data` + up to N probes, several of which block behind the 4-calls/60s window.)

## What We're NOT Doing

- **No multi-endpoint / multi-agency work** — TNSO stays single-agency. No mismatch-hint registry, no `endpoint=` argument.
- **No new transport, resources, prompts, or structured `outputSchema`** — those are separate borrow-ideas (#3, #4). Output stays `TextContent`.
- **No semantic search** (idea #2).
- **No intent inference from natural language.** "Intent-aware" here is a fixed heuristic: relax non-geography dimensions before geography dimensions (`AREA`/`CWT`), and offer drop-time last. We do not parse user prose.
- **No unbounded relaxation search** (no multi-dimension combinatorial relaxation; one dimension at a time only).
- **No change to the happy-path `get_data` output format** (TSV table + existing footer URLs is preserved verbatim when rows exist).
- **No caching of the *primary* empty result.** Probe sentinels are cached (`k_probe`), but `get_data`'s primary fetch raises `NoRecordsError`, and `cache.get_or_fetch` does not cache exceptions — so a repeated identical empty `get_data` re-hits upstream (serialized by the rate limiter). Caching the primary empty would mean changing the `k_data` value type to carry an empty sentinel; deferred as a separate optimization to avoid mixing payload types under `k_data`.

---

## Phase 1: Probe foundation (pure logic + primitive, no tool wiring)

### Overview
Add the building blocks with zero behavior change to existing tools, so the build stays green and everything is unit-testable in isolation: a precise empty-error type, optional `firstNObservations` support for cheap probes, a probe cache key, and a new `probe.py` module with pure diagnosis/relaxation logic plus the bounded probe primitive.

### Changes Required:

#### 1. Distinct empty-result error type
**File**: `src/tnso_mcp_server/api/models.py:171-175` (after `ApiError`)
**Changes**: Add `class NoRecordsError(ApiError): """Raised when the upstream returns HTTP 404 NoRecordsFound (query is valid but matches no data)."""` (no new `__init__`; inherits `message`/`status_code`).

#### 2. Raise the specific error from the client
**File**: `src/tnso_mcp_server/api/client.py:180-187`
**Changes**: In `_get`, change the `NoRecordsFound`/`No Results Found` branch to raise `NoRecordsError(...)` instead of `ApiError(...)`. Import `NoRecordsError` from `.models` (`:30-39`). Because `NoRecordsError` subclasses `ApiError`, the existing `except ApiError as exc:` dispatcher in `server.py:231-242` and the `except ApiError: raise` in `_get` (`:193-194`) keep working unchanged.

#### 3. Optional `firstNObservations` for bounded probes
**File**: `src/tnso_mcp_server/api/client.py:215-232` and `:422-435`
**Changes**: Add `first_n_observations: int | None = None` param to `_data_query(...)`; when set and > 0, add `query["firstNObservations"] = str(first_n_observations)`. Add the same param to `get_data_csv(...)` and pass it through to `_data_query`. **Do NOT** add it to `data_csv_url`/`data_curl_url` — suggested URLs must return full data, so the public URL builders stay unchanged.

#### 4. Probe cache key
**File**: `src/tnso_mcp_server/tools/helpers.py:91-110` (next to `k_data`)
**Changes**: Add `k_probe(agency, dataflow_id, version, key, sp, ep, first_n) -> str` returning `f"probe:{agency}:{dataflow_id}:{version or 'latest'}:{key}:{sp}:{ep}:{first_n}"`. (Distinct namespace from `k_data` so a probe's truncated payload never satisfies a later full `get_data`.)

#### 5. New module: `src/tnso_mcp_server/tools/probe.py`
**File**: `src/tnso_mcp_server/tools/probe.py` (NEW)
**Changes**: Implement (all type-hinted, docstringed to match repo style):
- Module constants (so Phases 2–3 have a config source before Phase 4 adds env overrides):
  - `GEO_DIMENSIONS: frozenset[str] = frozenset({"AREA", "CWT"})` — dims to defer when relaxing.
  - `DEFAULT_PROBE_MAX_CANDIDATES: int = 3` and `DEFAULT_PROBE_FIRST_N: int = 1` — used directly by Phases 2–3; Phase 4 only overrides these via env-threaded kwargs.
- `count_data_rows(csv_text: str) -> int` — parse SDMX-CSV with `csv.reader`, return `max(0, rows - 1)` (header). Reuse the `csv`/`io` idiom from `helpers.csv_to_tsv` (`helpers.py:175-181`).
- `_year(period: str | None) -> str` — normalize a TNSO period/time-range bound to its 4-char BE year (`(period or "")[:4]`), matching the existing slice in `get_data.py:62`. The cached `time_range` bounds are full datetimes (e.g. `"2557-01-01T00:00:00"`), NOT bare years — comparison must be year-on-year.
- `diagnose_filters(filters, available, dimension_order, time_range, start_period, end_period) -> dict` — pure, no network. Returns `{"invalid_codes": {dim: {"given": [...], "valid_sample": [first ~10 of available[dim]]}}, "unknown_dimensions": [dims in filters not in dimension_order], "period_out_of_range": {"requested": [sp, ep], "available_years": [start_year, end_year]} | None}`. A code is invalid if `available.get(dim)` is non-empty and the code ∉ it. `unknown_dimensions` flags filter keys absent from `dimension_order` (which `build_key` would otherwise silently ignore). Period is out-of-range when, after `_year()` normalization, `end_year(request) < start_year(available)` or `start_year(request) > end_year(available)`.
- `relaxation_candidates(filters, dimension_order, start_period, end_period, *, prioritize: set[str], period_out_of_range: bool = False) -> list[Candidate]` — pure. For each filtered dimension present in `dimension_order`, emit a candidate that drops just that dimension's filter (re-keyed via `build_key`); also emit one "drop time period" candidate (using `start_period`/`end_period` for its summary). Ordering: **if `period_out_of_range` is True, the drop-time candidate goes FIRST** (the period is the known cause — try it before spending budget on dimension relaxations that keep the bad period); otherwise dims named in `prioritize` (the diagnosed-invalid dims) come first, then non-geo dims, then `GEO_DIMENSIONS`, then drop-time last. `Candidate` is a small dataclass `{key, relaxed_filters, change_summary, drop_time: bool}` where `change_summary` is e.g. `"Removed AREA filter (was ['_T'])"` or `"Removed time period (was 2567–2567)"`.
- `async def probe_nonempty(cache, api, dataflow_id, version, key, start_period, end_period, *, first_n) -> dict` — bounded network probe that **never raises and always returns a cacheable dict**. The `fetch` callable passed to `cache.get_or_fetch(k_probe(...), fetch, persistent_ttl=helpers.DATA_TTL)` does the try/except *internally*: it calls `api.get_data_csv(..., key=key, first_n_observations=first_n)`, returns `{"status": "nonempty"|"empty", "observation_count": int}` on success (via `count_data_rows`), `{"status": "empty", "observation_count": 0}` on `NoRecordsError`, and `{"status": "inconclusive", "observation_count": None}` on any other `ApiError` (e.g. upstream rejects `firstNObservations`). Because the sentinel is a non-`None` dict, `get_or_fetch` caches it — so a repeated probe of the same key does not re-hit the API (satisfies the caching test). When `first_n` is falsy (0/None) the call omits `firstNObservations` entirely.

### Success Criteria:

#### Automated Verification:
- [x] `uv run pytest tests/test_probe.py` (new file added in this phase — see below) passes.
- [x] `uv run pytest -m "not integration"` — full suite still green (no behavior change to existing tools). (`integration` is the repo's registered marker for live tests, `pyproject.toml:39-41`; the live file is marked `pytest.mark.integration` at `tests/test_integration_live.py:14`. The repo ships no linter, so no lint command is asserted.)

#### New tests this phase (`tests/test_probe.py`):
- [x] `diagnose_filters` flags a code absent from `available` and ignores a code present in it; flags a filter key absent from `dimension_order` under `unknown_dimensions`; flags an out-of-range period using year-normalized bounds (e.g. request `2599` against available `("2557-01-01T00:00:00","2567-12-31T23:59:59")`), and does NOT flag the first available year `2557`.
- [x] `relaxation_candidates` orders prioritized/invalid dims first, geo dims (`AREA`/`CWT`) last, drop-time last; never relaxes the time dimension as a normal dimension; the drop-time candidate's `change_summary` includes the original period; **and when `period_out_of_range=True`, the drop-time candidate is first**.
- [x] `count_data_rows` returns 0 for header-only CSV, N for N data rows.
- [x] `probe_nonempty` returns `empty` when `FakeApi.get_data_csv` raises `NoRecordsError`, `nonempty` when it returns rows, `inconclusive` when it raises a generic `ApiError`; the result is cached (a second call does not re-invoke the fake — assert via a call counter). When `first_n=0`, `FakeApi.get_data_csv` is called with `first_n_observations` omitted/`None`. Extend `FakeApi.get_data_csv` to accept and record `first_n_observations=None` and to vary its return by `key` (raise `NoRecordsError` for the designated "no-data" key, rows otherwise).

#### Manual Verification:
- [ ] None for this phase (no user-facing change).

---

## Phase 2: `check_data_availability` tool

### Overview
Expose a standalone pre-flight probe: confirm whether a specific filter combination is non-empty before committing to a full `get_data`, reusing Phase 1's engine.

### Changes Required:

#### 1. Input model
**File**: `src/tnso_mcp_server/api/models.py` — add after `GetCacheDiagnosticsInput` (`:162-165`), inside the "Tool input models" section (`:78` onward)
**Changes**: Add `class CheckDataAvailabilityInput(BaseModel)` with `dataflow_id: str` (canonical name, consistent with the merged `bdc41df` work — `Field(validation_alias=AliasChoices("dataflow_id","id_dataflow"))`), `dimension_filters: dict[str, list[str]] | None`, `start_period: str | None = None`, `end_period: str | None = None`. **Share the JSON-string coercion**: extract `GetDataInput._coerce_filters` (`models.py:125-142`) into a module-level `_coerce_dimension_filters(v)` function and register it as a `@field_validator("dimension_filters", mode="before")` on *both* models, so the two never drift.

#### 2. Handler
**File**: `src/tnso_mcp_server/tools/check_data_availability.py` (NEW)
**Changes**: `async def handle_check_data_availability(arguments, cache, api, blacklist, *, first_n=probe.DEFAULT_PROBE_FIRST_N) -> list[TextContent]`. Mirror `get_data`'s setup (`get_data.py:41-56`): validate id (`validate_dataflow_id`), blacklist check, resolve dataflow + DSD + cached constraints (`available`, `time_range`) + `dimension_order`. Run `probe.diagnose_filters(...)` (free). **If `diagnosis["invalid_codes"]`, `diagnosis["unknown_dimensions"]`, or `diagnosis["period_out_of_range"]` is set, return `available: false` WITHOUT a network probe** (the combo provably cannot match — saves a rate-limited call). Otherwise run exactly one `probe.probe_nonempty(...)` on the requested key. Return `format_json_response(...)` with the unified contract: `{"dataflow_id", "available": bool, "status": "nonempty"|"empty"|"inconclusive"|"provably_empty", "observation_count": int|None, "time_range", "diagnosis": {...}, "note": <one-line human summary>}`. `available` is `status == "nonempty"`.

#### 3. Register + dispatch
**File**: `src/tnso_mcp_server/server.py:33-170` and `:207-228`
**Changes**: Add a `Tool(name="check_data_availability", ...)` definition with `required: ["dataflow_id"]` and properties `dataflow_id`, `dimension_filters` (the same `anyOf` object/JSON-string schema used by `get_data`, `server.py:121-126`), **and `start_period` / `end_period`** (`{"type":"string"}`, mirroring `get_data`'s period properties at `server.py:126-127`) so the optional period pre-check is advertised, not invisible. Add the dispatch branch `if name == "check_data_availability": return await handle_check_data_availability(args, cache, api, blacklist)`; import the handler.

### Success Criteria:

#### Automated Verification:
- [x] `uv run pytest tests/test_tools.py -k check_data_availability` passes (new tests: available combo → `available: true` with count; empty combo via `FakeApi` no-data key → `available: false`; **invalid code → `available: false`, status `provably_empty`, diagnosis lists it, and `FakeApi.get_data_csv` is NOT called** — assert via the call counter, proving the no-probe shortcut).
- [x] `uv run pytest tests/test_schema_validation.py` passes — add `check_data_availability` to the valid/invalid payload parametrizations (including a valid payload that carries `start_period`/`end_period`, proving the period fields are advertised) and to the canonical-id consistency list (`ID_CASES`).
- [x] `uv run pytest -m "not integration"` green.

#### Manual Verification:
- [ ] Live MCP call `check_data_availability(dataflow_id="DF_01DI_IND_AGING", dimension_filters={"SEX":["_T"],"AREA":["TH"],"CWT":["_T"]})` → `available: true`.
- [ ] Live `... dimension_filters={"SEX":["_T"],"AREA":["_T"]}` → `available: false` with a diagnosis.

---

## Phase 3: Empty-result recovery in `get_data`

### Overview
Wire the engine into `get_data`: on an empty result (either `NoRecordsError` or a 0-row CSV), diagnose for free and probe a bounded set of relaxations, then append a suggestions section instead of erroring.

### Changes Required:

#### 1. Catch empties and branch to recovery
**File**: `src/tnso_mcp_server/tools/get_data.py:91-118`
**Changes**: Wrap the `csv_text = await cache.get_or_fetch(cache_key, fetch, ...)` call (`:92`) in `try/except NoRecordsError` → set `empty = True`. After fetch, also set `empty = True` when `n_rows == 0`. Rename the discarded constraints variable at `:55` from `_dim_values` to `available` for reuse. When `empty`, return `_render_empty_recovery(...)` (below) instead of the table. When not empty, return the existing table+sources output unchanged.

#### 2. Recovery renderer (in `get_data.py` or `probe.py`)
**File**: `src/tnso_mcp_server/tools/get_data.py` (new private `_render_empty_recovery(...)` helper) using `probe.py`
**Changes**: `_render_empty_recovery(cache, api, params, dataflow, dimension_order, available, time_range, start_period, end_period, *, max_candidates, first_n)`:
- `diagnosis = probe.diagnose_filters(params.dimension_filters, available, dimension_order, time_range, start_period, end_period)`.
- `prioritize = set(diagnosis["invalid_codes"]) | set(diagnosis["unknown_dimensions"])`.
- `candidates = probe.relaxation_candidates(params.dimension_filters, dimension_order, start_period, end_period, prioritize=prioritize, period_out_of_range=bool(diagnosis["period_out_of_range"]))` — so a known out-of-range period probes the drop-time fix before exhausting the budget on dimension relaxations.
- Iterate candidates, issuing at most `max_candidates` probes (default `probe.DEFAULT_PROBE_MAX_CANDIDATES` = 3); for each, `probe.probe_nonempty(..., first_n=first_n)`; collect up to 2 with `status == "nonempty"`. For the drop-time candidate, probe with `start_period=end_period=None`. (Unlike `check_data_availability`, the recovery path still probes even when codes are invalid — its job is to *find* a working alternative; `prioritize` just makes the offending dimension the first one relaxed.)
- Build a text block: restate the empty query; list invalid codes (with valid samples), unknown dimensions, and/or the period-out-of-range note; list each working alternative with its `change_summary`, observation count, and full `data_csv_url`/`data_curl_url` (NO `firstNObservations`). If no non-empty alternative is found within budget, say so and still show the diagnosis. Return via `text_response(...)`.

#### 3. Update the `get_data` tool description
**File**: `src/tnso_mcp_server/server.py:108-116`
**Changes**: Append one sentence: "If the query matches no data, returns a diagnosis (invalid codes / out-of-range period) and up to 2 verified non-empty alternative queries."

### Success Criteria:

#### Automated Verification:
- [x] `uv run pytest tests/test_tools.py -k "get_data and empty"` passes — new tests: (a) `NoRecordsError` on the primary key triggers recovery and the output contains a working alternative + its URL; (b) a 0-row CSV triggers the same; (c) an invalid code is named in the diagnosis; (d) probe count never exceeds `max_candidates` (assert via `FakeApi` call counter). Drive via `FakeApi` raising `NoRecordsError` for the requested key and returning rows for the relaxed key.
- [x] `uv run pytest -m "not integration"` green; existing `test_get_data_defaults_to_latest_year_and_renders_tsv` and `test_get_data_builds_key_and_passes_period` still pass unchanged (happy path untouched).

#### Manual Verification:
- [ ] Live `get_data(dataflow_id="DF_01DI_IND_AGING", dimension_filters={"SEX":["_T"],"AREA":["_T"]})` now returns a diagnosis + at least one working alternative (e.g. `AREA=TH`), not `Error in get_data: No records found`.
- [ ] Confirm via server logs that no more than `PROBE_MAX_CANDIDATES` upstream `GET /data` probe calls were issued for that one tool call.

---

## Phase 4: Configuration, docs, and live verification

### Overview
Make the probe budget configurable, document the new behavior, and verify the two upstream assumptions against the live TNSO endpoint.

### Changes Required:

#### 1. Config / env
**File**: `src/tnso_mcp_server/server.py:173-197` (`create_server`) and `.env.example:1-27` (near the `AVAILABLECONSTRAINT_TIMEOUT_SECONDS` block, `.env.example:5`)
**Changes**: Read `PROBE_MAX_CANDIDATES` (default `probe.DEFAULT_PROBE_MAX_CANDIDATES` = 3) and `PROBE_FIRST_N_OBSERVATIONS` (default `probe.DEFAULT_PROBE_FIRST_N` = 1; `0` disables the `firstNObservations` optimization for endpoints that reject it) from env in `create_server`, and thread them into `handle_get_data` / `handle_check_data_availability` via handler kwargs (the handlers already default to the `probe.DEFAULT_*` constants from Phases 2–3, so this step only *overrides* them — no earlier phase depends on env). Document both in `.env.example`.

#### 2. Docs
**File**: `README.md` (Tools table `README.md:22-31`, the `8-tool workflow` text `README.md:14`, Example block `README.md:87-99` with the `get_data(...)` line at `README.md:96`) and `skills/tnso-mcp/SKILL.md` (`8 tools` text `:11`, workflow list `:26-37`)
**Changes**: Add a `check_data_availability` row to the tools table; **update the literal tool counts** `README.md:14` ("8-tool workflow" → "9-tool workflow") and `skills/tnso-mcp/SKILL.md:11` ("8 tools" → "9 tools"); add one line to the SKILL workflow noting `get_data` now self-diagnoses empty results and suggests verified working alternatives, and that `check_data_availability` can pre-check a combo. Keep all examples using the canonical `dataflow_id`.

### Success Criteria:

#### Automated Verification:
- [x] `uv run pytest -m "not integration"` — full suite green (93 passed: prior 63 + 30 new probe/availability/recovery tests).
- [x] A test asserts that with `first_n=0` the probe omits `firstNObservations` (the `FakeApi` records `first_n_observations is None`), and with `first_n=1` it passes `1` — proving the `PROBE_FIRST_N_OBSERVATIONS=0` fallback path works. (Repo ships no linter, so no lint command is asserted.)

#### Manual Verification:
- [ ] **Verify `firstNObservations` is honored:** live `GET /data/TNSO,DF_01DI_IND_AGING,1.0/._T.TH._T...?firstNObservations=1` returns 200 with a bounded payload (not a 4xx param-rejection). If rejected, set `PROBE_FIRST_N_OBSERVATIONS=0` as the documented default and confirm probes still function (just larger payloads).
- [ ] **Verify empty-vs-error semantics:** confirm whether TNSO returns 404-NoRecordsFound (current assumption) vs 200-header-only for the `AREA=_T` empty case, so the right recovery branch fires (both are handled, but confirm at least one path is exercised live).
- [ ] End-to-end: the two live `get_data` / `check_data_availability` checks from Phases 2–3 produce the expected diagnoses and working alternatives.

---

## Open Questions

(None blocking. The two upstream behaviors the design depends on — `firstNObservations` support and 404-vs-200 empty semantics — are both handled defensively in code AND explicitly verified in Phase 4 manual steps. `PROBE_FIRST_N_OBSERVATIONS=0` is the documented fallback if `firstNObservations` is rejected.)

## Codex Review Pass 1 — Disposition

Review file: `2026-06-25_query-probing-empty-result-recovery.codex_pass1.md`

| Issue | Severity | Action |
|-------|----------|--------|
| Config introduced after phases that depend on it | blocker | Fixed — `DEFAULT_PROBE_MAX_CANDIDATES`/`DEFAULT_PROBE_FIRST_N` now defined in `probe.py` (Phase 1); handlers default to them (Phases 2–3); Phase 4 only overrides via env. |
| Period diagnosis uses invalid string comparison | major | Fixed — added `_year()` normalization (`[:4]`) before comparison; `time_range` bounds are full datetimes. Test added for first/last available year + out-of-range. |
| Invalid-code pre-check contradicts no-network requirement | major | Fixed — `check_data_availability` returns `provably_empty`/`available:false` and skips the probe when diagnosis proves emptiness; test asserts `get_data_csv` not called. (get_data recovery still probes by design — clarified.) |
| Probe caching not guaranteed | major | Fixed — try/except moved *inside* the `fetch` callable; returns a cacheable non-`None` sentinel dict, so `get_or_fetch` caches empty/inconclusive too. |
| `relaxation_candidates` can't produce drop-time summary | major | Fixed — added `start_period`/`end_period` params to the signature. |
| Verification includes unavailable tooling (ruff) | major | Fixed — removed all `ruff` commands (repo ships no linter); standardized on `uv run pytest -m "not integration"`. |
| Public output shape inconsistent | minor | Fixed — single contract `{dataflow_id, available, status, observation_count, time_range, diagnosis, note}` used in Desired End State + handler + tests. |
| Several changed files lack line ranges | minor | Fixed — added ranges for `models.py:162-165`, `.env.example:1-27`, `README.md:14,22-31,84-93`, `SKILL.md:11,26-37`. |
| README/skill tool counts (8→9) | missing coverage | Fixed — Phase 4 now updates the literal "8-tool"/"8 tools" text. |
| `PROBE_FIRST_N_OBSERVATIONS=0` lacks a test | missing coverage | Fixed — Phase 4 asserts `firstNObservations` omitted when `first_n=0`. |
| Unknown filter dimensions unhandled | missing coverage | Fixed — `diagnose_filters` now returns `unknown_dimensions`; test added. |
| `-m "not integration"` vs `-k "not live"` | missing coverage | Fixed — all run commands now use the registered `integration` marker. |
| `server.py:229` except line | spot-check | Fixed — corrected to `server.py:231-242`. |

## Codex Review Pass 2 — Disposition

Review file: `2026-06-25_query-probing-empty-result-recovery.codex_pass2.md`
Verdict: **Pass with revisions** (all 12 Pass-1 issues confirmed addressed; 2 new majors + 1 minor + stale refs, all resolved below).

| Issue | Severity | Action |
|-------|----------|--------|
| Drop-time recovery can be skipped when the period is the actual problem | major | Fixed — `relaxation_candidates` gains `period_out_of_range`; when set, the drop-time candidate is probed FIRST. `get_data` recovery passes `bool(diagnosis["period_out_of_range"])`. Test added. |
| `check_data_availability` schema omits its optional period inputs | major | Fixed — Phase 2 tool schema now advertises `start_period`/`end_period`; schema-validation test includes a period-carrying payload. |
| Probe budget vs whole-call rate safety wording | minor | Fixed — reworded Desired End State #3: recovery caps *extra probe* calls; the `RateLimiter` serializes (never exceeds) but cold cache adds latency. |
| Stale anchor: README example block | minor | Fixed — corrected to `README.md:87-99` (`get_data` at `:96`), verified against the file. |
| Stale anchor: Current State still said `server.py:229-232` | minor | Fixed — corrected to `server.py:231-242`. |
| Outstanding gap: primary empty 404 not cached | gap | Documented as an explicit "What We're NOT Doing" scope decision (changing `k_data`'s value type is deferred). |
| Outstanding gap: live behavior validation deferred | gap | Accepted — remains in Phase 4 manual verification (not a design question). |

## Review Trail

- Pass 1 review: `2026-06-25_query-probing-empty-result-recovery.codex_pass1.md` (verdict: Needs rework)
- Pass 2 review: `2026-06-25_query-probing-empty-result-recovery.codex_pass2.md` (verdict: Pass with revisions)
- Final draft: 2026-06-25 — zero open questions; ready to implement.
