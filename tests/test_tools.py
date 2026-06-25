"""Tool-handler tests against a fake API + real (temp) cache. No network."""

from __future__ import annotations

import json

from tnso_mcp_server.api.models import (
    ApiError,
    CodelistInfo,
    CodeValue,
    DataflowInfo,
    DatastructureInfo,
    DimensionInfo,
    NoRecordsError,
)
from tnso_mcp_server.tools.check_data_availability import handle_check_data_availability
from tnso_mcp_server.tools.discover_dataflows import handle_discover_dataflows
from tnso_mcp_server.tools.get_constraints import handle_get_constraints
from tnso_mcp_server.tools.get_data import handle_get_data
from tnso_mcp_server.tools.get_territorial_codes import handle_get_territorial_codes
from tnso_mcp_server.utils.blacklist import DataflowBlacklist


class FakeApi:
    agency = "TNSO"
    base_url = "https://example.test/rest"

    def __init__(self):
        self.dataflows = [
            DataflowInfo(
                id="DF_AGING",
                name_en="Aging Index",
                id_datastructure="DSD_AGING",
                version="1.0",
                agency="TNSO",
            )
        ]
        self.dsd = DatastructureInfo(
            id_datastructure="DSD_AGING",
            dimensions=[
                DimensionInfo(dimension="POP_IND", codelist="CL_POP_IND"),
                DimensionInfo(dimension="CWT", codelist="CL_CWT"),
                DimensionInfo(dimension="TIME_PERIOD"),
            ],
        )
        self.codelists = {
            "CL_POP_IND": CodelistInfo(
                id_codelist="CL_POP_IND", values=[CodeValue(code="DEM_IND101", name_en="Aging index")]
            ),
            "CL_CWT": CodelistInfo(
                id_codelist="CL_CWT",
                values=[
                    CodeValue(code="10", name_en="Bangkok", name_th="กรุงเทพมหานคร"),
                    CodeValue(code="58", name_en="Mae Hong Son"),
                ],
            ),
        }
        self.last_data_call: dict = {}
        # Probe-driven knobs: how many times get_data_csv was called, and which keys
        # behave as empty (404 NoRecordsFound) or inconclusive (generic ApiError).
        self.data_call_count = 0
        self.data_calls: list[dict] = []
        self.no_data_keys: set[str] = set()
        self.error_keys: set[str] = set()
        self.header_only_keys: set[str] = set()
        # Periods (start_period values) for which the upstream returns no records,
        # so period-dependent recovery can be exercised (FakeApi is otherwise period-blind).
        self.empty_periods: set[str] = set()

    async def get_dataflows(self):
        return list(self.dataflows)

    async def get_datastructure(self, dsd):
        return self.dsd

    async def get_codelist(self, codelist_id):
        return self.codelists[codelist_id]

    async def get_availableconstraint(self, dataflow_id, version=None):
        return {"POP_IND": ["DEM_IND101"], "CWT": ["10", "58"]}, ("2557", "2567")

    async def get_data_csv(
        self,
        dataflow_id,
        version=None,
        key="all",
        start_period=None,
        end_period=None,
        detail="full",
        dimension_at_observation=None,
        first_n_observations=None,
    ):
        self.data_call_count += 1
        self.last_data_call = {
            "key": key,
            "start": start_period,
            "end": end_period,
            "detail": detail,
            "dimension_at_observation": dimension_at_observation,
            "first_n": first_n_observations,
        }
        self.data_calls.append(self.last_data_call)
        if key in self.error_keys:
            raise ApiError("Upstream rejected the request.", status_code=400)
        if start_period in self.empty_periods:
            raise NoRecordsError("No records found.", status_code=404)
        if key in self.no_data_keys:
            raise NoRecordsError("No records found.", status_code=404)
        if key in self.header_only_keys:
            return "DATAFLOW,POP_IND,CWT,TIME_PERIOD,OBS_VALUE\n"
        return (
            "DATAFLOW,POP_IND,CWT,TIME_PERIOD,OBS_VALUE\n"
            "TNSO:DF_AGING(1.0),DEM_IND101,10,2567,123\n"
        )

    def flow_ref(self, dataflow_id, version=None):
        return f"TNSO,{dataflow_id},{version or 'latest'}"

    def data_csv_url(
        self,
        dataflow_id,
        version=None,
        key="all",
        start_period=None,
        end_period=None,
        detail="full",
        dimension_at_observation=None,
    ):
        return f"{self.base_url}/data/{self.flow_ref(dataflow_id, version)}/{key}?format=csv"

    def data_curl_url(
        self,
        dataflow_id,
        version=None,
        key="all",
        start_period=None,
        end_period=None,
        detail="full",
        dimension_at_observation=None,
    ):
        return f"{self.base_url}/data/{self.flow_ref(dataflow_id, version)}/{key}"


def _text(result):
    return result[0].text


async def test_discover_matches_keyword(cache_manager):
    api = FakeApi()
    res = await handle_discover_dataflows({"keywords": "aging"}, cache_manager, api, DataflowBlacklist([]))
    data = json.loads(_text(res))
    assert data["count"] == 1
    assert data["dataflows"][0]["id"] == "DF_AGING"


async def test_discover_no_match(cache_manager):
    api = FakeApi()
    res = await handle_discover_dataflows({"keywords": "zzz"}, cache_manager, api, DataflowBlacklist([]))
    assert json.loads(_text(res))["count"] == 0


async def test_discover_keywords_or_by_default(cache_manager):
    api = FakeApi()
    # "zzz" is absent, but OR semantics means the present "aging" still matches.
    res = await handle_discover_dataflows(
        {"keywords": "aging, zzz"}, cache_manager, api, DataflowBlacklist([])
    )
    assert json.loads(_text(res))["count"] == 1


async def test_discover_match_all_requires_every_keyword(cache_manager):
    api = FakeApi()
    # match_all=True (AND): "zzz" is absent, so nothing matches...
    res = await handle_discover_dataflows(
        {"keywords": "aging, zzz", "match_all": True}, cache_manager, api, DataflowBlacklist([])
    )
    assert json.loads(_text(res))["count"] == 0
    # ...but both "aging" and "index" appear in "Aging Index", so it matches.
    res = await handle_discover_dataflows(
        {"keywords": "aging, index", "match_all": True}, cache_manager, api, DataflowBlacklist([])
    )
    assert json.loads(_text(res))["count"] == 1


async def test_constraints_merges_values_and_labels(cache_manager):
    api = FakeApi()
    res = await handle_get_constraints({"dataflow_id": "DF_AGING"}, cache_manager, api)
    data = json.loads(_text(res))
    dims = {c["dimension"]: c for c in data["constraints"]}
    assert dims["CWT"]["type"] == "enumerated"
    codes = {v["code"]: v for v in dims["CWT"]["values"]}
    assert codes["10"]["name_en"] == "Bangkok"
    assert codes["10"]["name_th"] == "กรุงเทพมหานคร"
    assert dims["TIME_PERIOD"]["type"] == "range"
    assert dims["TIME_PERIOD"]["start_period"] == "2557"
    assert dims["TIME_PERIOD"]["end_period"] == "2567"


async def test_get_data_defaults_to_latest_year_and_renders_tsv(cache_manager):
    api = FakeApi()
    res = await handle_get_data({"id_dataflow": "DF_AGING"}, cache_manager, api, DataflowBlacklist([]))
    txt = _text(res)
    assert "DEM_IND101" in txt
    assert "\t" in txt  # TSV
    assert "Buddhist Era" in txt
    assert api.last_data_call["key"] == "all"
    assert api.last_data_call["start"] == "2567"  # defaulted to latest available year
    assert api.last_data_call["end"] == "2567"


async def test_get_data_builds_key_and_passes_period(cache_manager):
    api = FakeApi()
    await handle_get_data(
        {
            "id_dataflow": "DF_AGING",
            "dimension_filters": {"CWT": ["10"]},
            "start_period": "2560",
            "end_period": "2564",
        },
        cache_manager,
        api,
        DataflowBlacklist([]),
    )
    # dimension order excludes TIME_PERIOD -> [POP_IND, CWT]; CWT=10 -> ".10"
    assert api.last_data_call["key"] == ".10"
    assert api.last_data_call["start"] == "2560"
    assert api.last_data_call["end"] == "2564"


async def test_get_data_empty_no_records_triggers_recovery(cache_manager):
    api = FakeApi()
    api.no_data_keys = {".10"}  # primary key (POP_IND.CWT order, CWT=10) -> 404
    res = await handle_get_data(
        {"id_dataflow": "DF_AGING", "dimension_filters": {"CWT": ["10"]}},
        cache_manager,
        api,
        DataflowBlacklist([]),
    )
    txt = _text(res)
    assert "No data found" in txt
    assert "Removed CWT filter" in txt  # the verified alternative
    assert "/data/" in txt  # a reproducible URL was rendered


async def test_get_data_empty_header_only_csv_triggers_recovery(cache_manager):
    api = FakeApi()
    api.header_only_keys = {".10"}  # primary returns a 0-row (header-only) CSV
    res = await handle_get_data(
        {"id_dataflow": "DF_AGING", "dimension_filters": {"CWT": ["10"]}},
        cache_manager,
        api,
        DataflowBlacklist([]),
    )
    txt = _text(res)
    assert "No data found" in txt
    assert "Removed CWT filter" in txt


async def test_get_data_empty_names_invalid_code_in_diagnosis(cache_manager):
    api = FakeApi()
    api.no_data_keys = {".999"}  # invalid CWT code; primary 404s
    res = await handle_get_data(
        {"id_dataflow": "DF_AGING", "dimension_filters": {"CWT": ["999"]}},
        cache_manager,
        api,
        DataflowBlacklist([]),
    )
    txt = _text(res)
    assert "Diagnosis" in txt
    # Pin the invalid-code diagnosis line specifically (CWT/999 also appear in the
    # echoed query line, so assert the line the invalid-code signal actually produces).
    assert "Invalid CWT code" in txt
    assert "999" in txt
    assert "Removed CWT filter" in txt  # still finds a working alternative


async def test_get_data_out_of_range_period_recovery(cache_manager):
    api = FakeApi()
    api.empty_periods = {"2599"}  # the requested out-of-range year returns no records
    res = await handle_get_data(
        {
            "id_dataflow": "DF_AGING",
            "dimension_filters": {"CWT": ["10"]},
            "start_period": "2599",
            "end_period": "2599",
        },
        cache_manager,
        api,
        DataflowBlacklist([]),
    )
    txt = _text(res)
    assert "outside the available range" in txt  # period diagnosed as the cause
    assert "Removed time period" in txt  # drop-time alternative found (probed first)
    # Fix: dimension-drop probes must NOT re-send the known-bad period.
    dim_drop_calls = [c for c in api.data_calls if c["key"] == "all"]
    assert dim_drop_calls and all(c["start"] is None for c in dim_drop_calls)


async def test_get_data_recovery_discloses_defaulted_year(cache_manager):
    api = FakeApi()
    api.no_data_keys = {".10"}  # the defaulted latest-year query is empty
    res = await handle_get_data(
        {"id_dataflow": "DF_AGING", "dimension_filters": {"CWT": ["10"]}},
        cache_manager,
        api,
        DataflowBlacklist([]),
    )
    txt = _text(res)
    # The server auto-picked the latest year; recovery must disclose that, not present
    # the defaulted period as if the caller requested it.
    assert "defaulted to latest available year" in txt


async def test_get_data_recovery_probe_matches_requested_detail(cache_manager):
    api = FakeApi()
    api.no_data_keys = {".10"}  # primary query is empty -> recovery probes relaxations
    await handle_get_data(
        {
            "id_dataflow": "DF_AGING",
            "dimension_filters": {"CWT": ["10"]},
            "detail": "serieskeysonly",
        },
        cache_manager,
        api,
        DataflowBlacklist([]),
    )
    # Every upstream call (primary + recovery probes) must use the caller's detail, so a
    # "verified alternative" is checked at the exact shape its advertised URL will request.
    assert api.data_calls
    assert all(c["detail"] == "serieskeysonly" for c in api.data_calls)


async def test_get_data_empty_probe_count_bounded(cache_manager):
    api = FakeApi()
    # primary + every relaxation is empty, forcing the loop to exhaust the budget.
    api.no_data_keys = {"DEM_IND101.10", ".10", "DEM_IND101."}
    res = await handle_get_data(
        {"id_dataflow": "DF_AGING", "dimension_filters": {"POP_IND": ["DEM_IND101"], "CWT": ["10"]}},
        cache_manager,
        api,
        DataflowBlacklist([]),
        max_candidates=2,
    )
    txt = _text(res)
    # 1 primary fetch + at most max_candidates (2) probes = 3 upstream calls.
    assert api.data_call_count == 3
    assert "No non-empty alternative found" in txt
    assert "2 probe(s)" in txt


async def test_get_data_blacklisted(cache_manager):
    api = FakeApi()
    res = await handle_get_data(
        {"id_dataflow": "DF_AGING"}, cache_manager, api, DataflowBlacklist(["DF_AGING"])
    )
    assert "blacklisted" in _text(res).lower()


async def test_check_data_availability_available_combo(cache_manager):
    api = FakeApi()
    res = await handle_check_data_availability(
        {"dataflow_id": "DF_AGING", "dimension_filters": {"CWT": ["10"]}},
        cache_manager,
        api,
        DataflowBlacklist([]),
    )
    data = json.loads(_text(res))
    assert data["available"] is True
    assert data["status"] == "nonempty"
    assert data["observation_count"] == 1
    assert api.data_call_count == 1  # exactly one probe


async def test_check_data_availability_empty_combo(cache_manager):
    api = FakeApi()
    api.no_data_keys = {".10"}  # POP_IND.CWT order -> CWT=10 is ".10"
    res = await handle_check_data_availability(
        {"dataflow_id": "DF_AGING", "dimension_filters": {"CWT": ["10"]}},
        cache_manager,
        api,
        DataflowBlacklist([]),
    )
    data = json.loads(_text(res))
    assert data["available"] is False
    assert data["status"] == "empty"


async def test_check_data_availability_invalid_code_skips_probe(cache_manager):
    api = FakeApi()
    res = await handle_check_data_availability(
        {"dataflow_id": "DF_AGING", "dimension_filters": {"CWT": ["999"]}},
        cache_manager,
        api,
        DataflowBlacklist([]),
    )
    data = json.loads(_text(res))
    assert data["available"] is False
    assert data["status"] == "provably_empty"
    assert "CWT" in data["diagnosis"]["invalid_codes"]
    assert data["diagnosis"]["fully_invalid_dims"] == ["CWT"]  # every requested code invalid
    assert api.data_call_count == 0  # no network probe was issued


async def test_check_data_availability_partial_invalid_codes_probes(cache_manager):
    api = FakeApi()
    # CWT 10 is valid, 999 is not. Codes within a dimension are OR-ed (key "10+999"), so
    # the combo can still return rows for 10 — it must be probed, not declared empty.
    res = await handle_check_data_availability(
        {"dataflow_id": "DF_AGING", "dimension_filters": {"CWT": ["10", "999"]}},
        cache_manager,
        api,
        DataflowBlacklist([]),
    )
    data = json.loads(_text(res))
    assert data["status"] == "nonempty"
    assert data["available"] is True
    assert api.data_call_count == 1  # it DID probe (no provably-empty shortcut)
    assert "CWT" in data["diagnosis"]["invalid_codes"]  # 999 still reported
    assert data["diagnosis"]["fully_invalid_dims"] == []  # but the dim is not fully invalid
    assert "still probed" in data["note"]  # partial-invalid surfaced as a warning


async def test_check_data_availability_out_of_range_period_skips_probe(cache_manager):
    api = FakeApi()
    res = await handle_check_data_availability(
        {
            "dataflow_id": "DF_AGING",
            "dimension_filters": {"CWT": ["10"]},
            "start_period": "2599",
            "end_period": "2599",
        },
        cache_manager,
        api,
        DataflowBlacklist([]),
    )
    data = json.loads(_text(res))
    assert data["available"] is False
    assert data["status"] == "provably_empty"
    assert data["diagnosis"]["period_out_of_range"] is not None
    assert api.data_call_count == 0


async def test_check_data_availability_inconclusive(cache_manager):
    api = FakeApi()
    api.error_keys = {".10"}  # probe hits a generic upstream error
    res = await handle_check_data_availability(
        {"dataflow_id": "DF_AGING", "dimension_filters": {"CWT": ["10"]}},
        cache_manager,
        api,
        DataflowBlacklist([]),
    )
    data = json.loads(_text(res))
    assert data["status"] == "inconclusive"
    assert data["available"] is False
    assert data["observation_count"] is None
    assert "Could not confirm" in data["note"]


async def test_check_data_availability_unknown_dim_probes_and_warns(cache_manager):
    api = FakeApi()
    # An unknown dimension is dropped by build_key -> effective key 'all' -> returns data.
    res = await handle_check_data_availability(
        {"dataflow_id": "DF_AGING", "dimension_filters": {"TYPO_DIM": ["x"]}},
        cache_manager,
        api,
        DataflowBlacklist([]),
    )
    data = json.loads(_text(res))
    assert data["available"] is True  # not provably_empty: the real query returns rows
    assert data["status"] == "nonempty"
    assert "TYPO_DIM" in data["diagnosis"]["unknown_dimensions"]
    assert "TYPO_DIM" in data["note"]  # surfaced as a warning
    assert api.data_call_count == 1  # it DID probe (no provably-empty shortcut)


async def test_territorial_province_name_search(cache_manager):
    api = FakeApi()
    res = await handle_get_territorial_codes({"level": "province", "name": "bangkok"}, cache_manager, api)
    data = json.loads(_text(res))
    assert data["count"] == 1
    assert data["codes"][0]["code"] == "10"
    assert data["codes"][0]["level"] == "province"
