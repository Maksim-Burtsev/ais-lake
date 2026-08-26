"""Structured logging: single-line key=value records, incidents included.

Incidents (ws_connect / ws_disconnect / …) are the raw material for /status'
honest incident log — keep them machine-parseable from day one.
"""

import logging
import sys
from typing import Any


def setup() -> None:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.handlers[:] = [handler]


def kv(event: str, **fields: Any) -> str:
    parts = [f"event={event}"] + [f"{k}={v}" for k, v in fields.items()]
    return " ".join(parts)
