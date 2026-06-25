---
name: tnso-mcp
description: >
  Workflow guide for querying Thailand's official statistics (National Statistical
  Office, TNSO) via this MCP server. Use whenever working with TNSO data, SDMX
  dataflows, Thai statistics, provincial/regional data, population, ageing, labour,
  agriculture, household surveys, census, or any other TNSO dataset. Guides the
  discover -> constraints -> data workflow. Also supports URL-only mode: build a
  download URL without fetching data, keeping the LLM context lightweight.
license: MIT
compatibility: Requires the tnso MCP server to be running (8 tools for the TNSO SDMX API).
metadata:
  author: ported from ondata/istat_mcp_server
  version: "1.0"
  source: https://github.com/ondata/istat_mcp_server
---

# Querying TNSO (Thailand) statistics

## 0. Language
Detect the user's language (Thai vs English) from their message and mirror it in your
prose, labels and section headers. When calling `get_concepts`, pass the matching
`lang` (`"th"` or `"en"`). Every dataflow, codelist and concept carries both
`name_th` and `name_en`.

## 1. Recommended workflow
1. **(If the question is geographic)** `get_territorial_codes` — resolve a place name
   to its code. `level="province"` → `CL_CWT` (77 changwat, e.g. `10` = Bangkok),
   `level="region"` → `CL_AREA`, `level="district"` → `CL_AMPHOE`. These feed the
   `AREA` / `CWT` dimensions.
2. **`discover_dataflows(keywords=...)`** — find the dataset. Keywords match the id,
   Thai/English name and description. Every keyword must appear (AND).
3. **`get_constraints(dataflow_id=...)`** — ONE call returns each dimension's valid
   codes (with Thai/English labels) **in DSD order**, plus the available time range.
4. **`get_data(id_dataflow=..., dimension_filters=..., start_period=..., end_period=...)`**
   — fetch the observations as a TSV table.

Fast path: if you already know the codes, `get_structure` gives just the dimension
order without fetching every codelist.

## 2. Buddhist Era dates — IMPORTANT
TNSO time periods use the **Buddhist Era** calendar: **BE = Gregorian + 543**.
- `2567` = 2024, `2560` = 2017, `2557` = 2014.
- Pass `start_period` / `end_period` as **BE years**.
- If the user says "2024", convert to `2567` before querying; when presenting results,
  you may show both (e.g. "2567 (2024)").

## 3. Building `get_data` queries
- **Dimension order matters** but you don't build the key yourself — pass
  `dimension_filters` as a map of `{dimension_id: [codes]}` and the server orders it.
- **Multiple codes** for one dimension: pass an array, e.g. `{"CWT": ["10", "58"]}`.
- **All values** of a dimension: simply omit it from `dimension_filters`.
- **Totals**: most dimensions have a `_T` ("total") code — use it to avoid summing.
- If no period is given, the server returns the **latest available year**. Only set a
  period range when the user asks for a trend/history.

## 4. URL-only / download mode
If the user asks for a link or download URL (not the data inline), run
`get_constraints` to learn the dimensions, then return the CSV URL that `get_data`
surfaces (the `?format=csv` link) — or describe the filters — without dumping the full
table into context.

## 5. Always cite sources
`get_data` returns a "Data sources" footer with the CSV URL and a curl command. Keep
that in your final answer so the user can reproduce the figures. Attribute the data to
the **National Statistical Office of Thailand (TNSO)**.
