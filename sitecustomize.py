"""Runtime hotfixes for sports-bot.

Python imports `sitecustomize` automatically on startup when it is present on
`sys.path`. Placing this file in the project root lets us patch helper
functions without needing the full original source tree in this archive.
"""

from __future__ import annotations

import re
from typing import Any


def _normalize_probability_percent_patched(value: Any) -> float | None:
    """Accept numeric percentages from multiple provider formats.

    Handles values like:
    - 10
    - 10.5
    - "10%"
    - "10.5 %"
    - "0.42"
    - None / "" / "N/A"

    Returns a normalized probability in the 0..1 range.
    """
    if value is None or isinstance(value, bool):
        return None

    had_percent_sign = False

    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        lowered = text.lower()
        if lowered in {"n/a", "na", "none", "null", "-", "--", "unknown"}:
            return None
        if "%" in text:
            had_percent_sign = True
        text = text.replace("%", "").replace(",", ".").strip()
        match = re.search(r"[-+]?\d*\.?\d+", text)
        if not match:
            return None
        number = float(match.group(0))
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None

    if had_percent_sign or number > 1.0:
        number /= 100.0

    if number < 0.0:
        number = 0.0
    elif number > 1.0:
        number = 1.0
    return number


try:
    import app.utils as _app_utils
except Exception:
    _app_utils = None

if _app_utils is not None:
    _app_utils.normalize_probability_percent = _normalize_probability_percent_patched
