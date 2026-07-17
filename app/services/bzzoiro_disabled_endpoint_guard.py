"""Skip redundant Bzzoiro per-event metadata/prediction calls.

The bulk predictions provider already supplies pre-match xG. Some legacy source
matrix enrichers still call the per-event metadata and prediction endpoints
directly, bypassing instance flags. This guard is installed after the shared hard
budget wrapper, so disabled endpoints do not consume the useful odds/stats budget.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

OUT = Path(".data/exports/latest-bzzoiro-disabled-endpoint-guard.json")
_DISABLED = {"metadata", "prediction"}
_COUNTS: Counter[str] = Counter()
_INSTALLED = False


def _endpoint(path: Any) -> str:
    text = str(path or "").lower()
    if text.endswith("/metadata/") or "/metadata/?" in text:
        return "metadata"
    if text.endswith("/prediction/") or "/prediction/?" in text:
        return "prediction"
    return ""


def _write() -> None:
    payload = {
        "status": "installed" if _INSTALLED else "not_installed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "disabled_endpoints": sorted(_DISABLED),
        "skipped": dict(_COUNTS),
        "bulk_prediction_provider_preserved": True,
        "publication_contract_relaxed": False,
    }
    try:
        OUT.parent.mkdir(parents=True, exist_ok=True)
        temporary = OUT.with_suffix(OUT.suffix + ".tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        temporary.replace(OUT)
    except Exception:
        pass


def reset_for_tests() -> None:
    _COUNTS.clear()


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed", "skipped": dict(_COUNTS)}
    try:
        from app.providers import bzzoiro_v2
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}

    cls = getattr(bzzoiro_v2, "BzzoiroContextProvider", None)
    original = getattr(cls, "_get_json", None) if cls is not None else None
    if cls is None or not callable(original):
        return {"status": "provider_method_missing"}
    if getattr(cls, "_harizon_disabled_endpoint_guard", False):
        _INSTALLED = True
        return {"status": "already_patched", "skipped": dict(_COUNTS)}

    async def guarded_get_json(
        self: Any,
        client: Any,
        path: str,
        headers: dict[str, str],
        params: dict[str, Any],
        stats: dict[str, Any],
    ) -> Any:
        endpoint = _endpoint(path)
        if endpoint in _DISABLED:
            _COUNTS[endpoint] += 1
            if isinstance(stats, dict):
                skipped = stats.setdefault("disabled_endpoint_skips", {})
                if isinstance(skipped, dict):
                    skipped[endpoint] = int(skipped.get(endpoint, 0) or 0) + 1
            _write()
            return None
        return await original(self, client, path, headers, params, stats)

    cls._get_json = guarded_get_json
    cls._harizon_disabled_endpoint_guard = True
    _INSTALLED = True
    _write()
    return {
        "status": "installed",
        "disabled_endpoints": sorted(_DISABLED),
        "bulk_prediction_provider_preserved": True,
    }


__all__ = ["install", "reset_for_tests"]
