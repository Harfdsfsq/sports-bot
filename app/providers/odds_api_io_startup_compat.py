from __future__ import annotations

"""Small startup compatibility patches for odds-api.io provider.

The production provider contains helper methods that are called as instance
methods from parsing code. If a helper was accidentally declared without
``self`` and without ``@staticmethod``, Python binds ``self`` automatically and
raises errors such as:

    TypeError: OddsApiIoProvider._is_supported_market() takes 1 positional
    argument but 2 were given

This module normalizes helper binding. It does not make network calls or change
provider budgets.
"""

from functools import wraps
from typing import Any, Callable


HELPER_NAMES = (
    "_is_supported_market",
    "_family_for_market",
    "_line_from_value",
    "_map_h2h_selection",
    "_normalize_yes_no",
    "_normalize_double_chance_selection",
    "_normalize_team_total_selection",
    "_infer_team_total_side",
    "_canonical_bookmaker",
)


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


def _make_binding_safe(raw: Callable[..., Any]) -> Callable[..., Any]:
    """Return an instance method wrapper tolerant to both helper styles.

    Supports:
    - def helper(value)
    - @staticmethod def helper(value)
    - def helper(self, value)
    - wrappers installed by older runtime patches

    The first call attempts the no-self form; if Python reports an argument
    binding TypeError, the wrapper retries with ``self`` injected.
    """
    @wraps(raw)
    def binding_safe(self: Any, *args: Any, **kwargs: Any) -> Any:
        try:
            return raw(*args, **kwargs)
        except TypeError as first_exc:
            try:
                return raw(self, *args, **kwargs)
            except TypeError:
                raise first_exc

    return binding_safe


def install() -> dict[str, str]:
    from app.providers import odds_api_io

    cls = getattr(odds_api_io, "OddsApiIoProvider", None)
    if cls is None:
        return {"status": "skipped", "reason": "provider_class_missing"}

    fixed: list[str] = []
    for name in HELPER_NAMES:
        raw = _raw_class_attr(cls, name)
        if not callable(raw):
            continue
        # Always install the wrapper. Older startup/runtime patches may have
        # marked the class as installed before the provider was actually safe.
        setattr(cls, name, _make_binding_safe(raw))
        fixed.append(name)

    cls._harizon_startup_compat_installed = True
    cls._harizon_startup_compat_version = "binding-safe-v2"
    cls._harizon_startup_compat_fixed = fixed
    return {"status": "installed", "version": "binding-safe-v2", "fixed": ",".join(fixed)}
