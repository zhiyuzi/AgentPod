"""JSON structured logging for AgentPod."""

from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone


class JSONFormatter(logging.Formatter):
    """Formats log records as single-line JSON objects.

    Output format::

        {"ts": "2026-02-26T10:30:00Z", "level": "info", "event": "...", ...}
    """

    _LEVEL_MAP = {
        "DEBUG": "debug",
        "INFO": "info",
        "WARNING": "warn",
        "ERROR": "error",
        "CRITICAL": "error",
    }

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "level": self._LEVEL_MAP.get(record.levelname, record.levelname.lower()),
            "event": record.getMessage(),
        }
        # Merge any extra fields passed via `extra={"user_id": ..., ...}`
        for key in ("user_id", "session_id", "model", "tool", "duration_ms",
                     "turns", "input_tokens", "output_tokens", "cost",
                     "path", "size", "status_code", "is_error"):
            value = getattr(record, key, None)
            if value is not None:
                payload[key] = value
        return json.dumps(payload, ensure_ascii=False)


_LOG_LEVEL_MAP = {
    "debug": logging.DEBUG,
    "info": logging.INFO,
    "warn": logging.WARNING,
    "warning": logging.WARNING,
    "error": logging.ERROR,
}


def get_logger(name: str) -> logging.Logger:
    """Return a logger that emits JSON to stdout.

    The log level is controlled by the ``AGENTPOD_LOG_LEVEL`` environment
    variable (default ``info``).
    """
    logger = logging.getLogger(name)
    if not logger.handlers:
        level_name = os.environ.get("AGENTPOD_LOG_LEVEL", "info").lower()
        logger.setLevel(_LOG_LEVEL_MAP.get(level_name, logging.INFO))
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JSONFormatter())
        logger.addHandler(handler)
        logger.propagate = False
    return logger
