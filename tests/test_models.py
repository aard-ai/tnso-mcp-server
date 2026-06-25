import pytest
from pydantic import ValidationError

from tnso_mcp_server.api.models import GetConstraintsInput, GetDataInput


def test_getdata_accepts_aliases_and_dict_filters():
    m = GetDataInput.model_validate({"dataflow_id": "DF_X", "filters": {"CWT": ["10"]}})
    assert m.id_dataflow == "DF_X"
    assert m.dimension_filters == {"CWT": ["10"]}


def test_getdata_coerces_json_string_filters():
    m = GetDataInput.model_validate({"id_dataflow": "DF_X", "dimension_filters": '{"SEX": ["_T"]}'})
    assert m.dimension_filters == {"SEX": ["_T"]}


def test_getdata_rejects_bad_json_filters():
    with pytest.raises(ValidationError):
        GetDataInput.model_validate({"id_dataflow": "DF_X", "dimension_filters": "not json"})


def test_constraints_alias():
    assert GetConstraintsInput.model_validate({"id_dataflow": "DF_X"}).dataflow_id == "DF_X"
    assert GetConstraintsInput.model_validate({"dataflow_id": "DF_Y"}).dataflow_id == "DF_Y"
