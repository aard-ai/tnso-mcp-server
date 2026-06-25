import pytest
from pydantic import ValidationError

from tnso_mcp_server.api.models import GetConstraintsInput, GetDataInput
from tnso_mcp_server.server import _tool_definitions


def _schema(name):
    return next(t.inputSchema for t in _tool_definitions() if t.name == name)


def test_dataflow_id_param_name_is_consistent_across_tools():
    """The advertised schema must use one name for the dataflow id everywhere.

    The MCP SDK validates arguments against inputSchema *before* dispatch, so an
    advertised name the pydantic model doesn't treat as canonical (or that differs
    between tools) gets rejected even though AliasChoices would accept it. Lock the
    name to 'dataflow_id' so get_constraints -> get_data calls don't fail.
    """
    for tool in ("get_constraints", "get_data"):
        schema = _schema(tool)
        assert "dataflow_id" in schema["properties"], f"{tool} lost dataflow_id"
        assert schema["required"] == ["dataflow_id"], f"{tool} requires the wrong key"
        assert "id_dataflow" not in schema["properties"], f"{tool} re-introduced id_dataflow"


def test_get_data_model_accepts_the_advertised_required_name():
    """Whatever get_data advertises as required must validate through the model."""
    required = _schema("get_data")["required"][0]
    assert GetDataInput.model_validate({required: "DF_X"}).dataflow_id == "DF_X"


def test_getdata_accepts_aliases_and_dict_filters():
    m = GetDataInput.model_validate({"dataflow_id": "DF_X", "filters": {"CWT": ["10"]}})
    assert m.dataflow_id == "DF_X"
    assert m.dimension_filters == {"CWT": ["10"]}
    # legacy id_dataflow alias still resolves to the canonical dataflow_id field
    assert GetDataInput.model_validate({"id_dataflow": "DF_Y"}).dataflow_id == "DF_Y"


def test_getdata_coerces_json_string_filters():
    m = GetDataInput.model_validate({"id_dataflow": "DF_X", "dimension_filters": '{"SEX": ["_T"]}'})
    assert m.dimension_filters == {"SEX": ["_T"]}


def test_getdata_rejects_bad_json_filters():
    with pytest.raises(ValidationError):
        GetDataInput.model_validate({"id_dataflow": "DF_X", "dimension_filters": "not json"})


def test_constraints_alias():
    assert GetConstraintsInput.model_validate({"id_dataflow": "DF_X"}).dataflow_id == "DF_X"
    assert GetConstraintsInput.model_validate({"dataflow_id": "DF_Y"}).dataflow_id == "DF_Y"
