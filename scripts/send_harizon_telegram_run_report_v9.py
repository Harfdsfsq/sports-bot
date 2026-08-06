from __future__ import annotations

"""HARIZON Telegram run report v9.

A/B publication-contract renderer from Правила.txt:
- A-tier: 2 line/odds sources, 2 bookmaker/price confirmations, 2 contexts;
- B-tier: 1 line/odds source, 1 bookmaker/price confirmation, 1 context;
- line movement/value/xG/quality and price-integrity still apply to both.
"""

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

V8_PATH = Path(__file__).with_name("send_harizon_telegram_run_report_v8.py")
EXPORT_DIR = Path(".data/exports")
V9_STATUS_PATH = EXPORT_DIR / "latest-harizon-telegram-run-report-v9-status.json"
FALLBACK_REPORT_PATH = EXPORT_DIR / "latest-controlled-fallback-report.json"


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


def _fallback_selected_tier() -> str:
    report = _load_json(FALLBACK_REPORT_PATH)
    selected = report.get("selected") if isinstance(report.get("selected"), dict) else {}
    tier = str(selected.get("tier") or selected.get("publication_tier") or "").lower()
    if not tier and isinstance(selected.get("metrics"), dict):
        tier = str(selected["metrics"].get("tier") or "").lower()
    return tier


def _counts(payload: dict[str, Any]) -> dict[str, int]:
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    funnel = payload.get("funnel") if isinstance(payload.get("funnel"), dict) else {}
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
    odds2 = _as_int(truth_counts.get("matches_with_2plus_odds_sources"))
    fallback_published = _as_int(funnel.get("fallback_published_count"))
    main_published = _as_int(funnel.get("main_pipeline_published_count"))
    # Fallback can publish an A-tier-looking row through the reserve path, but it
    # must not be counted as main/A-tier publication in the A-tier line.
    fallback_tier = _fallback_selected_tier()
    fallback_a_published = fallback_published if "a" in fallback_tier and "b" not in fallback_tier else 0
    b_cover = min(with_odds, price2, with_context) if inv_total else 0
    a_cover = min(odds2, price2, context2) if inv_total else 0
    return {
        "inv_total": inv_total,
        "with_odds": with_odds,
        "with_context": with_context,
        "price2": price2,
        "context2": context2,
        "odds2": odds2,
        "b_cover": b_cover,
        "a_cover": a_cover,
        "main_published": main_published,
        "fallback_published": fallback_published,
        "fallback_a_published": fallback_a_published,
    }


def build_payload() -> dict[str, Any]:
    payload = _base_build_payload()
    payload["version"] = "harizon-telegram-report-v9-rules-ab-contract"
    payload.setdefault("diagnostics", {})["ab_tier_contract"] = {
        "A": {"min_odds_sources": 2, "min_bookmakers": 2, "min_context_sources": 2},
        "B": {"min_odds_sources": 1, "min_bookmakers": 1, "min_context_sources": 1},
        "independent_odds_sources": "required_for_a_tier_only",
    }
    return payload


def _replace_odds_source_line(line: str) -> str:
    if "—" in line:
        prefix = line.split("—", 1)[0].rstrip()
    elif " - " in line:
        prefix = line.split(" - ", 1)[0].rstrip()
    else:
        prefix = line.rstrip()
    return f"{prefix} — A-tier strict metric; для B-tier не обязательный блок."


def _rewrite_contract_lines(text: str, counts: dict[str, int]) -> str:
    out: list[str] = []
    for line in text.splitlines():
        lower = line.lower()
        if "a-tier strict-ready:" in lower:
            published_note = f"main опубликовано: {counts['main_published']}"
            if counts["fallback_a_published"]:
                published_note += f"; A через fallback: {counts['fallback_a_published']}"
            out.append(f"• A-tier strict-ready: {counts['a_cover']} | {published_note}")
            continue
        if (
            "b-tier bookmaker coverage:" in lower
            or "b-tier 1+ bookmaker/context coverage:" in lower
            or "b-tier strict coverage:" in lower
            or "b-tier 1+ line/1+ bookmaker/1+ context coverage:" in lower
            or "b-tier 1 line/2 books/1 context coverage:" in lower
        ):
            out.append(f"• B-tier 1+ line/1+ bookmaker/1+ context coverage: {counts['b_cover']} | fallback опубликовано: {counts['fallback_published']}")
            continue
        if "b-tier =" in lower:
            out.append("  B-tier = 1+ линия/odds-source + 2+ букмекер/ценовое подтверждение + 2+ контекста + движение линии + value.")
            continue
        if "a-tier =" in lower:
            out.append("  A-tier = 2+ independent odds-source + 2+ букмекера/ценовых подтверждения + 2+ контекста + движение линии + value.")
            continue
        if lower.lstrip().startswith("• 2+ independent odds-source:"):
            out.append(_replace_odds_source_line(line))
            continue
        if "strict-cover" in lower or "b-cover 1+" in lower or re.search(r"пересечение 2\+.*2\+", lower):
            out.append(f"• A-cover 2+ odds-source ∩ 2+ букмекера ∩ 2+ контекста: до {counts['a_cover']} матчей; B-cover: до {counts['b_cover']} матчей.")
            continue
        if "контракт публикации сейчас" in lower or "ценовой контракт" in lower:
            out.append("• Контракт публикации сейчас: A-tier = 2 odds-source + 2 букмекера + 2 контекста; B-tier = 1 odds-source + 1 букмекер + 1 контекст; price-integrity guard обязателен.")
            continue
        if "не форсировать публикацию" in lower:
            out.append("• Не форсировать публикацию: кандидат должен пройти свой A/B-tier контракт, xG/quality/value/line movement и price-integrity.")
            continue
        out.append(line)
    return "\n".join(out)


def render(payload: dict[str, Any]) -> str:
    text = _base_render(payload)
    return _rewrite_contract_lines(text, _counts(payload))


v8.v7.v5.build_payload = build_payload
v8.v7.v5.render = render
v8.v7.build_payload = build_payload
v8.v7.render = render
_write_status({
    "status": "installed",
    "renderer": "v9",
    "main_module": "v8.v7.v5",
    "contract": "A=2 odds sources + 2 bookmakers + 2 contexts; B=1 odds source + 1 bookmaker + 1 context",
})


if __name__ == "__main__":
    raise SystemExit(v8.v7.v5.main())
