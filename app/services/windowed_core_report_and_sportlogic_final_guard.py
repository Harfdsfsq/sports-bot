from __future__ import annotations

"""Final post-finalizer guard.

This module fixes two runtime issues seen in the 2026-05-12 logs:

1. `latest-windowed-core-coverage.json` can be overwritten by an install-only
   report when later workflow steps install runtime extensions again. We keep a stable
   candidate-audit copy and restore it over install-only payloads.
2. Bzzoiro provides useful secondary odds, but only overlaps odds-api.io on a
   small match subset. SportLogic has a configured key/quota and should be used
   as a controlled near-window secondary odds source, not as a broad scraper.
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
    return isinstance(payload, dict) and (
        "candidates_in" in payload
        or "publish_blocked_by_coverage" in payload
        or payload.get("stage") in {"candidate_audit_publish_block_only", "publishable_filter"}
    )


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
        # Keep install/status payloads, but do not let them overwrite the richer
        # run-level coverage audit once it exists.
        _write_json(INSTALL_PATH, payload if isinstance(payload, dict) else {"payload": payload})
        audit = _read_json(AUDIT_PATH)
        if _is_candidate_audit(audit):
            _write_json(COVERAGE_PATH, audit)  # restore rich report
            return
        try:
            original(payload)
        except Exception:
            pass

    guarded_write_report._harizon_windowed_report_guard = True  # type: ignore[attr-defined]
    patch._write_report = guarded_write_report
    result["installed"] = True

    # If a later workflow step already overwrote coverage with install-only data,
    # restore the audit copy immediately.
    audit = _read_json(AUDIT_PATH)
    coverage = _read_json(COVERAGE_PATH)
    if _is_candidate_audit(audit) and not _is_candidate_audit(coverage):
        _write_json(COVERAGE_PATH, audit)
        result["restored_existing_audit"] = True
    return result


def _enable_controlled_sportlogic() -> dict[str, Any]:
    has_key = bool(os.getenv("SPORTLOGIC_API_KEY") or os.getenv("SPORTLOGIC_KEY") or os.getenv("SPORTLOGIC_TOKEN"))
    payload = {"api_key_present": has_key, "enabled": False, "mode": "controlled_near_window_secondary_odds"}
    if not has_key:
        return payload
    overrides = {
        "ENABLE_SPORTLOGIC": "true",
        "SPORTLOGIC_ENABLED": "true",
        "SPORTLOGIC_CONTROLLED_ODDS_ENABLED": "true",
        "SPORTLOGIC_PER_RUN_MAX": os.getenv("SPORTLOGIC_PER_RUN_MAX") or "30",
        "SPORTLOGIC_MAX_REQUESTS_PER_RUN": os.getenv("SPORTLOGIC_MAX_REQUESTS_PER_RUN") or "30",
        "SPORTLOGIC_MATCH_LIMIT": os.getenv("SPORTLOGIC_MATCH_LIMIT") or "80",
        "SPORTLOGIC_ODDS_MATCH_LIMIT": os.getenv("SPORTLOGIC_ODDS_MATCH_LIMIT") or "24",
        "SPORTLOGIC_TIMEOUT_SECONDS": os.getenv("SPORTLOGIC_TIMEOUT_SECONDS") or "20",
        "SPORTLOGIC_NEAR_WINDOW_HOURS": os.getenv("SPORTLOGIC_NEAR_WINDOW_HOURS") or "12",
        "SPORTLOGIC_ODDS_ONLY_IF_PRIMARY_HAS_ONE": "true",
    }
    for key, value in overrides.items():
        os.environ[key] = str(value)
    payload.update({"enabled": True, "overrides": overrides})
    return payload


def install() -> dict[str, Any]:
    report = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "report_guard": _install_report_guard(),
        "sportlogic": _enable_controlled_sportlogic(),
    }
    _write_json(REPORT_PATH, report)
    return report
