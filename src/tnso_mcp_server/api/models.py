"""Pydantic models for tool inputs and SDMX responses.

Mapping note (ISTAT -> TNSO): ISTAT exposes Italian/English labels (``*_it`` / ``*_en``);
TNSO exposes Thai/English, so the bilingual fields are ``name_th`` / ``name_en``.
"""

from __future__ import annotations

import json
from typing import Any

from pydantic import AliasChoices, BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# Response / data models
# --------------------------------------------------------------------------- #
class DataflowInfo(BaseModel):
    """A TNSO dataflow (dataset): bilingual names/descriptions and its DSD id."""

    id: str
    name_en: str = ""
    name_th: str = ""
    description_en: str = ""
    description_th: str = ""
    version: str = ""
    agency: str = ""
    id_datastructure: str = ""


class DimensionInfo(BaseModel):
    """One DSD dimension and the codelist it draws its values from."""

    dimension: str
    codelist: str = ""


class DatastructureInfo(BaseModel):
    """A data structure definition (DSD): its id and ordered dimensions."""

    id_datastructure: str
    dimensions: list[DimensionInfo] = Field(default_factory=list)


class CodeValue(BaseModel):
    """A single code with its Thai/English labels."""

    code: str
    name_en: str = ""
    name_th: str = ""


class CodelistInfo(BaseModel):
    """A codelist: its id and the code values it contains."""

    id_codelist: str
    values: list[CodeValue] = Field(default_factory=list)


class ConceptInfo(BaseModel):
    """An SDMX concept with its Thai/English names."""

    id: str
    name_en: str = ""
    name_th: str = ""


class ConceptSchemeInfo(BaseModel):
    """An SDMX concept scheme and the concepts it defines."""

    id: str
    agency: str = ""
    version: str = ""
    name_en: str = ""
    concepts: list[ConceptInfo] = Field(default_factory=list)


# --------------------------------------------------------------------------- #
# Tool input models
# --------------------------------------------------------------------------- #
def _coerce_dimension_filters(v: Any) -> Any:
    """Accept either a dict or a JSON-encoded string (LLMs often send a string).

    Shared by ``get_data`` and ``check_data_availability`` so the two never drift.
    """
    if v is None or isinstance(v, dict):
        return v
    if isinstance(v, str):
        v = v.strip()
        if not v:
            return None
        try:
            parsed = json.loads(v)
        except json.JSONDecodeError as exc:  # pragma: no cover - defensive
            raise ValueError(f"dimension_filters must be a JSON object: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ValueError("dimension_filters JSON must decode to an object")
        return parsed
    raise ValueError("dimension_filters must be an object or a JSON string")


class DiscoverDataflowsInput(BaseModel):
    """Input for discover_dataflows: optional keyword and coverage filters.

    A dataflow matches the keywords if it contains *any* of them (OR); set ``match_all``
    to require *every* keyword (AND). ``covers`` additionally keeps only dataflows whose
    published availability includes the given codes, e.g. ``{"CWT": ["10", "20"]}`` ->
    only dataflows that actually carry both Bangkok and Chon Buri. Coverage is checked
    **per dimension** (marginal availability), not as a joint combination. ``covers`` may
    be a dict or its JSON-encoded string form.
    """

    keywords: str = ""
    match_all: bool = False
    covers: dict[str, list[str]] | None = Field(
        default=None,
        validation_alias=AliasChoices("covers", "covers_codes"),
    )

    _coerce_covers = field_validator("covers", mode="before")(_coerce_dimension_filters)


class GetStructureInput(BaseModel):
    """Input for get_structure: a data structure (DSD) id.

    Accepts ``datastructure_id`` (canonical, matches the other ``*_id`` tools) or
    the legacy ``id_datastructure`` alias.
    """

    datastructure_id: str = Field(
        validation_alias=AliasChoices("datastructure_id", "id_datastructure")
    )


class GetConstraintsInput(BaseModel):
    """Input for get_constraints (accepts ``dataflow_id`` or ``id_dataflow``)."""

    dataflow_id: str = Field(validation_alias=AliasChoices("dataflow_id", "id_dataflow"))


class GetCodelistDescriptionInput(BaseModel):
    """Input for get_codelist_description: a codelist id."""

    codelist_id: str


class GetConceptsInput(BaseModel):
    """Input for get_concepts: a concept id and target language."""

    concept_id: str
    lang: str = "en"


class GetDataInput(BaseModel):
    """Input for get_data: dataflow id plus optional filters, period, and layout.

    Accepts ``dataflow_id`` (canonical, matches every other tool) or the legacy
    ``id_dataflow`` alias.
    """

    dataflow_id: str = Field(validation_alias=AliasChoices("dataflow_id", "id_dataflow"))
    dimension_filters: dict[str, list[str]] | None = Field(
        default=None,
        validation_alias=AliasChoices("dimension_filters", "filters"),
    )
    start_period: str | None = None
    end_period: str | None = None
    detail: str = "full"
    dimension_at_observation: str | None = None

    _coerce_filters = field_validator("dimension_filters", mode="before")(
        _coerce_dimension_filters
    )


class CheckDataAvailabilityInput(BaseModel):
    """Input for check_data_availability: a dataflow id, a filter combo, optional period.

    Accepts ``dataflow_id`` (canonical) or the legacy ``id_dataflow`` alias, and (like
    ``get_data``) a ``dimension_filters`` dict or its JSON-encoded string form.
    """

    dataflow_id: str = Field(validation_alias=AliasChoices("dataflow_id", "id_dataflow"))
    dimension_filters: dict[str, list[str]] | None = Field(
        default=None,
        validation_alias=AliasChoices("dimension_filters", "filters"),
    )
    start_period: str | None = None
    end_period: str | None = None

    _coerce_filters = field_validator("dimension_filters", mode="before")(
        _coerce_dimension_filters
    )


class GetTerritorialCodesInput(BaseModel):
    """Input for get_territorial_codes: a geography level and/or name filter."""

    level: str | None = None
    name: str | None = None


class GetCacheDiagnosticsInput(BaseModel):
    """Input for get_cache_diagnostics; ``debug`` opts in to sensitive details."""

    debug: bool = False


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class ApiError(Exception):
    """Raised for TNSO API failures; carries an optional HTTP status code."""

    def __init__(self, message: str, status_code: int = 0) -> None:
        """Store the human-readable message and optional HTTP status code."""
        super().__init__(message)
        self.message = message
        self.status_code = status_code


class NoRecordsError(ApiError):
    """Raised when the upstream returns HTTP 404 NoRecordsFound (query is valid but matches no data)."""
