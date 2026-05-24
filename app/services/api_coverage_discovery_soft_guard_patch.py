from __future__ import annotations

"""Discovery-safe API coverage guard for HARIZON unified scheme.

The API coverage consensus layer is the correct place to compute exact odds-source,
bookmaker and context coverage, but it was too early in the pipeline to hard-drop
candidates.  When it returns an empty list, quality/fallback/reporting lose the
candidate and Telegram says "raw candidates = 0" even though a useful candidate was
found and should be reported as rejected by the final publication contract.

This patch keeps the coverage computation strict but makes CandidateFactory
*discovery* soft by default: candidates with insufficient exact odds sources are
annotated and allowed to continue to quality/fallback/watchlist.  Final publish is
still blocked later by publish_coverage_contract, Telegram odds-source guards and
harizon_unified_scheme_runtime.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-api-coverage-discovery-soft-guard.json"

_INSTALLED = False
_ORIGINAL_GUARD: Callable[[Any, Any], tuple[bool, str, dict[str, Any]]] | None = None


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    if raw in {"0", "false", "no", "off", "none", "null"}:
        return False
    return raw in {"1", "true", "yes", "on", "force"}


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _get_dict_attr(obj: Any, attr: str) -> dict[str, Any]:
    value = getattr(obj, attr, None)
    if isinstance(value, dict):
        return dict(value)
    return {}


def _set_dict_attr(obj: Any, attr: str, value: dict[str, Any]) -> None:
    try:
        setattr(obj, attr, value)
    except Exception:
        pass


def _append_reason(candidate: Any, reason: str) -> None:
    try:
        reasons = getattr(candidate, "reasons", None)
        if not isinstance(reasons, list):
            reasons = []
            setattr(candidate, "reasons", reasons)
        if reason not in reasons:
            reasons.append(reason)
    except Exception:
        pass


def _soft_reason_allowed(reason: str) -> bool:
    """Only soften coverage/discovery reasons, not value, price-dispersion or data errors."""
    text = str(reason or "").strip().lower()
    if not text or text == "ok":
        return False
    if "missing_2_exact_odds_sources" in text:
        return True
    if "missing_2_context_sources" in text and _truthy(os.getenv("API_COVERAGE_SOFT_CONTEXT_DISCOVERY"), True):
        return True
    if "missing_2_exact_books" in text and _truthy(os.getenv("API_COVERAGE_SOFT_BOOK_DISCOVERY"), False):
        return True
    return False


def _annotate_soft_reject(candidate: Any, reason: str, inventory: dict[str, Any]) -> None:
    summary = _get_dict_attr(candidate, "source_summary")
    soft = dict(summary.get("api_coverage_discovery_soft_guard") or {})
    soft.update({
        "enabled": True,
        "soft_reject_reason": reason,
        "final_publish_must_recheck": True,
        "exact_odds_sources_count": int((inventory or {}).get("exact_odds_sources_count") or 0),
        "exact_books_count": int((inventory or {}).get("exact_books_count") or 0),
        "exact_odds_sources": list((inventory or {}).get("exact_odds_sources") or []),
    })
    summary["api_coverage_discovery_soft_guard"] = soft
    summary["publication_blocked_reason"] = reason
    summary["publish_coverage_passed"] = False
    _set_dict_attr(candidate, "source_summary", summary)

    diagnostics = _get_dict_attr(candidate, "diagnostics")
    diagnostics["api_coverage_discovery_soft_guard"] = soft
    _set_dict_attr(candidate, "diagnostics", diagnostics)
    _append_reason(candidate, f"api_coverage_discovery_soft:{reason}")


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_GUARD
    if _INSTALLED:
        return {"status": "already_installed", "artifact": str(REPORT_PATH)}
    if not _truthy(os.getenv("API_COVERAGE_DISCOVERY_SOFT_GUARD_ENABLED"), True):
        payload = {"status": "disabled_by_env", "created_at_utc": datetime.now(UTC).isoformat()}
        _write(payload)
        return payload
    try:
        import app.services.api_coverage_consensus_runtime_patch as api_cov
    except Exception as exc:
        payload = {"status": "import_error", "error": f"{type(exc).__name__}: {exc}", "created_at_utc": datetime.now(UTC).isoformat()}
        _write(payload)
        return payload

    current = getattr(api_cov, "_guard_candidate", None)
    if not callable(current):
        payload = {"status": "missing_guard", "created_at_utc": datetime.now(UTC).isoformat()}
        _write(payload)
        return payload
    if getattr(current, "_harizon_api_coverage_discovery_soft_guard", False):
        _INSTALLED = True
        payload = {"status": "already_wrapped", "artifact": str(REPORT_PATH), "created_at_utc": datetime.now(UTC).isoformat()}
        _write(payload)
        return payload

    _ORIGINAL_GUARD = current

    def soft_guard(candidate: Any, contexts_by_match: Any) -> tuple[bool, str, dict[str, Any]]:
        ok, reason, inventory = current(candidate, contexts_by_match)
        if ok:
            return ok, reason, inventory
        if _soft_reason_allowed(reason):
            _annotate_soft_reject(candidate, reason, inventory or {})
            return True, f"soft_discovery:{reason}", inventory or {}
        return ok, reason, inventory

    soft_guard._harizon_api_coverage_discovery_soft_guard = True  # type: ignore[attr-defined]
    setattr(api_cov, "_guard_candidate", soft_guard)
    _INSTALLED = True
    payload = {
        "status": "installed",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "artifact": str(REPORT_PATH),
        "soft_reasons": ["api_coverage_missing_2_exact_odds_sources", "api_coverage_missing_2_context_sources"],
        "publication_contract": "unchanged; final publish still requires strict coverage",
    }
    _write(payload)
    return payload
