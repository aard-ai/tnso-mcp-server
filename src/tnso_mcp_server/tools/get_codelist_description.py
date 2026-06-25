"""Tool: get_codelist_description — Thai/English labels for every code in a codelist."""

from __future__ import annotations

import logging
from typing import Any

from mcp.types import TextContent

from ..api.client import ApiClient
from ..api.models import GetCodelistDescriptionInput
from ..cache.manager import CacheManager
from .helpers import format_json_response, get_cached_codelist

logger = logging.getLogger(__name__)


async def handle_get_codelist_description(
    arguments: dict[str, Any], cache: CacheManager, api: ApiClient
) -> list[TextContent]:
    """Return the Thai/English labels for every code in a codelist."""
    params = GetCodelistDescriptionInput.model_validate(arguments or {})
    codelist = await get_cached_codelist(cache, api, params.codelist_id)
    return format_json_response(codelist)
