"""Tool: get_cache_diagnostics — inspect the persistent cache (debugging)."""

from __future__ import annotations

import logging
import os
from typing import Any

from mcp.types import TextContent

from ..api.client import ApiClient
from ..api.models import GetCacheDiagnosticsInput
from ..cache.manager import CacheManager
from .helpers import format_json_response

logger = logging.getLogger(__name__)


async def handle_get_cache_diagnostics(
    arguments: dict[str, Any], cache: CacheManager, api: ApiClient
) -> list[TextContent]:
    """Report persistent-cache health.

    By default returns only non-sensitive status (exists, size, writability). The
    absolute cache path and the cached request keys leak host filesystem layout and
    request identifiers, so they are returned only when ``debug=true`` is passed.
    """
    params = GetCacheDiagnosticsInput.model_validate(arguments or {})
    pc = cache.persistent

    info: dict[str, Any] = {"cache_exists": os.path.isdir(pc.cache_dir)}

    try:
        info["persistent_cache_size"] = pc.size()
    except Exception as exc:  # pragma: no cover - defensive
        # Coarse status by default; raw exception text (which can leak backend
        # internals) only when debugging is explicitly enabled.
        info["error"] = "persistent_cache_unavailable"
        if params.debug:
            info["error_detail"] = str(exc)

    # Coarse writability probe — no sensitive data.
    try:
        test_path = os.path.join(pc.cache_dir, ".write_test")
        with open(test_path, "w", encoding="utf-8") as fh:
            fh.write("ok")
        os.remove(test_path)
        info["cache_writable"] = True
    except OSError:
        info["cache_writable"] = False

    # Sensitive details (host path, cached request identifiers) only on explicit opt-in.
    if params.debug:
        info["cache_path"] = os.path.abspath(pc.cache_dir)
        try:
            info["persistent_cache_keys"] = pc.keys()[:200]
        except Exception as exc:  # pragma: no cover - defensive
            info["keys_error"] = str(exc)

    return format_json_response(info)
