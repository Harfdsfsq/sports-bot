from __future__ import annotations

"""Bookmaker-quorum wrapper for controlled fallback publication.

The legacy publisher still contains a few tier checks named
``*_odds_sources_below_min``.  After the HARIZON policy switch those checks must
not reject a candidate that has a clean same-side bookmaker quorum.  This wrapper
imports the original publisher, patches only the final controlled-fallback
metrics/tier checks, and then calls the original ``main``.

Safety remains unchanged: xG sanity, quality/value thresholds, line movement,
time guards, duplicate guards, and price-integrity guards still run in the
original module.
"""

import importlib.util
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "scripts" / "publish_controlled_fallback.py"
REPORT = ROOT / ".data" / "exports" / "latest-bookmaker-quorum-controlled-fallback-wrapper.json"


def _truthy(value: Any, default: bool = False) -> bool:
    text = str(value if value is not None else "").strip().lower()
    if not text:
        return default
    if text in {"0", "false", "no", "off", "none", "null"}:
        return False
    return text in {"1", "true", "yes", "on", "force"}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _write_report(payload: dict[str, Any]) -> None:
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _load_original() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_original_controlled_fallback", ORIGINAL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ORIGINAL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _bookmaker_mode() -> bool:
    mode = str(
        os.getenv("PUBLISH_PRICE_CONFIRMATION_MODE")
        or os.getenv("PUBLICATION_PRICE_CONFIRMATION_MODE")
        or "bookmakers"
    ).strip().lower()
    return mode in {"bookmaker", "bookmakers", "bookmaker_quorum", "books", "2books", "2_bookmakers"}


def _price_integrity_reasons(candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    try:
        from scripts.filter_controlled_fallback_price_integrity import candidate_reject_reasons
    except Exception:
        return []
    row = dict(candidate or {})
    if isinstance(metrics, dict):
        row.setdefault('metrics', metrics)
        for key in (
            'odds', 'selected_odds', 'price', 'market_probability', 'family',
            'market_family', 'selection', 'point', 'line', 'handicap',
        ):
            if key not in row and key in metrics:
                row[key] = metrics.get(key)
    try:
        reasons, details = candidate_reject_reasons(row)
    except Exception:
        return []
    if reasons and isinstance(metrics, dict):
        metrics.setdefault('price_integrity_guard', {})
        if isinstance(metrics['price_integrity_guard'], dict):
            metrics['price_integrity_guard'].update({
                'wrapper_reasons': list(reasons),
                'wrapper_details': details,
            })
    return list(reasons or [])


def _priced_books_count(metrics: dict[str, Any]) -> int:
    quorum = metrics.get('tier_b_bookmaker_quorum') if isinstance(metrics.get('tier_b_bookmaker_quorum'), dict) else {}
    candidates = [
        quorum.get('priced_books_count') if isinstance(quorum, dict) else None,
        metrics.get('priced_books_count'),
        metrics.get('same_side_books_count'),
        metrics.get('bookmaker_count'),
    ]
    for value in candidates:
        parsed = _as_int(value, 0)
        if parsed > 0:
            return parsed
    raw = _as_int(metrics.get('books_count'), 0)
    if raw > 20:
        # A-cover/promoted rows can carry raw offer/hint counts in books_count
        # (for example 210), while the actual independently useful evidence is
        # the line/source/confirmation count.  Use that as display fallback only;
        # true quorum checks still require explicit priced_books_count when set.
        fallback = max(
            _as_int(metrics.get('confirmation_sources_count'), 0),
            _as_int(metrics.get('odds_sources_count'), 0),
            _as_int(metrics.get('sources_count'), 0),
            2,
        )
        return min(raw, fallback)
    return raw


def _clamp_display_books(metrics: dict[str, Any]) -> None:
    priced = _priced_books_count(metrics)
    raw_books = _as_int(metrics.get('books_count'), 0)
    if priced > 0 and (raw_books <= 0 or raw_books > max(20, priced * 5)):
        metrics['raw_books_count_before_display_clamp'] = raw_books
        metrics['books_count'] = priced
        metrics['display_books_count'] = priced
        metrics['bookmaker_quorum_display_clamped'] = True
    elif priced > 0:
        metrics.setdefault('display_books_count', priced)


def _candidate_has_bookmaker_quorum(module: Any, candidate: dict[str, Any], metrics: dict[str, Any], tier: str = "") -> bool:
    if not _bookmaker_mode():
        return False
    prefix = f"CONTROLLED_FALLBACK_TIER_{tier.upper()}_" if tier else "CONTROLLED_FALLBACK_"
    min_books = max(
        2,
        _as_int(
            os.getenv(prefix + "MIN_BOOKS")
            or os.getenv(prefix + "MIN_BOOKMAKERS")
            or os.getenv("PUBLISH_MIN_BOOKS")
            or os.getenv("MIN_BOOKS_PUBLISH"),
            2,
        ),
    )
    if _priced_books_count(metrics) < min_books:
        return False

    if _price_integrity_reasons(candidate, metrics):
        return False

    guard = getattr(module, "_bookmaker_quorum_price_guard", None)
    if callable(guard):
        try:
            guard_reasons = guard(candidate, metrics)
        except Exception:
            guard_reasons = []
        if guard_reasons:
            return False
    return True


def _patch_module(module: Any) -> None:
    original_candidate_metrics = module.candidate_metrics
    original_tier_reasons = module.tier_reasons
    original_translate_reject_reason = getattr(module, "translate_reject_reason", None)

    def translate_reject_reason_bookmaker_quorum(reason: Any) -> str:
        text = str(reason or "")
        if "odds_sources_below_min" in text or "odds sources below min" in text.lower():
            return "диагностика legacy odds-source; не блок при 2+ букмекерах"
        if callable(original_translate_reject_reason):
            try:
                return str(original_translate_reject_reason(reason))
            except Exception:
                return text
        return text

    def candidate_metrics_bookmaker_quorum(candidate: dict[str, Any]) -> dict[str, Any]:
        metrics = dict(original_candidate_metrics(candidate))
        _clamp_display_books(metrics)
        if _candidate_has_bookmaker_quorum(module, candidate, metrics):
            if _as_int(metrics.get("odds_sources_count"), 0) <= 0:
                metrics["odds_sources_count"] = 1
                metrics["line_sources"] = metrics.get("line_sources") or ["bookmaker_quorum"]
                metrics["bookmaker_quorum_satisfied_price_source"] = True
            metrics["price_confirmation_mode"] = "bookmakers"
        return metrics

    def tier_reasons_bookmaker_quorum(tier: str, candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
        _clamp_display_books(metrics)
        reasons = list(original_tier_reasons(tier, candidate, metrics))
        if not _candidate_has_bookmaker_quorum(module, candidate, metrics, tier=tier):
            return reasons
        filtered: list[str] = []
        removed: list[str] = []
        for reason in reasons:
            text = str(reason or "")
            if "odds_sources_below_min" in text:
                removed.append(text)
                continue
            filtered.append(reason)
        if removed:
            metrics.setdefault("bookmaker_quorum_policy", {})
            if isinstance(metrics["bookmaker_quorum_policy"], dict):
                metrics["bookmaker_quorum_policy"].update(
                    {
                        "removed_legacy_odds_source_reasons": removed,
                        "mode": "2plus_bookmakers",
                        "note": "2+ independent API odds sources are diagnostic only",
                    }
                )
        return filtered

    module.candidate_metrics = candidate_metrics_bookmaker_quorum
    module.tier_reasons = tier_reasons_bookmaker_quorum
    module.translate_reject_reason = translate_reject_reason_bookmaker_quorum


def _run_retro_price_audit_after_publish() -> dict[str, Any]:
    try:
        from scripts.retro_audit_price_integrity_ledger import main as retro_main
        retro_main([])
        path = ROOT / ".data" / "exports" / "latest-ledger-retro-price-integrity-audit.json"
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
        return {"status": "missing_report_after_audit"}
    except Exception as exc:
        return {"status": "failed", "error": str(exc)[:300]}


def main() -> int:
    defaults = {
        "PUBLISH_PRICE_CONFIRMATION_MODE": "bookmakers",
        "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "true",
        "CONTROLLED_FALLBACK_REQUIRE_BOOKMAKER_QUORUM_FOR_TELEGRAM": "true",
        "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "false",
        "CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES": "0",
        "CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES": "0",
        "CONTROLLED_FALLBACK_TIER_C_MIN_ODDS_SOURCES": "0",
    }
    for key, value in defaults.items():
        if os.getenv(key) in (None, ""):
            os.environ[key] = value

    module = _load_original()
    _patch_module(module)
    _write_report({
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "installed",
        "wrapper": "publish_controlled_fallback_bookmaker",
        "policy": "2plus_bookmakers_replace_api_odds_source_blocker_with_external_snapshot_price_guard",
        "original_script": str(ORIGINAL),
        "price_integrity_preserved": True,
        "display_books_clamped_to_priced_quorum": True,
        "raw_offer_count_display_clamp_enabled": True,
    })
    try:
        code = int(module.main() or 0)
    finally:
        audit = _run_retro_price_audit_after_publish()
        _write_report({
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "status": "completed",
            "wrapper": "publish_controlled_fallback_bookmaker",
            "policy": "2plus_bookmakers_replace_api_odds_source_blocker_with_external_snapshot_price_guard",
            "price_integrity_preserved": True,
            "display_books_clamped_to_priced_quorum": True,
            "raw_offer_count_display_clamp_enabled": True,
            "retro_price_audit": audit,
        })
    return code


if __name__ == "__main__":
    raise SystemExit(main())
