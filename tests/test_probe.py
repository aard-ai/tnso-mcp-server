"""Unit tests for the probe engine: diagnosis, relaxation ordering, bounded probe."""

from __future__ import annotations

from tnso_mcp_server.tools import probe

from tests.test_tools import FakeApi

DIMENSION_ORDER = ["POP_IND", "AREA", "CWT"]
AVAILABLE = {"POP_IND": ["DEM_IND101"], "AREA": ["TH", "10"], "CWT": ["10", "58"]}
# Cached time_range bounds are full datetimes, not bare years.
TIME_RANGE = ("2557-01-01T00:00:00", "2567-12-31T23:59:59")


# --------------------------------------------------------------------------- #
# diagnose_filters
# --------------------------------------------------------------------------- #
def test_diagnose_flags_invalid_code_and_ignores_valid():
    diag = probe.diagnose_filters(
        {"POP_IND": ["DEM_IND101"], "CWT": ["999"]},
        AVAILABLE,
        DIMENSION_ORDER,
        TIME_RANGE,
        None,
        None,
    )
    assert "POP_IND" not in diag["invalid_codes"]  # present in available -> valid
    assert diag["invalid_codes"]["CWT"]["given"] == ["999"]
    assert diag["invalid_codes"]["CWT"]["valid_sample"] == ["10", "58"]


def test_diagnose_flags_unknown_dimension():
    diag = probe.diagnose_filters(
        {"NOT_A_DIM": ["x"]}, AVAILABLE, DIMENSION_ORDER, TIME_RANGE, None, None
    )
    assert diag["unknown_dimensions"] == ["NOT_A_DIM"]
    assert diag["invalid_codes"] == {}


def test_diagnose_flags_out_of_range_period():
    diag = probe.diagnose_filters(
        {}, AVAILABLE, DIMENSION_ORDER, TIME_RANGE, "2599", "2599"
    )
    assert diag["period_out_of_range"] is not None
    assert diag["period_out_of_range"]["requested"] == ["2599", "2599"]
    assert diag["period_out_of_range"]["available_years"] == ["2557", "2567"]


def test_diagnose_first_available_year_is_in_range():
    diag = probe.diagnose_filters(
        {}, AVAILABLE, DIMENSION_ORDER, TIME_RANGE, "2557", "2557"
    )
    assert diag["period_out_of_range"] is None


def test_diagnose_no_availability_for_dim_cannot_judge():
    # SEX has no availability info -> its codes can't be judged invalid.
    diag = probe.diagnose_filters(
        {"SEX": ["_T"]}, AVAILABLE, ["POP_IND", "SEX", "AREA", "CWT"], TIME_RANGE, None, None
    )
    assert diag["invalid_codes"] == {}
    assert diag["unknown_dimensions"] == []


# --------------------------------------------------------------------------- #
# relaxation_candidates
# --------------------------------------------------------------------------- #
def test_relaxation_orders_prioritized_first_geo_last_droptime_last():
    cands = probe.relaxation_candidates(
        {"POP_IND": ["DEM_IND101"], "AREA": ["TH"], "CWT": ["999"]},
        DIMENSION_ORDER,
        "2567",
        "2567",
        prioritize={"CWT"},  # diagnosed-invalid dim comes first
    )
    summaries = [c.change_summary for c in cands]
    # CWT (prioritized) before POP_IND (non-geo) before AREA (geo); drop-time last.
    assert cands[0].change_summary.startswith("Removed CWT filter")
    assert cands[-1].drop_time is True
    # geo AREA must come after the non-geo POP_IND
    assert summaries.index("Removed POP_IND filter (was ['DEM_IND101'])") < summaries.index(
        "Removed AREA filter (was ['TH'])"
    )
    # never relaxes the time dimension as a normal dimension
    assert all("TIME_PERIOD" not in (c.change_summary) for c in cands if not c.drop_time)
    # drop-time summary includes the original period
    assert "2567–2567" in cands[-1].change_summary


def test_relaxation_droptime_first_when_period_out_of_range():
    cands = probe.relaxation_candidates(
        {"CWT": ["10"]},
        DIMENSION_ORDER,
        "2599",
        "2599",
        prioritize=set(),
        period_out_of_range=True,
    )
    assert cands[0].drop_time is True
    assert "2599–2599" in cands[0].change_summary


def test_relaxation_non_geo_before_geo_without_priority():
    cands = probe.relaxation_candidates(
        {"POP_IND": ["DEM_IND101"], "CWT": ["10"]},
        DIMENSION_ORDER,
        None,
        None,
        prioritize=set(),
    )
    order = [c.change_summary for c in cands if not c.drop_time]
    assert order[0].startswith("Removed POP_IND")  # non-geo first
    assert order[1].startswith("Removed CWT")  # geo last


# --------------------------------------------------------------------------- #
# count_data_rows
# --------------------------------------------------------------------------- #
def test_count_data_rows_header_only_is_zero():
    assert probe.count_data_rows("DATAFLOW,POP_IND,OBS_VALUE\n") == 0
    assert probe.count_data_rows("") == 0


def test_count_data_rows_counts_data_rows():
    csv_text = "DATAFLOW,POP_IND,OBS_VALUE\nA,1,10\nB,2,20\nC,3,30\n"
    assert probe.count_data_rows(csv_text) == 3


# --------------------------------------------------------------------------- #
# probe_nonempty
# --------------------------------------------------------------------------- #
async def test_probe_nonempty_returns_nonempty_with_count(cache_manager):
    api = FakeApi()
    result = await probe.probe_nonempty(
        cache_manager, api, "DF_AGING", "1.0", ".10", "2567", "2567", first_n=1
    )
    assert result["status"] == "nonempty"
    assert result["observation_count"] == 1
    assert api.last_data_call["first_n"] == 1


async def test_probe_nonempty_returns_empty_on_no_records(cache_manager):
    api = FakeApi()
    api.no_data_keys = {".10"}
    result = await probe.probe_nonempty(
        cache_manager, api, "DF_AGING", "1.0", ".10", "2567", "2567", first_n=1
    )
    assert result["status"] == "empty"
    assert result["observation_count"] == 0


async def test_probe_nonempty_returns_inconclusive_on_generic_error(cache_manager):
    api = FakeApi()
    api.error_keys = {".10"}
    result = await probe.probe_nonempty(
        cache_manager, api, "DF_AGING", "1.0", ".10", "2567", "2567", first_n=1
    )
    assert result["status"] == "inconclusive"
    assert result["observation_count"] is None


async def test_probe_nonempty_is_cached(cache_manager):
    api = FakeApi()
    await probe.probe_nonempty(
        cache_manager, api, "DF_AGING", "1.0", ".10", "2567", "2567", first_n=1
    )
    await probe.probe_nonempty(
        cache_manager, api, "DF_AGING", "1.0", ".10", "2567", "2567", first_n=1
    )
    assert api.data_call_count == 1  # second probe served from cache


async def test_probe_nonempty_omits_first_n_when_zero(cache_manager):
    api = FakeApi()
    await probe.probe_nonempty(
        cache_manager, api, "DF_AGING", "1.0", ".10", "2567", "2567", first_n=0
    )
    assert api.last_data_call["first_n"] is None
