"""Application package initialization and startup compatibility patches."""

__all__ = []

import math
import re


def _patched_normalize_probability_percent(value):
    if value is None:
        return None

    if isinstance(value, str):
        cleaned = value.strip()
        if not cleaned:
            return None
        if cleaned.lower() in {"n/a", "na", "none", "null", "-", "--"}:
            return None
        cleaned = cleaned.replace(",", ".")
        cleaned = re.sub(r"\s+", "", cleaned)
        if cleaned.endswith("%"):
            cleaned = cleaned[:-1]
            if not cleaned:
                return None
            try:
                return float(cleaned) / 100.0
            except ValueError:
                return None
        try:
            number = float(cleaned)
        except ValueError:
            return None
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

    if math.isnan(number) or math.isinf(number):
        return None
    if number > 1.0:
        number = number / 100.0
    if number < 0.0 or number > 1.0:
        return None
    return number


try:
    from app import utils as _app_utils
    _app_utils.normalize_probability_percent = _patched_normalize_probability_percent
except Exception:
    pass
