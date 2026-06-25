"""Input validation helpers."""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# TNSO dataflow ids look like ``DF_01DI_IND_AGING`` — alphanumerics + underscore.
_DATAFLOW_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")


def validate_keywords(keywords: str | None) -> list[str]:
    """Split a comma-separated keyword string into a cleaned, lowercased list."""
    if not keywords:
        return []
    result = [k.strip().lower() for k in keywords.split(",") if k.strip()]
    logger.debug("Parsed keywords: %s", result)
    return result


def validate_dataflow_id(dataflow_id: str | None) -> bool:
    """Return True if the dataflow id is non-empty and safe to interpolate into a URL."""
    return bool(dataflow_id) and bool(_DATAFLOW_ID_RE.match(dataflow_id))
