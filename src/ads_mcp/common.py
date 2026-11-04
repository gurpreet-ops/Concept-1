"""Helpers shared by the Meta and Google Ads servers."""

from __future__ import annotations

import logging
import os
import re

from mcp.server.mcpserver.exceptions import ToolError

# httpx logs full request URLs at INFO, which would leak the Meta access token.
logging.getLogger("httpx").setLevel(logging.WARNING)

_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def require_env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ToolError(f"Missing environment variable {name}. See README.md for setup.")
    return value


def check_date(value: str, label: str) -> str:
    if not _DATE_RE.match(value):
        raise ToolError(f"{label} must be YYYY-MM-DD, got {value!r}")
    return value


def flatten(obj: dict, prefix: str = "") -> dict:
    """Flatten nested dicts into dotted keys: {"a": {"b": 1}} -> {"a.b": 1}."""
    out: dict = {}
    for key, value in obj.items():
        name = f"{prefix}{key}"
        if isinstance(value, dict):
            out.update(flatten(value, f"{name}."))
        else:
            out[name] = value
    return out
