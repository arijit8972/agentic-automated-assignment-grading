"""Project logger factory.

Use this instead of ``print`` or bare ``logging`` calls::

    from src.logging.logger import get_logger
    logger = get_logger(__name__)
    logger.info("processing %s", path)

The root handler is configured once, on first ``get_logger`` call. The log level
is read from the ``LOG_LEVEL`` environment variable (default ``INFO``).
"""

from __future__ import annotations

import logging
import os
import sys

_LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
_configured = False


def _configure_root() -> None:
    global _configured
    if _configured:
        return
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(_LOG_FORMAT))
    root = logging.getLogger()
    root.setLevel(level)
    root.addHandler(handler)
    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for ``name``."""
    _configure_root()
    return logging.getLogger(name)
