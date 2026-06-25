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
class DiscoverDataflowsInput(BaseModel):
    """Input for discover_dataflows: optional comma-separated keywords."""

    keywords: str = ""


class GetStructureInput(BaseModel):
    """Input for get_structure: a data structure (DSD) id."""

    id_datastructure: str


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
    """Input for get_data: dataflow id plus optional filters, period, and layout."""

    id_dataflow: str = Field(validation_alias=AliasChoices("id_dataflow", "dataflow_id"))
    dimension_filters: dict[str, list[str]] | None = Field(
        default=None,
        validation_alias=AliasChoices("dimension_filters", "filters"),
    )
    start_period: str | None = None
    end_period: str | None = None
    detail: str = "full"
    dimension_at_observation: str | None = None

    @field_validator("dimension_filters", mode="before")
    @classmethod
    def _coerce_filters(cls, v: Any) -> Any:
        """Accept either a dict or a JSON-encoded string (LLMs often send a string)."""
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
