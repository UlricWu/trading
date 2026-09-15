# filepath: src/observability/log_format.py
from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import fields, is_dataclass
from datetime import date, datetime
from pathlib import Path


def format_log_json(name: str, value: object) -> str:
    """Format a named structured value as a human-readable log block."""
    if not name or name.strip() != name or any(char.isspace() for char in name):
        raise ValueError("log JSON name must be a non-empty key without whitespace")
    if "=" in name:
        raise ValueError("log JSON name must not contain '='")

    payload = json.dumps(
        _format_log_value(value, key=None),
        ensure_ascii=False,
        indent=2,
        allow_nan=False,
    )
    return f"{name}={payload}"


def _format_log_value(value: object, *, key: str | None) -> object:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if is_dataclass(value) and not isinstance(value, type):
        return {
            field.name: _format_log_value(getattr(value, field.name), key=field.name)
            for field in fields(value)
        }
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError("log JSON numbers must be finite")
        return _format_float(value, key=key)
    if isinstance(value, Mapping):
        return {
            str(item_key): _format_log_value(item_value, key=str(item_key))
            for item_key, item_value in value.items()
        }
    if isinstance(value, list | tuple):
        return [_format_log_value(item, key=key) for item in value]
    raise TypeError(f"log JSON value is not serializable: {type(value).__name__}")


def _format_float(value: float, *, key: str | None) -> str:
    if key is not None and key.endswith("_return"):
        percent = value * 100.0
        decimals = 2 if abs(percent) >= 1.0 else 4
        return f"{percent:.{decimals}f}%"
    if key is not None and key.endswith("_ratio"):
        return f"{value * 100.0:.2f}%"
    return f"{value:.4f}"
