from __future__ import annotations

"""Final post-finalizer guard.

Keeps the rich windowed candidate audit from being overwritten and prevents the
known-zero SportLogic path from being re-enabled after the runtime budget guard.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
AUDIT_PATH = EXPORT_DIR / "latest-windowed-core-candidate-audit.json"
COVERAGE_PATH = EXPORT_DIR / "latest-windowed-core-coverage.json"
INSTALL_PATH = EXPORT_DIR / "latest-windowed-core-install.json"
REPORT_PATH = EXPORT_DIR / "latest-windowed-core-report-and-sportlogic-final-guard.json"

SPORTLOGIC_ZERO = {
    "ENABLE_SPORTLOGIC": "false",
    "SPORTLOGIC_ENABLED": "false",
    "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "false",
    "SPORTLOGIC_BROAD_FALLBACK_ENABLED": "false",
    "SPORTLOGIC_ODDS_DISCOVERY_FALLBACK_ENABLED": "false",
    "SPORTLOGIC_PER_RUN_MAX": "0",
    "SPORTLOGIC_MAX_REQUESTS_PER_RUN": "0",
    "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": "0",
    "SPORTLOGIC_REQUESTS_MAX_PER_RUN": "0",
    "SPORTLOGIC_REQUEST_BUDGET_GRANTED": "0",
    "SPORTLOGIC_MATCH_LIMIT": "0",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "0",
    "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "0",
    "SPORTLOGIC_ODDS_DISCOVERY_MAX_PAGES": "0",
    "SPORTLOGIC_ODDS_DISCOVERY_GAME_DETAIL_LIMIT": "0",
    "SPORTLOGIC_DISABLED_ZERO_ROWS_GUARD": "true",
}


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _is_candidate_audit(payload: Any) -> bool:
    return isinstance(payload, dict) and ("candidates_in" in payload or "publish_blocked_by_coverage" in payload or payload.get("stage") in {"candidate_audit_publish_block_only", "publishable_filter"})


def _install_report_guard() -> dict[str, Any]:
    result: dict[str, Any] = {"installed": False, "restored_existing_audit": False}
    try:
        from app.services import windowed_core_coverage_runtime_patch as patch
    except Exception as exc:
        result["error"] = f"import_error:{type(exc).__name__}: {exc}"
        return result

    original = getattr(patch, "_write_report", None)
    if not callable(original):
        result["error"] = "missing_patch_write_report"
        return result
    if getattr(original, "_harizon_windowed_report_guard", False):
        result["installed"] = True
        result["already_wrapped"] = True
        return result

    def guarded_write_report(payload: dict[str, Any]) -> None:
        if _is_candidate_audit(payload):
            _write_json(AUDIT_PATH, payload)
            _write_json(COVERAGE_PATH, payload)
            return
        _write_json(INSTALL_PATH, payload if isinstance(payload, dict) else {"payload": payload})
        audit = _read_json(AUDIT_PATH)
        if _is_candidate_audit(audit):
            _write_json(COVERAGE_PATH, audit)
            return
        try:
            original(payload)
        except Exception:
            pass

    guarded_write_report._harizon_windowed_report_guard = True  # type: ignore[attr-defined]
    patch._write_report = guarded_write_report
    result["installed"] = True
    audit = _read_json(AUDIT_PATH)
    coverage = _read_json(COVERAGE_PATH)
    if _is_candidate_audit(audit) and not _is_candidate_audit(coverage):
        _write_json(COVERAGE_PATH, audit)
        result["restored_existing_audit"] = True
    return result


def _disable_sportlogic() -> dict[str, Any]:
    previous = {key: os.getenv(key) for key in SPORTLOGIC_ZERO}
    for key, value in SPORTLOGIC_ZERO.items():
        os.environ[key] = value
    return {"enabled": False, "mode": "disabled_zero_rows_guard", "previous": previous, "overrides": SPORTLOGIC_ZERO}


def install() -> dict[str, Any]:
    report = {"created_at_utc": datetime.now(UTC).isoformat(), "report_guard": _install_report_guard(), "sportlogic": _disable_sportlogic()}
    _write_json(REPORT_PATH, report)
    return report
