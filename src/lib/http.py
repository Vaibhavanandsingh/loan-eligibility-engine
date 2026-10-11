"""Small helpers shared by the HTTP API handlers.

CORS headers are not set here: the HTTP API adds them (see `httpApi.cors`
in serverless.yml), and setting them twice makes browsers reject the response.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any


def configure_logging() -> None:
    """Lambda installs a root handler already; we only set the level."""
    logging.getLogger().setLevel(os.environ.get("LOG_LEVEL", "INFO"))


def json_response(status: int, body: Any) -> dict:
    """Build an API Gateway (HTTP API v2) proxy response."""
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        # default=str covers Decimal, datetime and UUID values from Postgres.
        "body": json.dumps(body, default=str),
    }
