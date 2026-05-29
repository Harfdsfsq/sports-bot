from __future__ import annotations

"""HARIZON Telegram run report v9.

Small wrapper around v8 that appends GitHub Actions run metadata to the human
Telegram report. This lets a forwarded Telegram report identify the exact run,
logs and run-bot artifact without manually uploading ZIP files.
"""

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

from app.services.github_actions_context import append_github_run_reference, github_run_context, write_github_run_context

V8_PATH = Path(__file__).with_name("send_harizon_telegram_run_report_v8.py")
EXPORT_DIR = Path(".data/exports")
V9_STATUS_PATH = EXPORT_DIR / "latest-harizon-telegram-run-report-v9-status.json"


def _load_v8() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_report_v8", V8_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V8_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v8 = _load_v8()

SOURCE_ALIASES = {
    "oddsapiio": "odds_api_io",
    "odds_api": "odds_api_io",
    "odds_api_io_account1": "odds_api_io",
    "odds_api_io_account2": "odds_api_io",
    "bzzoiro_current_odds": "bzzoiro",
    "bzzoiro_v2": "bzzoiro",
    "sport_logic": "sportlogic",
}
LIVE_ODDS_SOURCES = {"odds_api_io", "bzzoiro", "sportlogic", "allsportsapi", "api_football", "rapidapi_odds", "oddspapi", "highlightly"}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _norm_source(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return SOURCE_ALIASES.get(text, text)


def _items(value: Any) -> list[Any]:
    if isinstance(value, dict):
        return list(value.keys())
    if isinstance(value, (list, tuple, set)):
        return list(value)
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in re.split(r"[,|;/]+", value) if x.strip()]
    return []


def _independent_odds_count(row: dict[str, Any]) -> int:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    details = metrics.get("independent_odds_source_detail") if isinstance(metrics.get("independent_odds_source_detail"), dict) else {}
    sources: set[str] = set()
    for container in (row, metrics, details):
        if not isinstance(container, dict):
            continue
        for key in ("odds_sources", "independent_odds_sources", "normalized_sources"):
            for item in _items(container.get(key)):
                src = _norm_source(item)
                if src in LIVE_ODDS_SOURCES:
                    sources.add(src)
    if sources:
        return len(sources)
    return _as_int(metrics.get("independent_odds_sources_count"), _as_int(metrics.get("odds_sources_count"), _as_int(row.get("odds_sources_count"))))


def _strict_current_run_fallback_tier_counts(report: dict[str, Any]) -> dict[str, int]:
    """Count fallback publication tiers for the current run only.

    ``latest-controlled-fallback-published-picks.json`` is a persisted ledger and
    may contain a pick from a previous run.  The human report must not say that
    the current run published a forecast when this run only rejected the old pick
    as a duplicate.
    """
    rows = report.get("selected_all") if isinstance(report.get("selected_all"), list) else []
    if not rows and isinstance(report.get("selected"), dict):
        rows = [report["selected"]]
    published = bool(report.get("published")) or str(report.get("status") or "") == "published"
    if published:
        try:
            published_picks = v8._load_json_any(v8.FALLBACK_PUBLISHED_PICKS)
        except Exception:
            published_picks = None
        if isinstance(published_picks, list) and published_picks:
            rows = [row for row in published_picks if isinstance(row, dict)] or rows
    selected = [row for row in rows if isinstance(row, dict)]
    out = {
        "published_total": len(selected) if published else 0,
        "selected_total": len(selected),
        "tier_a_published": 0,
        "tier_b_published": 0,
        "tier_a_selected": 0,
        "tier_b_selected": 0,
    }
    for row in selected:
        try:
            tier = v8._tier_code(row)
        except Exception:
            tier = str(row.get("tier") or row.get("publication_tier") or "").strip().upper()
        # Honest downgrade for reports: raw ``уровень A`` is not enough if the row
        # has only one independent odds provider after normalization.
        if tier == "A" and _independent_odds_count(row) < 2:
            tier = "B"
        if tier == "A":
            out["tier_a_selected"] += 1
            if published:
                out["tier_a_published"] += 1
        elif tier == "B":
            out["tier_b_selected"] += 1
            if published:
                out["tier_b_published"] += 1
    return out


if hasattr(v8, "_fallback_tier_counts"):
    v8._fallback_tier_counts = _strict_current_run_fallback_tier_counts

_original_render = v8.render


def _write_status(payload: dict[str, Any]) -> None:
    try:
        V9_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        V9_STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def render(payload: dict[str, Any]) -> str:
    ctx = write_github_run_context()
    try:
        payload.setdefault("diagnostics", {})["github_actions"] = ctx
    except Exception:
        pass
    return append_github_run_reference(_original_render(payload))


v8.render = render
v8.v7.render = render
v8.v7.v5.render = render
_write_status({"status": "installed", "renderer": "v9-github-run-reference", "github_actions": github_run_context(), "current_run_fallback_tier_counts": True, "honest_independent_odds_tier_counts": True})


if __name__ == "__main__":
    raise SystemExit(v8.v7.v5.main())
