from __future__ import annotations

"""Timeout guard for Bzzoiro context gap pass.

The broad Bzzoiro gap pass can hit slow Bzzoiro endpoints. A single
`ReadTimeout` previously bubbled out of `_fetch_json`, causing the whole pass to
return only `error: ReadTimeout` and add zero contexts. This guard patches the
module-level HTTP helper so timeouts are counted and skipped instead of aborting
all remaining candidates.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-bzzoiro-context-gap-timeout-guard.json"


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _safe_preview(value: Any, limit: int = 800) -> str:
    try:
        return str(value)[:limit]
    except Exception:
        return ""


def install() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "starting",
    }
    try:
        from app.services import bzzoiro_context_gap_finalizer as gap
        from app.providers.bzzoiro import BzzoiroContextProvider
    except Exception as exc:
        payload.update({"status": "error", "error": f"import:{type(exc).__name__}: {exc}"})
        _write_json(REPORT_PATH, payload)
        return payload

    if getattr(gap._fetch_json, "_harizon_timeout_guard", False):
        payload.update({"status": "already_installed"})
        _write_json(REPORT_PATH, payload)
        return payload

    async def safe_fetch_json(client: httpx.AsyncClient, url: str, headers: dict[str, str], stats: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
        stats["requests"] = _to_int(stats.get("requests"), 0) + 1
        try:
            response = await client.get(url, headers=headers, params=params or {})
        except httpx.TimeoutException as exc:
            stats["errors"] = _to_int(stats.get("errors"), 0) + 1
            stats["timeouts"] = _to_int(stats.get("timeouts"), 0) + 1
            stats["last_error"] = f"timeout:{type(exc).__name__}: {exc}"
            stats["last_url"] = url
            return None
        except httpx.HTTPError as exc:
            stats["errors"] = _to_int(stats.get("errors"), 0) + 1
            stats["http_errors"] = _to_int(stats.get("http_errors"), 0) + 1
            stats["last_error"] = f"http_error:{type(exc).__name__}: {exc}"
            stats["last_url"] = url
            return None
        except Exception as exc:
            stats["errors"] = _to_int(stats.get("errors"), 0) + 1
            stats["unexpected_errors"] = _to_int(stats.get("unexpected_errors"), 0) + 1
            stats["last_error"] = f"unexpected:{type(exc).__name__}: {exc}"
            stats["last_url"] = url
            return None
        stats.setdefault("http_statuses", []).append(response.status_code)
        stats["last_url"] = str(response.url)
        if response.status_code != 200:
            stats["errors"] = _to_int(stats.get("errors"), 0) + 1
            stats["last_error"] = f"http_status={response.status_code}"
            stats["last_body_preview"] = _safe_preview(response.text)
            return None
        try:
            return response.json()
        except Exception as exc:
            stats["errors"] = _to_int(stats.get("errors"), 0) + 1
            stats["last_error"] = f"json:{type(exc).__name__}: {exc}"
            stats["last_body_preview"] = _safe_preview(response.text)
            return None

    safe_fetch_json._harizon_timeout_guard = True  # type: ignore[attr-defined]
    gap._fetch_json = safe_fetch_json  # type: ignore[assignment]

    current = BzzoiroContextProvider.fetch_context
    if not getattr(current, "_harizon_bzzoiro_context_gap_timeout_wrapper", False):
        async def fetch_context_timeout_bounded(self, matches):  # type: ignore[no-untyped-def]
            old_timeout = None
            settings = getattr(self, "settings", None)
            try:
                if settings is not None and hasattr(settings, "bzzoiro_timeout_seconds"):
                    old_timeout = getattr(settings, "bzzoiro_timeout_seconds")
                    timeout = float(os.getenv("BZZOIRO_CONTEXT_GAP_TIMEOUT_SECONDS") or 8.0)
                    try:
                        setattr(settings, "bzzoiro_timeout_seconds", timeout)
                    except Exception:
                        pass
                return await current(self, matches)
            finally:
                if settings is not None and old_timeout is not None:
                    try:
                        setattr(settings, "bzzoiro_timeout_seconds", old_timeout)
                    except Exception:
                        pass

        fetch_context_timeout_bounded._harizon_bzzoiro_context_gap_timeout_wrapper = True  # type: ignore[attr-defined]
        BzzoiroContextProvider.fetch_context = fetch_context_timeout_bounded  # type: ignore[assignment]

    payload.update({
        "status": "installed",
        "timeout_seconds": float(os.getenv("BZZOIRO_CONTEXT_GAP_TIMEOUT_SECONDS") or 8.0),
        "effect": "timeouts are counted and skipped instead of aborting the whole gap pass",
    })
    _write_json(REPORT_PATH, payload)
    return payload
