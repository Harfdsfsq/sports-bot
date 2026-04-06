__all__ = []

from __future__ import annotations

import importlib
from typing import Any


def _patched_normalize_probability_percent(value: Any):
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number < 0:
            return None
        return number / 100.0 if number > 1 else number

    text = str(value).strip()
    if not text:
        return None

    lowered = text.lower()
    if lowered in {"n/a", "na", "none", "null", "-", "nan"}:
        return None

    is_percent = "%" in text
    cleaned = text.replace("%", "").replace(",", ".").strip()
    if not cleaned:
        return None

    try:
        number = float(cleaned)
    except (TypeError, ValueError):
        return None

    if number < 0:
        return None
    if is_percent or number > 1:
        return number / 100.0
    return number


try:
    _utils = importlib.import_module(__name__ + ".utils")
    _utils.normalize_probability_percent = _patched_normalize_probability_percent
except Exception:
    pass
