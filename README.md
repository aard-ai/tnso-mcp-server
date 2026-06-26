# Thailand NSO MCP Server

An [MCP](https://modelcontextprotocol.io) server that lets an LLM **discover, query and
analyse Thailand's official statistics** from the **National Statistical Office (NSO,
agency `TNSO`)** via the SDMX REST API — in natural language.

This is a lightweight MCP that uses keyword search (this is not the Aard MCP).  
It should be effective for e.g. searching for indicators or dataflows. 

Note that this lightweight approach is much less effective for certain question types such as "Compare Bangkok and Chon buri", or "Tell me about trade of all fish that are not tuna".  These kinds of questions benefit from structure-based search across all data sources, just two factors that make the Aard MCP different from other tools.  

When connected to your chat tool (e.g. Gemini or Claude) it can generate analysis like the below:
<img width="1007" height="917" alt="image" src="https://github.com/user-attachments/assets/3b132f28-21a3-4262-b7c1-66a0bc942030" />

This implementation based on a port of [`ondata/istat_mcp_server`](https://github.com/ondata/istat_mcp_server)
(Italy / ISTAT), adjusted to point to the Thaliand NSO SDMX endpoint at
`https://ns1-stathub.nso.go.th/rest`. 

It uses a 9-tool workflow (like the ISTAT MCP), same two-layer caching;
the data source, agency, languages (Thai/English) and geography (Thai provinces) are
swapped in.

> **Note we use Buddhist Era dates.** TNSO publishes time periods in the Buddhist Era
> calendar (BE = Gregorian + 543). So `2567` means **2024**. Pass `start_period` /
> `end_period` as BE years.

## Tools

| Tool | What it does |
|---|---|
| `discover_dataflows` | Search ~900 TNSO dataflows by keyword (Thai/English; any keyword matches (OR), or `match_all` for AND), or by `covers` — the dimension codes a dataflow must actually have data for (e.g. `{"CWT": ["10","20"]}` → every dataflow carrying both Bangkok and Chon Buri). |
| `get_structure` | Dimensions + codelists for a data structure (DSD). |
| `get_constraints` | Valid values (with labels) per dimension + available time range. **Start here.** |
| `get_codelist_description` | Thai/English labels for every code in a codelist. |
| `get_concepts` | Resolve an SDMX concept id to its Thai/English name. |
| `get_data` | Fetch observations as a TSV table (+ reproducible CSV/curl URLs). On an empty result it self-diagnoses (invalid codes / out-of-range period) and suggests verified non-empty alternatives. |
| `check_data_availability` | Pre-flight check that a specific filter/period combination returns rows before a full `get_data`. |
| `get_territorial_codes` | Thai geography codes: region (`CL_AREA`), province (`CL_CWT`, 77 changwat), district (`CL_AMPHOE`). |
| `get_cache_diagnostics` | Inspect the on-disk cache. |

**Typical workflow:** `discover_dataflows` → `get_constraints` → `get_data`
(use `get_territorial_codes` first when you need province/region codes). `get_data`
self-diagnoses empty results and suggests working alternatives; use
`check_data_availability` to pre-check a combination before fetching.

## Install & run

Requires Python ≥ 3.11. Using [uv](https://docs.astral.sh/uv/) (recommended):

```bash
git clone https://github.com/aard-ai/tnso-mcp-server.git
cd tnso-mcp-server
uv venv
uv pip install -e ".[dev]"

# Run the server (stdio transport)
uv run python -m tnso_mcp_server
```

Or with pip:

```bash
cd tnso-mcp-server
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m tnso_mcp_server
```

## Register with an MCP client

**Claude Code:**

```bash
claude mcp add tnso -- uv --directory /abs/path/to/tnso-mcp-server run python -m tnso_mcp_server
```

**Claude Desktop** (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "tnso": {
      "command": "uv",
      "args": ["--directory", "/abs/path/to/tnso-mcp-server", "run", "python", "-m", "tnso_mcp_server"]
    }
  }
}
```

## Configuration

Copy `.env.example` to `.env` to override defaults (API base URL, timeouts, cache TTLs,
log level, dataflow blacklist). All values have sensible defaults, so `.env` is optional.

## Example

```
discover_dataflows(keywords="aging")
  -> DF_01DI_IND_AGING — "Aging Index dataflow"
discover_dataflows(covers={"CWT": ["10", "20"]})
  -> every dataflow whose data covers both Bangkok (10) and Chon Buri (20)
get_constraints(dataflow_id="DF_01DI_IND_AGING")
  -> dimensions POP_IND, SEX, AREA, CWT, ...; TIME_PERIOD range 2557–2567 (BE)
get_territorial_codes(level="province", name="bangkok")
  -> { code: "10", name_en: "Krung Thep Maha Nakhon (Bangkok)", name_th: "กรุงเทพมหานคร" }
get_data(dataflow_id="DF_01DI_IND_AGING", dimension_filters={"CWT": ["10"]}, start_period="2560", end_period="2567")
  -> TSV table of the aging index for Bangkok, 2017–2024
```

## Tests

```bash
uv run pytest -m "not integration"   # fast unit tests (no network)
uv run pytest -m integration         # live tests against the real TNSO API
uv run pytest                         # everything
```


MIT (same as the upstream `istat_mcp_server`).
