from __future__ import annotations

"""Compatibility finalizer for progressive coverage _fetch_provider wrapper.

`PredictionRunner._fetch_provider` is used both for provider calls that have a
match list (`fetch_offers`, `fetch_context`) and for bootstrap/list calls that do
not pass matches. The first progressive wrapper required `matches` as a mandatory
argument, which broke bootstrap:

    TypeError: fetch_provider_progressive() missing 1 required positional argument: 'matches'

This finalizer replaces the wrapper with a compatible `*args/**kwargs` version.
It sorts only calls where matches are actually provided and method is
`fetch_offers` or `fetch_context`; all other calls are passed through untouched.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / ".data" / "exports" / "latest-progressive-fetch-provider-signature-finalizer.json"


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _find_original_fetch_provider(current: Callable[..., Any]) -> Callable[..., Any] | None:
    closure = getattr(current, "__closure__", None) or []
    names = getattr(getattr(current, "__code__", None), "co_freevars", ()) or ()
    for name, cell in zip(names, closure):
        try:
            value = cell.cell_contents
        except Exception:
            continue
        if name == "original_fetch_provider" and callable(value):
            return value
    for cell in closure:
        try:
            value = cell.cell_contents
        except Exception:
            continue
        if callable(value) and value is not current:
            return value
    return None


def _looks_like_match_list(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, list):
        if not value:
            return True
        first = value[0]
        return hasattr(first, "match_key") or hasattr(first, "home_team") or isinstance(first, dict)
    if isinstance(value, tuple):
        return _looks_like_match_list(list(value))
    return False


def install() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "starting",
    }
    try:
        from app.services.runner import PredictionRunner
        from app.services import progressive_coverage_runtime_patch as p
    except Exception as exc:
        payload.update({"status": "error", "error": f"import:{type(exc).__name__}: {exc}"})
        _write(payload)
        return payload

    current = PredictionRunner._fetch_provider
    if getattr(current, "_harizon_progressive_signature_compatible", False):
        payload.update({"status": "already_installed"})
        _write(payload)
        return payload

    original = _find_original_fetch_provider(current) if getattr(current, "_harizon_progressive_coverage", False) else current
    if original is None:
        original = current

    async def fetch_provider_progressive_signature_compatible(self, provider, method_name, *args, **kwargs):  # type: ignore[no-untyped-def]
        provider_name = "unknown"
        try:
            provider_name = self._provider_name(provider) if provider is not None else "none"
        except Exception:
            provider_name = "unknown"
        method = str(method_name or "")

        matches = None
        match_source = "none"
        rest_args = list(args)
        if rest_args and _looks_like_match_list(rest_args[0]):
            matches = list(rest_args[0] or [])
            rest_args[0] = matches
            match_source = "positional"
        elif "matches" in kwargs and _looks_like_match_list(kwargs.get("matches")):
            matches = list(kwargs.get("matches") or [])
            kwargs["matches"] = matches
            match_source = "keyword"

        if method in {"fetch_offers", "fetch_context"} and matches is not None:
            sorted_matches = p._sort_matches_for_provider(matches, provider_name, method)
            p._mark_attempts(sorted_matches, provider_name, method)
            if match_source == "positional":
                rest_args[0] = sorted_matches
            elif match_source == "keyword":
                kwargs["matches"] = sorted_matches

        result = await original(self, provider, method_name, *rest_args, **kwargs)

        if method in {"fetch_offers", "fetch_context"} and matches is not None:
            try:
                data = result[0] if isinstance(result, tuple) and len(result) >= 1 else None
                stats = result[1] if isinstance(result, tuple) and len(result) >= 2 else None
                p._record_provider_success(data, provider_name, method, stats)
            except Exception:
                pass
        return result

    fetch_provider_progressive_signature_compatible._harizon_progressive_signature_compatible = True  # type: ignore[attr-defined]
    fetch_provider_progressive_signature_compatible._harizon_progressive_coverage = True  # type: ignore[attr-defined]
    PredictionRunner._fetch_provider = fetch_provider_progressive_signature_compatible  # type: ignore[assignment]
    payload.update({
        "status": "installed",
        "wrapped_existing_progressive": bool(getattr(current, "_harizon_progressive_coverage", False)),
        "original_found": original is not current,
    })
    _write(payload)
    return payload
