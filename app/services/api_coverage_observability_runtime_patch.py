from __future__ import annotations

"""API coverage observability and near-window context policy.

This runtime patch is intentionally read-only for publication logic.  It does not
relax quality/value/xG/price/timing guards and it does not fabricate odds.  It
adds two things that are needed after the API-max runs:

* a durable diagnostic artifact for SportLogic requests, because SportLogic is
  still returning 0 fixtures/matches despite being enabled;
* stronger provider-target env for near-window context gaps so existing Bzzoiro
  and SStats finalizers spend quota on matches that already have bookmaker
  quorum and are waiting for the final run.
"""

import atexit
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
REPORT_PATH = EXPORT_DIR / "latest-api-coverage-observability-policy.json"
SPORTLOGIC_DIAG_PATH = EXPORT_DIR / "latest-sportlogic-api-diagnostic.json"

_SPORTLOGIC_ATTEMPTS: list[dict[str, Any]] = []


def _truthy(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _set_if_lower(name: str, value: int) -> None:
    if _as_int(os.environ.get(name), -1) < int(value):
        os.environ[name] = str(int(value))


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _rows_count(payload: Any) -> int:
    if payload is None:
        return 0
    if isinstance(payload, list):
        return len(payload)
    if isinstance(payload, dict):
        for key in ("results", "data", "events", "fixtures", "matches", "items", "response", "games"):
            value = payload.get(key)
            if isinstance(value, list):
                return len(value)
            if isinstance(value, dict):
                nested = _rows_count(value)
                if nested:
                    return nested
    return 0


def _param_preview(params: Any) -> dict[str, Any]:
    if not isinstance(params, dict):
        return {}
    out: dict[str, Any] = {}
    for key, value in params.items():
        text = str(value)
        if any(secret in key.lower() for secret in ("key", "token", "secret", "auth")):
            out[key] = "***"
        else:
            out[key] = text[:120]
    return out


def _apply_near_window_context_policy() -> dict[str, Any]:
    changed: dict[str, str] = {}

    def mark(name: str) -> None:
        changed[name] = str(os.environ.get(name) or "")

    # Spend extra context budget where it matters: bookmaker-qualified upcoming
    # matches.  These variables are consumed by existing context-gap/source-matrix
    # runtime layers when present; if a layer ignores one of them, it is harmless.
    for name in (
        "BZZOIRO_CONTEXT_GAP_NEAR_WINDOW_FIRST",
        "BZZOIRO_CONTEXT_GAP_INCLUDE_BOOKMAKER_QUALIFIED",
        "BZZOIRO_CONTEXT_GAP_INCLUDE_NEAR_WINDOW_WITH_2PLUS_BOOKS",
        "BZZOIRO_ODDS_MATCH_COUNTS_AS_EVENT_CONTEXT",
        "BZZOIRO_PRICE_BACKFILL_ENABLED",
        "BZZOIRO_V2_SOURCE_MATRIX_TARGETS_ENABLED",
        "SSTATS_TEAM_FORM_RUNTIME_BRIDGE_ENABLED",
        "SSTATS_TEAM_FORM_JOIN_BY_ALIAS",
    ):
        os.environ[name] = "true"; mark(name)

    _set_if_lower("BZZOIRO_CONTEXT_GAP_TARGET_LIMIT", 260); mark("BZZOIRO_CONTEXT_GAP_TARGET_LIMIT")
    _set_if_lower("BZZOIRO_CONTEXT_GAP_TIMEOUT_SECONDS", 165); mark("BZZOIRO_CONTEXT_GAP_TIMEOUT_SECONDS")
    _set_if_lower("BZZOIRO_MAX_REQUESTS_PER_RUN", 260); mark("BZZOIRO_MAX_REQUESTS_PER_RUN")
    _set_if_lower("BZZOIRO_REQUEST_BUDGET_GRANTED", 260); mark("BZZOIRO_REQUEST_BUDGET_GRANTED")
    _set_if_lower("SSTATS_TEAM_FORM_TARGET_MATCHES", 300); mark("SSTATS_TEAM_FORM_TARGET_MATCHES")
    _set_if_lower("SSTATS_DEEP_CONTEXT_MATCH_LIMIT", 220); mark("SSTATS_DEEP_CONTEXT_MATCH_LIMIT")

    # SportLogic has stayed at fixtures=0.  Do not waste publication logic on it;
    # first capture exactly which provider calls are empty so the base URL/auth/date
    # contract can be fixed safely.
    os.environ["SPORTLOGIC_DIAGNOSTIC_CAPTURE"] = "true"; mark("SPORTLOGIC_DIAGNOSTIC_CAPTURE")
    os.environ["SPORTLOGIC_DOCS_PATH_PROBE_ENABLED"] = "true"; mark("SPORTLOGIC_DOCS_PATH_PROBE_ENABLED")
    _set_if_lower("SPORTLOGIC_PER_RUN_MAX", 70); mark("SPORTLOGIC_PER_RUN_MAX")
    _set_if_lower("SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN", 70); mark("SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN")
    _set_if_lower("SPORTLOGIC_REQUEST_BUDGET_GRANTED", 70); mark("SPORTLOGIC_REQUEST_BUDGET_GRANTED")
    return changed


def _patch_sportlogic_diagnostics() -> dict[str, Any]:
    try:
        from app.providers.sportlogic_provider import SportLogicProvider
    except Exception as exc:
        return {"sportlogic_diagnostic_patch": "import_failed", "error": str(exc)[:160]}
    if getattr(SportLogicProvider, "_harizon_api_observability_patched", False):
        return {"sportlogic_diagnostic_patch": "already_installed"}

    original_get_json = SportLogicProvider._get_json

    async def get_json_observed(self: Any, client: Any, path: str, params: dict[str, Any], stats: dict[str, Any], preview: dict[str, Any]) -> Any | None:
        before = dict(stats or {})
        started = datetime.now(UTC).isoformat()
        payload = await original_get_json(self, client, path, params, stats, preview)
        rows = _rows_count(payload)
        attempt = {
            "started_at_utc": started,
            "path": str(path),
            "params": _param_preview(params),
            "rows_count": rows,
            "payload_type": type(payload).__name__ if payload is not None else "None",
            "stats_delta": {
                key: stats.get(key)
                for key in sorted(set((stats or {}).keys()) - set(before.keys()))
                if key not in {"headers", "authorization", "api_key"}
            },
            "last_error": str((stats or {}).get("last_error") or "")[:240],
            "last_url_path_hint": str((stats or {}).get("last_url_path_hint") or (stats or {}).get("last_url") or "")[:240],
        }
        _SPORTLOGIC_ATTEMPTS.append(attempt)
        if len(_SPORTLOGIC_ATTEMPTS) > 80:
            del _SPORTLOGIC_ATTEMPTS[:-80]
        _write_sportlogic_diag()
        return payload

    SportLogicProvider._get_json = get_json_observed
    SportLogicProvider._harizon_api_observability_patched = True
    return {"sportlogic_diagnostic_patch": "installed"}


def _write_sportlogic_diag() -> None:
    rows_total = sum(_as_int(item.get("rows_count"), 0) for item in _SPORTLOGIC_ATTEMPTS)
    games_calls = [item for item in _SPORTLOGIC_ATTEMPTS if str(item.get("path") or "") == "/games"]
    _write_json(SPORTLOGIC_DIAG_PATH, {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "ok",
        "attempts_count": len(_SPORTLOGIC_ATTEMPTS),
        "games_attempts_count": len(games_calls),
        "rows_total": rows_total,
        "empty_games_attempts": sum(1 for item in games_calls if _as_int(item.get("rows_count"), 0) <= 0),
        "attempts_tail": _SPORTLOGIC_ATTEMPTS[-20:],
        "note": "No secrets are logged. If rows_total stays 0, check SportLogic base URL/auth/date contract.",
    })


def _write_policy_report(changed_env: dict[str, str], patches: dict[str, Any]) -> None:
    _write_json(REPORT_PATH, {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "installed",
        "policy": "api_coverage_observability_and_near_window_context_priority",
        "changed_env": changed_env,
        "patches": patches,
        "publication_safety": {
            "price_integrity_guard": "unchanged",
            "line_movement_guard": "unchanged",
            "timing_guard": "unchanged",
            "quarter_totals_block": "unchanged",
            "xg_quality_value_guards": "unchanged",
        },
    })


def install() -> None:
    changed_env = _apply_near_window_context_policy()
    patches = _patch_sportlogic_diagnostics()
    _write_policy_report(changed_env, patches)
    _write_sportlogic_diag()
    try:
        atexit.register(_write_sportlogic_diag)
    except Exception:
        pass
