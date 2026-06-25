"""Tool: get_structure — dimensions + codelists for a data structure (DSD)."""

from __future__ import annotations

import logging
from typing import Any

from mcp.types import TextContent

from ..api.client import ApiClient
from ..api.models import GetStructureInput
from ..cache.manager import CacheManager
from .helpers import format_json_response, get_cached_datastructure

logger = logging.getLogger(__name__)


async def handle_get_structure(
    arguments: dict[str, Any], cache: CacheManager, api: ApiClient
) -> list[TextContent]:
    """Return the ordered dimensions + codelists for a data structure (DSD)."""
    params = GetStructureInput.model_validate(arguments or {})
    dsd = await get_cached_datastructure(cache, api, params.id_datastructure)
    return format_json_response(dsd)
