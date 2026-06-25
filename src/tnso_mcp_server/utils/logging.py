"""Logging setup.

IMPORTANT: an MCP stdio server uses **stdout** for the protocol, so all logs must
go to stderr (and optionally a rotating file). Never log to stdout.
"""

from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_FORMAT = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"


def setup_logging(level: str = "INFO", log_dir: str | None = None) -> None:
    """Configure root logging to stderr (+ optional rotating file). Safe to call repeatedly."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, str(level).upper(), logging.INFO))

    # Avoid duplicate handlers if called more than once; close each as we remove it so
    # repeated reconfiguration doesn't leak file descriptors from old file handlers.
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter(_FORMAT)

    # stderr (NOT stdout — stdout is the MCP transport).
    stream = logging.StreamHandler()
    stream.setFormatter(formatter)
    root.addHandler(stream)

    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            file_handler = RotatingFileHandler(
                os.path.join(log_dir, "tnso_mcp_server.log"),
                maxBytes=10 * 1024 * 1024,
                backupCount=5,
                encoding="utf-8",
            )
            file_handler.setFormatter(formatter)
            root.addHandler(file_handler)
        except OSError:
            root.warning("Could not create log dir %s; logging to stderr only.", log_dir)

    # Quiet noisy HTTP libraries.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
