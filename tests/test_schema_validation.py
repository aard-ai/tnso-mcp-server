"""Exercises the MCP input-schema validation layer.

Before dispatching a tool call, the MCP SDK runs
``jsonschema.validate(instance=arguments, schema=tool.inputSchema)`` (see
``mcp/server/lowlevel/server.py``). If validation fails the handler is never
reached and the client gets "Input validation error: ...".

Every other test calls the handlers directly with a dict, which bypasses that
check -- so a schema that is *stricter* than the handler (rejecting arguments the
pydantic model would accept) sails through the suite and only fails against a live
client. These tests close that gap by validating against the advertised schema the
same way the SDK does.
"""

import jsonschema
import pytest

from tnso_mcp_server.api.models import (
    CheckDataAvailabilityInput,
    GetConstraintsInput,
    GetDataInput,
    GetStructureInput,
)
from tnso_mcp_server.server import _tool_definitions

TOOLS = {t.name: t for t in _tool_definitions()}


def validate(tool_name, arguments):
    """Mirror the SDK's pre-dispatch check; raises jsonschema.ValidationError on bad input."""
    jsonschema.validate(instance=arguments, schema=TOOLS[tool_name].inputSchema)


# --------------------------------------------------------------------------- #
# The advertised schemas must themselves be well-formed and self-consistent
# --------------------------------------------------------------------------- #
def test_every_advertised_schema_is_valid_json_schema():
    for name, tool in TOOLS.items():
        jsonschema.Draft202012Validator.check_schema(tool.inputSchema)


def test_every_required_name_is_declared_in_properties():
    """A required key absent from properties can never be satisfied by a caller."""
    for name, tool in TOOLS.items():
        schema = tool.inputSchema
        props = schema.get("properties", {})
        for req in schema.get("required", []):
            assert req in props, f"{name}: required '{req}' missing from properties"


# --------------------------------------------------------------------------- #
# Representative valid payloads must pass the schema layer
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "tool_name, arguments",
    [
        ("discover_dataflows", {}),
        ("discover_dataflows", {"keywords": "population, aging"}),
        ("discover_dataflows", {"keywords": "education, health", "match_all": True}),
        ("discover_dataflows", {"covers": {"CWT": ["10", "20"]}}),
        # covers as a JSON-encoded string: the schema must accept what the model coerces.
        ("discover_dataflows", {"covers": '{"CWT": ["10", "20"]}'}),
        ("get_structure", {"datastructure_id": "DSD_01DI_IND_AGING"}),
        ("get_constraints", {"dataflow_id": "DF_01DI_IND_AGING"}),
        ("get_codelist_description", {"codelist_id": "CL_CWT"}),
        ("get_concepts", {"concept_id": "POP_IND"}),
        ("get_concepts", {"concept_id": "POP_IND", "lang": "th"}),
        ("get_data", {"dataflow_id": "DF_X"}),
        (
            "get_data",
            {
                "dataflow_id": "DF_X",
                "dimension_filters": {"SEX": ["_T"], "CWT": ["10", "11"]},
                "start_period": "2560",
                "end_period": "2567",
                "detail": "dataonly",
            },
        ),
        # dimension_filters as a JSON-encoded string: schema must accept what
        # GetDataInput._coerce_filters accepts (round-trip asserted below).
        ("get_data", {"dataflow_id": "DF_X", "dimension_filters": '{"SEX": ["_T"]}'}),
        ("check_data_availability", {"dataflow_id": "DF_X"}),
        (
            "check_data_availability",
            {
                "dataflow_id": "DF_X",
                "dimension_filters": {"SEX": ["_T"], "CWT": ["10"]},
                "start_period": "2560",
                "end_period": "2567",
            },
        ),
        ("check_data_availability", {"dataflow_id": "DF_X", "dimension_filters": '{"SEX": ["_T"]}'}),
        ("get_territorial_codes", {}),
        ("get_territorial_codes", {"level": "province", "name": "Bangkok"}),
        ("get_cache_diagnostics", {}),
        ("get_cache_diagnostics", {"debug": True}),
    ],
)
def test_valid_payloads_pass_schema_validation(tool_name, arguments):
    validate(tool_name, arguments)


# --------------------------------------------------------------------------- #
# Malformed payloads must be rejected by the schema layer
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "tool_name, arguments",
    [
        ("get_structure", {}),                                    # missing required id
        ("get_constraints", {}),                                  # missing required id
        ("get_data", {}),                                         # missing required id
        ("get_concepts", {"concept_id": "X", "lang": "fr"}),      # lang not in enum
        ("get_data", {"dataflow_id": "X", "detail": "bogus"}),    # detail not in enum
        ("get_data", {"dataflow_id": "X", "dimension_filters": {"SEX": "T"}}),  # value not an array
        ("check_data_availability", {}),                          # missing required id
        ("check_data_availability", {"dataflow_id": "X", "dimension_filters": {"SEX": "T"}}),  # value not an array
        ("get_territorial_codes", {"level": "galaxy"}),           # level not in enum
        ("get_cache_diagnostics", {"debug": "yes"}),              # debug not a boolean
        ("discover_dataflows", {"covers": {"CWT": []}}),          # empty code list (minItems: 1)
    ],
)
def test_invalid_payloads_are_rejected_by_schema(tool_name, arguments):
    with pytest.raises(jsonschema.ValidationError):
        validate(tool_name, arguments)


# --------------------------------------------------------------------------- #
# Schema <-> handler-model agreement: the invariant that the dataflow_id /
# id_datastructure bugs both violated.
# --------------------------------------------------------------------------- #
ID_CASES = [
    ("get_structure", GetStructureInput, "datastructure_id", "id_datastructure"),
    ("get_constraints", GetConstraintsInput, "dataflow_id", "id_dataflow"),
    ("get_data", GetDataInput, "dataflow_id", "id_dataflow"),
    ("check_data_availability", CheckDataAvailabilityInput, "dataflow_id", "id_dataflow"),
]


@pytest.mark.parametrize("tool_name, model, canonical, legacy", ID_CASES)
def test_canonical_id_passes_schema_and_is_the_model_field(tool_name, model, canonical, legacy):
    """The name the schema advertises is exactly the model's canonical field name."""
    validate(tool_name, {canonical: "ID_X"})
    assert getattr(model.model_validate({canonical: "ID_X"}), canonical) == "ID_X"


@pytest.mark.parametrize("tool_name, model, canonical, legacy", ID_CASES)
def test_legacy_alias_no_longer_advertised_but_still_accepted_in_process(
    tool_name, model, canonical, legacy
):
    """Legacy aliases are dropped from the advertised schema (so the surface is
    consistent) yet AliasChoices keeps direct/legacy callers working."""
    with pytest.raises(jsonschema.ValidationError):
        validate(tool_name, {legacy: "ID_X"})
    assert getattr(model.model_validate({legacy: "ID_X"}), canonical) == "ID_X"


def test_json_string_dimension_filters_round_trip():
    """A JSON-string dimension_filters passes the schema *and* the handler model."""
    args = {"dataflow_id": "DF_X", "dimension_filters": '{"SEX": ["_T"]}'}
    validate("get_data", args)  # schema layer
    assert GetDataInput.model_validate(args).dimension_filters == {"SEX": ["_T"]}
