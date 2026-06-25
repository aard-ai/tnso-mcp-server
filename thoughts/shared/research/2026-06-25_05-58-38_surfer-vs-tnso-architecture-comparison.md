---
date: 2026-06-25T05:58:38+00:00
researcher: brad
git_commit: bdc41df
branch: main
repository: tnso-mcp-server
topic: "Review the ~/surfer implementation; compare architecture & functionality; the 5 best things this MCP could take from surfer"
tags: [research, sdmx, mcp, architecture, surfer, embeddings, probing]
status: complete
last_updated: 2026-06-25
last_updated_by: brad
---

# Research: `~/surfer` vs `tnso-mcp-server` — architecture comparison & top-5 borrowable ideas

**Date**: 2026-06-25T05:58:38+00:00
**Researcher**: brad
**Git Commit**: bdc41df
**Branch**: main
**Repository**: tnso-mcp-server

## Research Question
Review the `~/surfer` implementation. Compare the architecture and functionality against this TNSO MCP server. Highlight the 5 best things this MCP could take from surfer.

## Summary

`~/surfer` is **two projects**:
- **`sdmx-mcp-gateway`** — a Python FastMCP server and the direct architectural peer to `tnso-mcp-server`. It is a *multi-provider, progressive-discovery, query-validation* server: 12 SDMX endpoints, ~18–23 tools, **4 MCP resources + 4 MCP prompts**, typed structured-output schemas, per-session client pooling over streamable-http, and a "probing" layer that confirms a query returns data before you consume it. Notably it **never returns data rows** — it stops at `build_data_url` (URL + curl) and `probe_data_url` (sample/shape only).
- **`sdmx-surfer`** (package `sdmx-dashboarder`) — a Next.js full-stack SDMX dashboard/chat web app. Its portable ideas are **semantic dataflow discovery** (embedding index + cosine search), an **endpoints registry**, and a large body of **encoded SDMX domain knowledge** (system prompt + tier-2 session memory).

`tnso-mcp-server` is deliberately narrower: single-agency (Thailand NSO), 8 tools, stdio-only, two-layer cache (memory + disk), keyword-only discovery, **tools-only** (no resources/prompts), `TextContent` output (TSV / pretty-JSON), and — its one advantage over the gateway — it actually **returns real data rows** and has on-disk cache persistence.

The five highest-value, best-fit ideas to borrow (detailed at the end):
1. **Query probing + guided empty-result recovery** (the #1 functional gap).
2. **Semantic dataflow discovery** (offline embedding index) to replace keyword matching across 901 bilingual dataflows.
3. **Encoded SDMX domain knowledge as MCP prompts + resources + richer tool descriptions** (cheapest high-leverage win; includes the UNIT_MULT magnitude caveat).
4. **Typed structured-output schemas** (`outputSchema`) instead of `TextContent`.
5. **Cassette-style integration tests + an in-process / stdio-subprocess MCP harness.**

Deliberately **not** in the top 5: full multi-endpoint support — it is the gateway's defining feature but conflicts with TNSO's single-agency identity (see "Explicit non-recommendation").

---

## Architecture comparison

| Dimension | `tnso-mcp-server` | `sdmx-mcp-gateway` | `sdmx-surfer` (web app) |
|---|---|---|---|
| Kind | Python MCP server | Python FastMCP server | Next.js full-stack app |
| Scope | 1 agency (TNSO) | 12 SDMX providers | Multi-provider chat/dashboards |
| Transport | **stdio only** (`__main__.py`) | stdio **+ streamable-http**, DNS-rebind protection (`main_server.py:114-122,5126-5197`) | HTTP (web) |
| Endpoint selection | n/a (hardcoded) | **per-call `endpoint=`** arg; `_resolve_client` (`main_server.py:196-250`) | per-call + host detection (`lib/endpoints-registry.ts:241-251`) |
| Sessions | none | per-`Mcp-Session-Id`, **per-endpoint client pool**, 30-min expiry, heavy locking (`session_manager.py`) | next-auth + Postgres sessions |
| MCP surface | **tools only** | tools **+ 4 resources + 4 prompts** (`main_server.py:5057-5118`) | n/a |
| Tool output | `TextContent` (TSV / pretty-JSON) | **typed pydantic return models → `outputSchema`** (`models/schemas.py`) | n/a |
| Discovery | **keyword AND-match** (`utils`/`discover_dataflows.py`) | keyword substring + score + pagination (`utils.py:242-260`) — also lexical | **semantic embeddings + cosine** (`lib/embeddings.ts`) |
| Cache | **memory + on-disk** (persistent) | memory only: per-client `_cache`/`version_cache` + per-session (`sdmx_progressive_client.py:143-146`) | disk index, reloaded per call |
| Returns data rows? | **Yes** (TSV) | **No** (URL + probe sample only) | Yes (renders charts) |

**Key architectural takeaways the single-agency TNSO server lacks** (from gateway): multi-provider registry with per-provider capability metadata (`config.py:40-207`); cross-endpoint operations in one call (`compare_dataflow_dimensions`, `main_server.py:1710-1767`); session/client pooling; a per-session dataflow→endpoint "mismatch hint" self-correction registry (`main_server.py:253-309`, `session_manager.py:161-181`); and the richer MCP surface (resources/prompts/structured output).

---

## Detailed Findings

### A. `sdmx-mcp-gateway` core architecture
- FastMCP server built at module load with `TransportSecuritySettings` (DNS-rebinding protection) — `main_server.py:114-122`.
- Dual transport via argparse `--transport {stdio,http,streamable-http}`, default stdio; HTTP maps to streamable-http with `--stateless`/`--json-response` — `main_server.py:5126-5197`. **No SSE.**
- Env-driven config (no file): `SDMX_ENDPOINT` default key, `SDMX_BASE_URL`/`SDMX_AGENCY_ID` custom override, `HOST`/`PORT`, `SDMX_STATSNZ_KEY` — `config.py:30-34,198`.
- Lifespan builds `SessionManager` + `AppContext{session_manager, global_config, cache}`, reachable as `ctx.request_context.lifespan_context` — `app_context.py:116-170`, `main_server.py:166-168`.
- **Endpoint selection is per-call, not session-switch** — the documented `switch_session_endpoint` does not exist; `default_endpoint_key` is immutable at runtime; resolution precedence in `_resolve_client` is explicit-arg → session-default → fallback (`main_server.py:196-250`, `session_manager.py:19` stale docstring).
- **Progressive client** (`sdmx_progressive_client.py:110`): `DetailLevel` OVERVIEW→STRUCTURE→FULL (`:30-35`); never fetches `references=all` (README: 100KB+ XML → ~2.5KB); per-dimension code retrieval with `limit=50` + `search_term` + `truncated` flag (`:584-672`); version cache resolves `latest`→concrete (`:233-305`).
- **Sessions**: `SessionState` holds per-endpoint client pool, single-flight `pending` tasks, `known_dataflows` registry, session-scoped `probe_cache`, locks (`session_manager.py:74-105`); 30-min expiry; pool tests in `test_session_pool.py`.

### B. `sdmx-mcp-gateway` tools & functionality
- Discovery/query tools in `tools/sdmx_tools.py`: `list_dataflows` (`:39`), `get_dataflow_structure` (`:168`, no codes), `get_dimension_codes` (`:289`, paginated), `get_data_availability` (`:362`), `validate_query` (`:636`), `build_data_url` (`:820`, returns URL + curl), `build_sdmx_key` (`:1043`), `get_discovery_guide` (`:958`).
- **Discovery is lexical substring matching only** — `filter_dataflows_by_keywords` (`utils.py:242-260`); **no embeddings anywhere in the gateway** (same matching power as TNSO, plus pagination/scoring/hints).
- The user-facing data path **ends at a URL** — no tool returns rows. Only internal fetch is `fetch_data_probe` (`sdmx_progressive_client.py:211`).
- **Probing** (`tools/probing_tools.py`) — the distinctive feature; docstring: closes the gap between "syntax valid" and "this exact query returns observations" (`:1-6`).
  - `probe_data_url` (`:282`): preflight via `/availableconstraint?mode=exact` reading `obs_count` (`:549-672`), else CSV probe with `firstNObservations=1` (`:128,385`); `status ∈ {nonempty,empty,error}`; SHA-256 `query_fingerprint`; session-scoped LRU cache.
  - `suggest_nonempty_queries` (`:816`): on empty, relax one dimension at a time + drop-time candidate (`:722`), **intent-aware ranking** (defer geo for maps, time for timeseries, `:989`), bounded `max_probes=20`/`max_suggestions=5`, returns ranked working URLs with `change_summary`.
- **Mismatch hint** (`main_server.py:253-309`): on 404/empty, names which other endpoint a dataflow is known on (session registry) or steers OECD `DSD@DF` ids toward `agency_id=`; gated by not-found signals (`:326`).
- **Structure diagram** (`get_structure_diagram`, `main_server.py:2711`): generates **Mermaid** hierarchy/impact diagrams with typed subgraphs + icons; `compare_structures` (`:4574`) emits colour-coded version/structural diffs.
- **Time-availability classification** (`check_time_availability`, `main_server.py:978`): three-valued `no` / `plausible` / `plausible_different_frequency` (flags "asked monthly, only annual exists").
- Developer tools (`tools/developer_tools.py`): `get_content_constraints` allowed-vs-actual + coverage gaps (`:440-599`), `get_structure_references` impact analysis (`:627`), `browse_category_scheme` topic taxonomy (`:780`), `check_data_updates` via `updatedAfter` (`:955`).

### C. `sdmx-mcp-gateway` resources, prompts, models, tests
- **4 resources** (`resources/sdmx_resources.py`, registered `main_server.py:5057-5078`): `sdmx://agencies`, templated `sdmx://agency/{agency_id}/info`, `sdmx://formats/guide` (csv/json/xml MIME + tradeoffs), `sdmx://syntax/guide` (key syntax, dot/plus/empty operators, period formats). Reference data the model pulls on demand at **zero tool-selection token cost**.
- **4 prompts** (`prompts/sdmx_prompts.py`, registered `main_server.py:5086-5118`): `discovery_guide` (7-step pipeline naming exact tool calls), `troubleshooting_guide` (404/400/empty/auth playbook), `best_practices` (research/dashboard/automation), `query_builder` (dataflow-specialized key construction).
- **Typed structured output** (`models/schemas.py:1-8`) — every tool annotated `-> SomeResult`; FastMCP emits `outputSchema` + structured content. Rich features: shared `Literal` status enums (`ProbeStatus`, `:17`), nested result trees with per-field `description=`, workflow-affordance fields (`next_step`, `usage`, `recommendation`), `mermaid_diagram` payloads, elicitation schemas (`:725-751`).
- Internal SDMX domain types (`models/sdmx_types.py`): discriminated unions (`DimensionCodesResponse`, `:129-135`), `Literal` discriminators, semantic aliases (`SDMXKey`, `TimePeriod`, …).
- **Tests**: unit (pure-Python model/helpers); integration mocks at the **HTTP-session boundary with inline SDMX-ML/CSV cassettes** so the real parser runs offline (`test_mcp_tools.py:439-490`, `test_probing_tools.py:9-69`) + reaches real `@mcp.tool()` handlers asserting on typed models; e2e opt-in live tests that skip on network error + an **in-process `_FakeCtx`/`AppContext` harness** (`test_multiendpoint_smoke.py:59-95`) and a **stdio subprocess `ClientSession`** protocol smoke test (`test_multiendpoint_smoke_extended.py:844-928`). Gap: no `read_resource`/`get_prompt` protocol coverage.

### D. `sdmx-surfer` web-app portable ideas
- **Semantic search** (`lib/embeddings.ts`): deployed path uses Google `gemini-embedding-001` (API), 3072-dim, brute-force `cosineSimilarity` over the index in a JS loop (`:136-181`); index reloaded per call; **substring fallback** if embedding fails (`app/api/explore/route.ts:36-56`).
  - Build (`scripts/build-index.ts`): per-dataflow **rich text** = `"{name}. {description}. Dimensions: …. Codelists: …"` (`:434-476`); categories/availability stored but not embedded; → `models/dataflow-index.json` (5.6MB, **121 entries**, `gemini-embedding-001`).
  - **Original design was local/offline ONNX**: `ibm-granite/granite-embedding-small-r2` (384-dim, ModernBERT) via `@huggingface/transformers`; switched to Gemini only for Vercel's 250MB limit (git `cf4c083`; `docs/technical-reference.md:69,342-344,1069-1071`). The committed `models/granite-embedding-small-r2/` + `onnxruntime-node` dep are now orphaned. **For an MCP server the offline ONNX path is the better fit.**
  - Fidelity rule: query + documents must use identical model + settings or cosine breaks (`thoughts/…02-17.md:215-229`).
- **Endpoints registry** (`lib/endpoints-registry.ts:98-232`): 12 providers with `apiHosts` (host→provider reverse map, `:241-251`), `agency` fallback for bare-flow URLs, per-provider `buildExplorerUrl`; encodes quirks (OECD sub-agencies, Stats NZ `TIME` vs `TIME_PERIOD`, IMF.STA, ABS `df[ds]` label, BIS topic map). `lib/proxied-hosts.ts:37-64`: only 3 hosts proxied (key/CORS) with `allowedPathPattern` regex narrowing.
- **Domain knowledge** (`lib/system-prompt.ts`): `SDMX_CONVENTIONS` (`:311-340`) — dimension semantics, **empty-slot = all, never `*`**, and the **UNIT_MULT 10^n caveat** (`:332-340`, prevents silent 1000×/1,000,000× magnitude errors); `DISCOVERY_WORKFLOW` (`:241-309`) — progressive workflow + SPC empty sentinel (`9999-01-01`→`0001-12-31`) + per-call routing rules.
- **Tier-2 session memory** (`lib/tier2-knowledge.ts:18-156`): summarizes already-discovered dataflows/dims/URLs into a ~1500-token "do NOT re-query these" block.
- **Data Explorer URL** (`lib/data-explorer-url.ts`): inverse of TNSO's CSV/curl — builds a **human GUI deep link** via dotstatsuite params (`buildDotStatUrl`, `endpoints-registry.ts:43-66`); `extractDataSources` emits per-chart provenance.
- **Model router** (`lib/model-router.ts`): 4 providers; one MCP-relevant nugget — disable eager parallel tool-calling for providers that break sequential discovery loops (`:49-57`).
- Design docs: `thoughts/shared/research/2026-06-24_03-15_surfer-vs-gsdmx2-design-eval.md` is the most decision-relevant — it catalogs surfer's semantic-layer limitations (SPC-only 121 flows, O(N) cosine, thin embed text, and crucially **the chat agent doesn't use the semantic index** — it's wired to lexical `list_dataflows`), and a head-to-head showing semantic+graph reaching usable URLs in 1 round-trip vs 5 lexical calls.

---

## The 5 best things `tnso-mcp-server` should take from surfer

### 1. Query probing + guided empty-result recovery — *the #1 functional gap*
**Where**: gateway `tools/probing_tools.py` (`probe_data_url` `:282`, `suggest_nonempty_queries` `:816`); workflow rules in `system-prompt.ts:241-309`.
**Why it matters most for TNSO**: TNSO's `get_data` returns rows blindly. The #1 LLM SDMX failure is "syntactically valid query, **zero rows**." TNSO already queries `availableconstraint` in `get_constraints` (the slow 180s path) and caches it — so the infrastructure is **half-built**. Add (a) a probe that confirms a *specific dimension-filter combination* is non-empty before/within `get_data`, and (b) on empty, suggest the minimal relaxation that returns data (relax one dimension at a time; for Thai geography defer the `CWT`/`AREA` dimension when the user wants a province breakdown). Turns "here's a URL" into "here's a URL that actually returns data."

### 2. Semantic dataflow discovery (offline embedding index) — replace keyword matching
**Where**: `lib/embeddings.ts`, `scripts/build-index.ts`, `models/dataflow-index.json`; offline-ONNX origin in `docs/technical-reference.md:69,342-344`.
**Why for TNSO**: discovery is currently keyword AND-matching over **901 bilingual (Thai/English) dataflows** — brittle across synonyms and languages. A pre-built embedding index + cosine search (≈190 LoC + one build script + a JSON file, no vector DB; brute-force is fine at this scale) is a large recall upgrade, especially cross-language. **Prefer the original local ONNX model** (granite-small, 384-dim, ~48MB) so discovery stays fully offline and dependency-free. Embed a *rich* text per dataflow (`name. description. Dimensions… Codelists…`), keep query+doc embeddings symmetric, and keep the existing keyword path as a **fallback**. **Critical**: wire it into the actual `discover_dataflows` tool — surfer's own lesson is that an index the agent doesn't call is wasted.

### 3. Encoded SDMX domain knowledge as MCP prompts + resources + richer tool descriptions — *cheapest high-leverage win*
**Where**: gateway `prompts/sdmx_prompts.py` + `resources/sdmx_resources.py`; app `lib/system-prompt.ts:311-340`.
**Why for TNSO**: TNSO is tools-only with minimal static descriptions. Near-zero effort, high payoff: (a) add a `discovery_guide` / `troubleshooting_guide` **MCP prompt**; (b) expose a **resource** with the SDMX key-syntax + format guide and the TNSO dimension cheat-sheet (BE dates, `_T` totals, AREA/CWT geography) at zero tool-selection cost; (c) bake the **UNIT_MULT 10^n caveat** and **empty-slot = all (never `*`)** rule into `get_data`'s description — the UNIT_MULT one prevents an entire class of silent magnitude errors (TNSO data carries `UNIT_MUL`).

### 4. Typed structured-output schemas (`outputSchema`) instead of `TextContent`
**Where**: gateway `models/schemas.py` (wired via return annotations, e.g. `main_server.py:411,2445`).
**Why for TNSO**: tools return TSV / pretty-JSON text the model must re-parse. Annotating each tool with a pydantic **return model** makes FastMCP emit an `outputSchema` + structured content, and lets outputs carry workflow affordances (`next_step`, `recommendation`, `Literal` status enums). Pairs naturally with the input-schema-consistency work just merged (`bdc41df`). (Keep a human-readable TSV alongside for `get_data` since TNSO, unlike the gateway, actually returns rows.)

### 5. Cassette-style integration tests + an in-process / stdio-subprocess MCP harness
**Where**: gateway `tests/integration/test_mcp_tools.py:439-490`, `tests/e2e/test_multiendpoint_smoke.py:59-95`, `test_multiendpoint_smoke_extended.py:844-928`.
**Why for TNSO**: TNSO's tests largely bypass the MCP layer (the new `test_schema_validation.py` only partly closes this). Adopt (a) **inline SDMX-ML/CSV cassettes** mocking at the HTTP boundary so the real parser runs offline and deterministically, and (b) a **stdio `ClientSession` subprocess smoke test** that drives `initialize` + `call_tool` over real JSON-RPC. If TNSO adds resources/prompts (#3), add the `read_resource`/`get_prompt` protocol coverage the gateway itself is missing.

### Honorable mentions (high polish, lower priority)
- **Mermaid structure diagrams / version diffs** (`get_structure_diagram`, `main_server.py:2711`) — nice for explaining a DSD's dimensions visually.
- **Human Data Explorer deep-link** beside the CSV/curl URL (`lib/data-explorer-url.ts`) — one-click verification in the provider GUI.
- **Time-availability classification** (`check_time_availability`, `main_server.py:978`) — "asked monthly, only annual exists" (relevant given TNSO's BE annual data).

### Explicit non-recommendation
- **Full multi-endpoint support** (gateway `config.py:40-207`, sessions, mismatch hints) is the gateway's defining feature but **conflicts with TNSO's single-agency identity**. Only worth it if the project's scope expands beyond Thailand NSO. The lighter parts of that machinery (a typed endpoint/agency metadata object) could still be adopted without going multi-tenant.

---

## Open Questions
- Does TNSO's catalogue have enough Thai/English description richness for embeddings to beat keyword match, or would `discover_dataflows` need the same description-enrichment step `build-index.ts` does?
- For probing: can TNSO's existing cached `availableconstraint` response be reused to answer "is this *specific* filter combination non-empty?" without a second network call?
- Which TNSO dataflows actually carry `UNIT_MULT ≠ 0` (magnitude-caveat impact surface)?
- Does the TNSO endpoint support `firstNObservations=1` and `mode=exact` availableconstraint (the two probe mechanisms the gateway relies on)?
