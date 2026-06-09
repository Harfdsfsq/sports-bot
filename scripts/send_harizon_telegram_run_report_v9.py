from __future__ import annotations

"""HARIZON Telegram run report v9.

Renderer-only patch for the owner-requested tier contract:
- B-tier = 1+ bookmaker/price confirmation + 1+ context;
- A-tier = 2+ bookmakers/price confirmations + 2+ contexts.

The v8 renderer still described B-tier as 2+ bookmakers.  This wrapper keeps
v8 data loading and formatting but fixes the displayed coverage contract and
B-tier coverage numbers so the Telegram report matches the runtime policy.
"""

import importlib.util
import json
import os
import re
from pathlib import Path
from typing import Any

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
_base_build_payload = v8.build_payload
_base_render = v8.render


def _as_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return 0


def _pct(part: Any, total: Any) -> str:
    p = _as_int(part)
    t = _as_int(total)
    if t <= 0:
        return "0%"
    return f"{round(p * 100.0 / t)}%"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists() and path.stat().st_size > 0:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
    return {}


def _write_status(payload: dict[str, Any]) -> None:
    try:
        V9_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        V9_STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _counts(payload: dict[str, Any]) -> dict[str, int]:
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    diag = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    truth = diag.get("coverage_truth") if isinstance(diag.get("coverage_truth"), dict) else {}
    truth_counts = truth.get("counts") if isinstance(truth.get("counts"), dict) else {}
    bookmaker_norm = diag.get("bookmaker_quorum_normalizer") if isinstance(diag.get("bookmaker_quorum_normalizer"), dict) else {}

    inv_total = _as_int(truth_counts.get("matches_total")) or _as_int(coverage.get("day_inventory_total"))
    with_odds = (
        _as_int(truth_counts.get("matches_with_1plus_price_confirmations"))
        or _as_int(truth_counts.get("matches_with_price_confirmations"))
        or _as_int(truth_counts.get("matches_with_odds"))
        or _as_int(coverage.get("day_inventory_with_odds"))
        or _as_int(coverage.get("matches_with_offers"))
    )
    with_context = (
        _as_int(truth_counts.get("matches_with_context"))
        or _as_int(coverage.get("day_inventory_with_context"))
        or _as_int(coverage.get("matches_with_context"))
    )
    price2 = (
        _as_int(truth_counts.get("matches_with_2plus_price_confirmations"))
        or _as_int(bookmaker_norm.get("normalized_inventory_2plus_books"))
        or _as_int(coverage.get("matches_with_2plus_books"))
    )
    context2 = _as_int(truth_counts.get("matches_with_2plus_context_sources"))
    published = _as_int((payload.get("funnel") or {}).get("fallback_published_count")) if isinstance(payload.get("funnel"), dict) else 0
    return {
        "inv_total": inv_total,
        "with_odds": with_odds,
        "with_context": with_context,
        "price2": price2,
        "context2": context2,
        "b_cover": min(with_odds, with_context) if inv_total else 0,
        "a_cover": min(price2, context2) if inv_total else 0,
        "fallback_published": published,
    }


def build_payload() -> dict[str, Any]:
    payload = _base_build_payload()
    payload["version"] = "harizon-telegram-report-v9-ab-tier-bookmaker-contract"
    payload.setdefault("diagnostics", {})["ab_tier_contract"] = {
        "A": {"min_bookmakers": 2, "min_context_sources": 2},
        "B": {"min_bookmakers": 1, "min_context_sources": 1},
        "independent_odds_sources": "diagnostic_only",
    }
    return payload


def render(payload: dict[str, Any]) -> str:
    text = _base_render(payload)
    c = _counts(payload)

    # Replace v8's B-tier section.  It used the 2+ bookmaker count for B-tier,
    # which made a valid 1+ bookmaker/1+ context B-cover look like zero.
    b_line = f"• B-tier 1+ bookmaker/context coverage: {c['b_cover']} | fallback опубликовано: {c['fallback_published']}"
    text = re.sub(r"• B-tier bookmaker coverage: .*", b_line, text)
    text = text.replace(
        "  B-tier = 2+ букмекера + 1+ контекст + второй снимок линии + value сохранился.",
        "  B-tier = 1+ букмекер + 1+ контекст + второй снимок линии + value сохранился.",
    )
    text = text.replace(
        "  A-tier = 2+ букмекера по той же стороне рынка + 2+ контекста + подтверждённое движение линии + value.",
        "  A-tier = 2+ букмекера/ценовых подтверждения + 2+ контекста + подтверждённое движение линии + value.",
    )
    text = re.sub(
        r"• Пересечение 2\+ букмекера ∩ 2\+ контекста: .*",
        f"• B-cover 1+ букмекер ∩ 1+ контекст: до {c['b_cover']} матчей; A-cover 2+ букмекер ∩ 2+ контекст: до {c['a_cover']} матчей.",
        text,
    )
    text = text.replace(
        "• Ценовой контракт сейчас: 2+ букмекера по той же стороне рынка; price-integrity guard остаётся обязательным.",
        "• Ценовой контракт сейчас: B-tier 1+ букмекер; A-tier 2+ букмекера. Price-integrity guard остаётся обязательным.",
    )
    text = text.replace(
        "• Не форсировать публикацию: текущие кандидаты отрезаны xG/quality/value/line movement, а не старым требованием 2 independent odds sources.",
        "• Не форсировать публикацию: текущие кандидаты отрезаны xG/quality/value/line movement, а не старым требованием 2 independent odds sources. B-tier теперь считается по 1+ букмекеру и 1+ контексту.",
    )
    return text


v8.v7.v5.build_payload = build_payload
v8.v7.v5.render = render
v8.v7.build_payload = build_payload
v8.v7.render = render
_write_status({
    "status": "installed",
    "renderer": "v9",
    "main_module": "v8.v7.v5",
    "contract": "B=1+bookmaker+1+context; A=2+bookmakers+2+contexts",
})


if __name__ == "__main__":
    raise SystemExit(v8.v7.v5.main())
