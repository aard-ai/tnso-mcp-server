"""Tool: get_concepts — resolve a TNSO SDMX concept id to its Thai/English name.

Mirrors the ISTAT design: spawns the bundled CLI in a subprocess so the heavy
conceptscheme parse (~3 MB) runs out-of-process; the result is cached on disk.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from typing import Any

from mcp.types import TextContent

from ..api.client import ApiClient
from ..api.models import GetConceptsInput
from ..cache.manager import CacheManager
from .helpers import text_response

logger = logging.getLogger(__name__)

# Upper bound for the out-of-process concept lookup. Generous enough for a cold
# fetch + parse (with retries), but prevents a stalled CLI from hanging the request.
CONCEPT_LOOKUP_TIMEOUT = float(os.getenv("CONCEPT_LOOKUP_TIMEOUT_SECONDS", "120"))


async def handle_get_concepts(
    arguments: dict[str, Any], cache: CacheManager, api: ApiClient
) -> list[TextContent]:
    """Resolve a concept id to its Thai/English name via the bundled CLI subprocess."""
    params = GetConceptsInput.model_validate(arguments or {})
    lang = params.lang if params.lang in ("th", "en") else "en"

    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tnso_mcp_server.cli.get_concepts_cli",
        params.concept_id,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(
            proc.communicate(), timeout=CONCEPT_LOOKUP_TIMEOUT
        )
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()  # reap the killed child
        return text_response(f"Concept lookup for {params.concept_id!r} timed out.")

    try:
        data = json.loads(stdout.decode("utf-8").strip() or "{}")
    except json.JSONDecodeError:
        err = stderr.decode("utf-8", errors="replace")[:300]
        return text_response(f"Could not look up concept {params.concept_id!r}: {err}")

    if not data.get("found"):
        return text_response(f"Concept {params.concept_id!r} not found in any TNSO concept scheme.")

    name = data.get(f"name_{lang}") or data.get("name_en") or data.get("name_th") or ""
    scheme = data.get("scheme_id", "")
    return text_response(f"{name}" + (f"  (scheme: {scheme})" if scheme else ""))
