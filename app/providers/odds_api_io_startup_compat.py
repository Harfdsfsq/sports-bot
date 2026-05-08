from __future__ import annotations

"""Small startup compatibility patches for odds-api.io provider.

The production provider contains helper methods that are called as instance
methods from parsing code. If a helper was accidentally declared without
``self`` and without ``@staticmethod``, Python binds ``self`` automatically and
raises errors such as:

    TypeError: OddsApiIoProvider._is_supported_market() takes 1 positional
    argument but 2 were given

This module only normalizes that helper binding. It does not make network calls
or change provider budgets.
"""

import inspect
from typing import Any


def _raw_class_attr(cls: type[Any], name: str) -> Any:
    try:
        value = cls.__dict__.get(name)
        if isinstance(value, staticmethod):
            return value.__func__
        if isinstance(value, classmethod):
            return value.__func__
        return value
    except Exception:
        return None


def _callable_positional_count(fn: Any) -> int | None:
    try:
        signature = inspect.signature(fn)
    except Exception:
        return None
    count = 0
    for param in signature.parameters.values():
        if param.kind in (param.POSITIONAL_ONLY, param.POSITIONAL_OR_KEYWORD) and param.default is param.empty:
            count += 1
    return count


def install() -> dict[str, str]:
    from app.providers import odds_api_io

    cls = getattr(odds_api_io, "OddsApiIoProvider", None)
    if cls is None:
        return {"status": "skipped", "reason": "provider_class_missing"}
    if getattr(cls, "_harizon_startup_compat_installed", False):
        return {"status": "skipped", "reason": "already_installed"}

    fixed: list[str] = []
    for name in ("_is_supported_market",):
        raw = _raw_class_attr(cls, name)
        if not callable(raw):
            continue
        required_positional = _callable_positional_count(raw)
        if required_positional == 1:
            setattr(cls, name, staticmethod(raw))
            fixed.append(name)

    cls._harizon_startup_compat_installed = True
    return {"status": "installed", "fixed": ",".join(fixed)}
