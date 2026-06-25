"""Dataflow blacklist.

Lets operators hide specific dataflows from ``discover_dataflows`` / ``get_data``
via the ``DATAFLOW_BLACKLIST`` env var (comma-separated ids). Default: empty.

NOTE: unlike ISTAT, TNSO marks most dataflows ``isFinal=false`` /
``NonProductionDataflow``; we deliberately do NOT auto-filter on that annotation,
otherwise almost everything would be hidden.
"""

from __future__ import annotations

import logging
import os
from typing import Iterable

logger = logging.getLogger(__name__)


class DataflowBlacklist:
    """A set of dataflow ids to hide, seeded from the ``DATAFLOW_BLACKLIST`` env var."""

    def __init__(self, blacklist_ids: Iterable[str] | None = None) -> None:
        """Build the blacklist from ``blacklist_ids`` or the env var when omitted."""
        if blacklist_ids is None:
            raw = os.getenv("DATAFLOW_BLACKLIST", "")
            blacklist_ids = [x.strip() for x in raw.split(",") if x.strip()]
        self._ids: set[str] = set(blacklist_ids)
        logger.debug("Blacklist initialized with %d id(s).", len(self._ids))

    def is_blacklisted(self, dataflow_id: str | None) -> bool:
        """Return True if ``dataflow_id`` is on the blacklist."""
        return dataflow_id in self._ids

    def filter_dataflows(self, dataflows: list) -> list:
        """Return ``dataflows`` with any blacklisted entries removed."""
        if not self._ids:
            return dataflows
        return [df for df in dataflows if not self.is_blacklisted(getattr(df, "id", None))]

    def get_blacklisted_ids(self) -> set[str]:
        """Return a copy of the blacklisted ids."""
        return set(self._ids)

    def add_to_blacklist(self, dataflow_id: str) -> None:
        """Add a dataflow id to the blacklist."""
        self._ids.add(dataflow_id)

    def remove_from_blacklist(self, dataflow_id: str) -> None:
        """Remove a dataflow id from the blacklist (no-op if absent)."""
        self._ids.discard(dataflow_id)
