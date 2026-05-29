from __future__ import annotations

"""HARIZON Telegram run report v9.

Small wrapper around v8 that appends GitHub Actions run metadata to the human
Telegram report. This lets a forwarded Telegram report identify the exact run,
logs and run-bot artifact without manually uploading ZIP files.
"""

import importlib.util
import json
from pathlib import Path
from typing import Any

from app.services.github_actions_context import append_github_run_reference, github_run_context, write_github_run_context

V8_PATH = Path(__file__).with_name("send_harizon_telegram_run_report_v8.py")
EXPORT_DIR = Path(".data/exports")
V9_STATUS_PATH = EXPORT_DIR / "latest-harizon-telegram-run-report-v9-status.json"

_HIGH_ODDS_TOTALS_REASON_RU = "высокий коэффициент на тотал: xG-запас недостаточен для публикации"
_REASON_OVERRIDES = {
    "quality_high_odds_totals_xg_headroom_guard": _HIGH_ODDS_TOTALS_REASON_RU,
    "quality_quality_high_odds_totals_xg_headroom_guard": _HIGH_ODDS_TOTALS_REASON_RU,
    "high_odds_totals_xg_headroom_guard": _HIGH_ODDS_TOTALS_REASON_RU,
}


def _load_v8() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_report_v8", V8_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V8_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v8 = _load_v8()


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
        if tier == "A":
            out["tier_a_selected"] += 1
            if published:
                out["tier_a_published"] += 1
        elif tier == "B":
            out["tier_b_selected"] += 1
            if published:
                out["tier_b_published"] += 1
    return out


def _normalize_reason_key(reason: Any) -> str:
    text = str(reason or "").strip().lower()
    text = text.replace(" ", "_").replace("-", "_")
    while "__" in text:
        text = text.replace("__", "_")
    # Some report paths pass already-humanized text like
    # "quality quality high odds totals xg headroom guard".  Bring it back to
    # the canonical reject key before translating.
    if text.startswith("quality_quality_high_odds_totals_xg_headroom_guard"):
        return "quality_high_odds_totals_xg_headroom_guard"
    return text


def _reason_ru_patched(reason: Any) -> str:
    key = _normalize_reason_key(reason)
    if key in _REASON_OVERRIDES:
        return _REASON_OVERRIDES[key]
    try:
        return str(v8.v7.v5.reason_ru(str(reason or "n/a")))
    except Exception:
        return str(reason or "n/a")


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value))
    except Exception:
        return default


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _point_text(value: Any) -> str:
    if value in (None, "", "null"):
        return ""
    try:
        number = float(str(value))
        if number.is_integer():
            return str(int(number))
        return f"{number:g}"
    except Exception:
        return str(value).strip()


def _selection_with_point(row: dict[str, Any], metrics: dict[str, Any] | None = None) -> str:
    metrics = metrics or {}
    selection = str(row.get("selection") or row.get("market") or "ставка").strip()
    point = row.get("point")
    if point in (None, ""):
        point = metrics.get("point")
    point_s = _point_text(point)
    if not point_s:
        return selection or "ставка"
    # Keep already-expanded strings as-is: "Меньше 2.5", "Over (2.5)", etc.
    normalized_selection = selection.replace(",", ".")
    if point_s in normalized_selection:
        return selection or "ставка"
    family = str(row.get("family") or row.get("market_family") or "").strip().lower()
    if family in {"totals", "teamtotals", "team_totals", "spreads", "dnb"} or selection.lower() in {"over", "under", "больше", "меньше"}:
        return f"{selection or 'ставка'} {point_s}".strip()
    return selection or "ставка"


def _render_samples_with_points(payload: dict[str, Any]) -> list[str]:
    samples = payload.get("samples") if isinstance(payload.get("samples"), dict) else {}
    evaluated = samples.get("fallback_evaluated") if isinstance(samples.get("fallback_evaluated"), list) else []
    rows = [x for x in evaluated if isinstance(x, dict)][:3]
    if not rows:
        return []
    lines = ["🔎 Последние проверенные кандидаты"]
    for idx, row in enumerate(rows, 1):
        metrics = _first_dict(row.get("metrics"))
        home = row.get("home_team") or row.get("home") or "?"
        away = row.get("away_team") or row.get("away") or "?"
        selection = _selection_with_point(row, metrics)
        odds = _as_float(metrics.get("odds"))
        ev = _as_float(metrics.get("canonical_ev_pct"))
        edge = _as_float(metrics.get("canonical_edge_pp"))
        q = _as_float(metrics.get("quality_score"))
        odds_text = f" @{odds:.2f}" if odds > 0 else ""
        lines.append(f"{idx}. {home} — {away} | {selection}{odds_text} | EV {ev:+.1f}% | edge {edge:+.1f} п.п. | q {q:.1f}")
        reject = ", ".join(_reason_ru_patched(x) for x in (row.get("reject_reasons") or [])[:3])
        if reject:
            lines.append(f"   • причина: {reject}")
    return lines


if hasattr(v8, "_fallback_tier_counts"):
    v8._fallback_tier_counts = _strict_current_run_fallback_tier_counts
if hasattr(v8, "_reason_ru"):
    v8._reason_ru = _reason_ru_patched
if hasattr(v8, "_render_samples"):
    v8._render_samples = _render_samples_with_points

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
_write_status({
    "status": "installed",
    "renderer": "v9-github-run-reference",
    "github_actions": github_run_context(),
    "current_run_fallback_tier_counts": True,
    "sample_point_formatter": True,
    "high_odds_totals_reason_translation": True,
})


if __name__ == "__main__":
    raise SystemExit(v8.v7.v5.main())
