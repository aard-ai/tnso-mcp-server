# Codex Review — Pass 2

## Verdict
Pass with revisions

## Pass-1 issue follow-up

### Config is introduced after phases that depend on it
- **Status**: addressed
- **Evidence**: Current plan defines `DEFAULT_PROBE_MAX_CANDIDATES` / `DEFAULT_PROBE_FIRST_N` in Phase 1 `probe.py`, then says Phase 4 “only *overrides* them — no earlier phase depends on env.”

### Period diagnosis uses invalid string comparison for real cached time ranges
- **Status**: addressed
- **Evidence**: Current plan adds `_year(period) -> str` using `(period or "")[:4]` and explicitly notes cached bounds can be full datetimes like `"2557-01-01T00:00:00"`.

### Invalid-code pre-check contradicts the no-network requirement
- **Status**: addressed
- **Evidence**: Current plan says `check_data_availability` returns `available: false` without a probe when `diagnosis["invalid_codes"]`, `unknown_dimensions`, or `period_out_of_range` is set, and adds a test asserting `FakeApi.get_data_csv` is not called.

### Probe caching success criterion is not guaranteed by the described implementation
- **Status**: addressed
- **Evidence**: Current plan moves `NoRecordsError` / `ApiError` handling inside the `fetch` callable and returns non-`None` sentinel dicts. Repo check confirms `CacheManager.get_or_fetch` only caches non-`None` values at `src/tnso_mcp_server/cache/manager.py:80-81`.

### `relaxation_candidates` cannot produce the planned drop-time summary
- **Status**: addressed
- **Evidence**: Current plan changes the signature to `relaxation_candidates(filters, dimension_order, start_period, end_period, *, prioritize)` and requires the drop-time summary to include the original period.

### Verification commands include unavailable tooling
- **Status**: addressed
- **Evidence**: Current plan removes ruff and standardizes on `uv run pytest -m "not integration"`. Repo check confirms dev deps only include pytest and pytest-asyncio at `pyproject.toml:19-23`.

### Public output shape is inconsistent
- **Status**: addressed
- **Evidence**: Desired End State and Phase 2 now use one contract: `{dataflow_id, available, status, observation_count, time_range, diagnosis, note}`.

### Several changed files lack line ranges
- **Status**: partially addressed
- **Evidence**: The plan now includes line ranges for `models.py`, `.env.example`, `README.md`, and `SKILL.md`, but some are still stale/imprecise: it names the README example block as `README.md:84-93`, while the current example block is `README.md:87-98`.

### README and skill tool counts
- **Status**: addressed
- **Evidence**: Current plan explicitly says to update `README.md:14` from “8-tool workflow” to “9-tool workflow” and `skills/tnso-mcp/SKILL.md:11` from “8 tools” to “9 tools.” Repo check confirms those current strings exist at those lines.

### `PROBE_FIRST_N_OBSERVATIONS=0` behavior
- **Status**: addressed
- **Evidence**: Current plan requires a test proving `first_n=0` records `first_n_observations is None`, and `probe_nonempty` says falsy `first_n` omits `firstNObservations`.

### Unknown filter dimensions
- **Status**: addressed
- **Evidence**: Current plan adds `unknown_dimensions` to `diagnose_filters`, tests it, and makes `check_data_availability` return `provably_empty` without probing for unknown dimensions.

### Marker-based non-live tests
- **Status**: addressed
- **Evidence**: Current plan uses `uv run pytest -m "not integration"` and cites the repo’s registered marker. Repo check confirms `integration` is registered in `pyproject.toml:39-40`.

### `server.py:229` except line
- **Status**: partially addressed
- **Evidence**: Phase 1 now cites `server.py:231-242`, matching the real `except ApiError` start at `src/tnso_mcp_server/server.py:231`; however Current State still says `server.py:229-232`.

## New or remaining issues

### [major] Drop-time recovery can be skipped when the period is the actual problem
- **Location in plan**: Phase 3 recovery renderer: “non-geo dims, then `GEO_DIMENSIONS`, then drop-time last” and “issuing at most `max_candidates` probes (default 3)”
- **Problem**: For an out-of-range period with 3+ filtered dimensions, all dimension-relaxation probes keep the invalid period and can consume the default budget before the drop-time candidate is reached. That means the plan can fail its own period recovery goal even though `diagnose_filters` knows the period is out of range.
- **Suggested fix**: When `diagnosis["period_out_of_range"]` is set, prioritize the drop-time candidate first or always include it within the probe budget before ordinary dimension relaxations.

### [major] `check_data_availability` schema omits its optional period inputs
- **Location in plan**: Phase 2 Register + dispatch: “same `dimension_filters` `anyOf` object/JSON-string schema used by `get_data`”
- **Problem**: Desired End State says `check_data_availability` accepts optional period, and the model includes `start_period` / `end_period`, but the tool schema instructions only mention `dataflow_id` and `dimension_filters`. Because MCP clients rely on the advertised schema, period pre-checks will be under-documented or invisible.
- **Suggested fix**: Add `start_period` and `end_period` properties to the `check_data_availability` tool schema and include a schema-validation test with those fields.

### [minor] Probe budget is defined only for probes, but readiness claims imply whole-call rate safety
- **Location in plan**: Desired End State: “the rate limiter is never exceeded by more than the configured budget on a single tool call”
- **Problem**: On a cold cache, an empty `get_data` can perform metadata/setup calls plus the primary data call before recovery probes: dataflows, DSD, constraints, primary data, then up to 3 probes. The Phase 3 manual check only verifies `PROBE_MAX_CANDIDATES` `/data` probe calls, not total upstream calls under the 4-calls/60s limiter.
- **Suggested fix**: Clarify the criterion as “extra recovery probes are capped,” or add an explicit cold-cache behavior policy such as lowering the probe budget when metadata was fetched during the same call.

## Outstanding gaps
- Empty primary 404 caching — `probe_nonempty` caches empty probe sentinels, but the primary `get_data` 404 path still re-hits the upstream on repeated identical empty calls.
- Stale line anchors — not implementation-blocking, but the plan still has a few inaccurate references that will slow execution and review.
- Live behavior validation — still deferred to Phase 4, which is acceptable, but implementation should not be considered done until `firstNObservations` support and 404-vs-header-only empty behavior are confirmed.

## Implementation-readiness check
- Are all "Open Questions" in the plan resolved? The plan lists none blocking; the remaining live checks are verification tasks, not design questions.
- I would implement from this plan after the revisions above, especially the drop-time prioritization and `check_data_availability` period schema fix.