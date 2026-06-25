# Codex Review — Pass 1

## Verdict
Needs rework

## Issues

### [blocker] Config is introduced after phases that depend on it
- **Location in plan**: Phase 2 handler, Phase 3 recovery renderer, Phase 4 config/env
- **Problem**: Phase 2 says `check_data_availability` probes with `first_n` from config, and Phase 3 uses `PROBE_MAX_CANDIDATES`; Phase 4 only later adds those env/config values and threads them through `server.py`. Implementing Phase 2 or 3 literally leaves undefined config sources or forces temporary API churn.
- **Suggested fix**: Move probe config creation/threading into Phase 1 before any handler uses it, or explicitly define module-level defaults in `probe.py` for Phases 1-3 and make Phase 4 only replace them with env-backed values.

### [major] Period diagnosis uses invalid string comparison for real cached time ranges
- **Location in plan**: Phase 1 `diagnose_filters`
- **Problem**: The plan assumes `time_range` values are 4-digit BE years and says lexical comparison is correct. The local cache for `DF_01DI_IND_AGING` contains `['2557-01-01T00:00:00', '2567-12-31T23:59:59']`, while `get_data.py` already slices `time_range[1][:4]`. Comparing `start_period="2557"` to `"2557-01-01T00:00:00"` lexically would incorrectly mark the first available year as out of range.
- **Suggested fix**: Normalize `time_range` to year strings before diagnosis, e.g. `available_start = time_range[0][:4]`, `available_end = time_range[1][:4]`, and compare only same-granularity values unless the request supports finer periods.

### [major] Invalid-code pre-check contradicts the no-network requirement
- **Location in plan**: Current State Analysis and Phase 2 handler
- **Problem**: The plan says the free pre-check must eliminate invalid-code cases without any network call, but Phase 2 always runs `probe.probe_nonempty(...)` after diagnosis. That burns one of the 4 calls/minute even when `availableconstraint` proves the query cannot match.
- **Suggested fix**: In `check_data_availability`, if `diagnosis["invalid_codes"]` is non-empty or the requested period is definitely out of range, return `available: false` with diagnosis and skip the network probe.

### [major] Probe caching success criterion is not guaranteed by the described implementation
- **Location in plan**: Phase 1 `probe_nonempty`
- **Problem**: `CacheManager.get_or_fetch` only caches returned non-`None` values. If `NoRecordsError` or generic `ApiError` is caught outside `get_or_fetch`, empty/inconclusive probe results will not be cached, contradicting the test requirement that a second call does not re-invoke the fake.
- **Suggested fix**: Catch `NoRecordsError` and `ApiError` inside the `fetch` callable and return a cacheable sentinel dict such as `{"status": "empty", "observation_count": 0}` or `{"status": "inconclusive", ...}`.

### [major] `relaxation_candidates` cannot produce the planned drop-time summary
- **Location in plan**: Phase 1 `relaxation_candidates`
- **Problem**: The signature is `relaxation_candidates(filters, dimension_order, *, prioritize)`, but the `Candidate.change_summary` example requires the original time period: `"Removed time period (was 2567–2567)"`. The function has no `start_period` or `end_period` inputs.
- **Suggested fix**: Either pass `start_period` and `end_period` into `relaxation_candidates`, or generate the drop-time candidate summary in the get_data recovery renderer where the period values are available.

### [major] Verification commands include unavailable tooling
- **Location in plan**: Phase 1 and Phase 4 automated verification
- **Problem**: `uv run ruff check ...` is listed as required, but `pyproject.toml` has no `ruff` dependency or config, and `uv run ruff --version` fails in this repo with `Failed to spawn: ruff`.
- **Suggested fix**: Add `ruff` to dev dependencies and optionally a lint config, or remove ruff from success criteria and use only commands that exist in the repository.

### [minor] Public output shape is inconsistent
- **Location in plan**: Desired End State #2 vs Phase 2 handler
- **Problem**: Desired End State says `check_data_availability` returns top-level `{available, observation_count|null, time_range, invalid_codes, note}`. Phase 2 says the handler returns nested `"diagnosis": {...}` and no `note`.
- **Suggested fix**: Choose one response contract and use it consistently in the desired state, handler instructions, and tests.

### [minor] Several changed files lack line ranges
- **Location in plan**: Phase 2 input model, Phase 4 config/docs
- **Problem**: The review criteria ask for paths and line ranges. Several entries only say `src/tnso_mcp_server/api/models.py`, `.env.example`, `README.md`, and `skills/tnso-mcp/SKILL.md` without concrete ranges. The actual current files have relevant anchors: models tool inputs at `api/models.py:78`, env timeout block at `.env.example:1`, README tools table at `README.md:22`, and skill workflow at `skills/tnso-mcp/SKILL.md:26`.
- **Suggested fix**: Add line ranges for every existing file touched; new files can remain marked `(NEW)`.

## File:line spot-checks
- `src/tnso_mcp_server/api/client.py:180` — verified exists
- `src/tnso_mcp_server/api/client.py:215` — verified exists
- `src/tnso_mcp_server/api/client.py:422` — verified exists
- `src/tnso_mcp_server/api/models.py:171` — verified exists
- `src/tnso_mcp_server/tools/get_data.py:55` — verified exists
- `src/tnso_mcp_server/tools/get_data.py:92` — verified exists
- `src/tnso_mcp_server/tools/get_data.py:94` — verified exists
- `src/tnso_mcp_server/tools/helpers.py:91` — verified exists
- `src/tnso_mcp_server/tools/helpers.py:184` — verified exists
- `src/tnso_mcp_server/server.py:229` — contradicted; `except ApiError` starts at line 231
- `tests/test_tools.py:21` — verified exists
- `tests/test_tools.py:69` — verified exists
- `tests/test_schema_validation.py:112` — verified exists
- `pyproject.toml:19` — contradicted; dev dependencies do not include `ruff`
- `README.md:14` — verified exists and still says `8-tool workflow`
- `skills/tnso-mcp/SKILL.md:11` — verified exists and still says `8 tools`
- `src/tnso_mcp_server/tools/probe.py` — not found, correctly planned as new

## Missing coverage
- README and skill tool counts — adding `check_data_availability` changes the server from 8 tools to 9, but the plan only says to update the tools table/workflow and misses existing explicit `8-tool` text.
- `PROBE_FIRST_N_OBSERVATIONS=0` behavior — the plan documents this fallback but does not require an automated test proving `firstNObservations` is omitted when disabled.
- Unknown filter dimensions — existing `build_key` ignores filters for dimensions not in the DSD, and `diagnose_filters` would not flag them when absent from `available`; the plan should decide whether `check_data_availability` reports unknown dimensions.
- Marker-based non-live tests — the repo documents `uv run pytest -m "not integration"`; the plan repeatedly uses `-k "not live"`, which relies on naming rather than the existing `integration` marker.