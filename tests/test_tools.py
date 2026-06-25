"""Tool-handler tests against a fake API + real (temp) cache. No network."""

from __future__ import annotations

import json

from tnso_mcp_server.api.models import (
    CodelistInfo,
    CodeValue,
    DataflowInfo,
    DatastructureInfo,
    DimensionInfo,
)
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
    ):
        self.last_data_call = {"key": key, "start": start_period, "end": end_period}
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


async def test_get_data_blacklisted(cache_manager):
    api = FakeApi()
    res = await handle_get_data(
        {"id_dataflow": "DF_AGING"}, cache_manager, api, DataflowBlacklist(["DF_AGING"])
    )
    assert "blacklisted" in _text(res).lower()


async def test_territorial_province_name_search(cache_manager):
    api = FakeApi()
    res = await handle_get_territorial_codes({"level": "province", "name": "bangkok"}, cache_manager, api)
    data = json.loads(_text(res))
    assert data["count"] == 1
    assert data["codes"][0]["code"] == "10"
    assert data["codes"][0]["level"] == "province"
