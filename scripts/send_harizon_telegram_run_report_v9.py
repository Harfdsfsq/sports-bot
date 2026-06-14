from __future__ import annotations

"""HARIZON Telegram run report v9.

Strict A/B publication-contract renderer:
- 2 independent odds sources;
- 2 bookmaker/price confirmations;
- 2 independent context confirmations;
- line movement/value/xG/quality still required before publication.
"""

import importlib.util
import json
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
    odds2 = _as_int(truth_counts.get("matches_with_2plus_odds_sources"))
    published = _as_int((payload.get("funnel") or {}).get("fallback_published_count")) if isinstance(payload.get("funnel"), dict) else 0
    strict_cover = min(odds2, price2, context2) if inv_total else 0
    return {
        "inv_total": inv_total,
        "with_odds": with_odds,
        "with_context": with_context,
        "price2": price2,
        "context2": context2,
        "odds2": odds2,
        "b_cover": strict_cover,
        "a_cover": strict_cover,
        "fallback_published": published,
    }


def build_payload() -> dict[str, Any]:
    payload = _base_build_payload()
    payload["version"] = "harizon-telegram-report-v9-strict-ab-contract"
    payload.setdefault("diagnostics", {})["ab_tier_contract"] = {
        "A": {"min_odds_sources": 2, "min_bookmakers": 2, "min_context_sources": 2},
        "B": {"min_odds_sources": 2, "min_bookmakers": 2, "min_context_sources": 2},
        "independent_odds_sources": "required",
    }
    return payload


def _rewrite_contract_lines(text: str, counts: dict[str, int]) -> str:
    out: list[str] = []
    for line in text.splitlines():
        lower = line.lower()
        if "B-tier bookmaker coverage:" in line or "B-tier 1+ bookmaker/context coverage:" in line:
            out.append(f"• B-tier strict coverage: {counts['b_cover']} | fallback опубликовано: {counts['fallback_published']}")
            continue
        if "B-tier =" in line:
            out.append("  B-tier = 2+ independent odds-source + 2+ букмекера/ценовых подтверждения + 2+ контекста + движение линии + value.")
            continue
        if "A-tier =" in line:
            out.append("  A-tier = 2+ independent odds-source + 2+ букмекера/ценовых подтверждения + 2+ контекста + движение линии + value.")
            continue
        if "odds-source" in lower and "2+" in lower and any(marker in lower for marker in ("диагност", "diagnostic", "не блок публикации", "только диагност", "РґРёР°Рі".lower())):
            out.append("• 2+ independent odds-source: обязательный блок публикации вместе с 2+ букмекерами и 2+ контекстами.")
            continue
        if "B-cover 1+" in line or re.search(r"Пересечение 2\+.*2\+", line):
            out.append(f"• Strict-cover 2+ odds-source ∩ 2+ букмекера ∩ 2+ контекста: до {counts['a_cover']} матчей.")
            continue
        if "Ценовой контракт" in line or "Р¦РµРЅРѕРІРѕР№" in line:
            out.append("• Контракт публикации сейчас: A/B-tier = 2+ odds-source + 2+ букмекера + 2+ контекста; price-integrity guard обязателен.")
            continue
        if "Не форсировать публикацию" in line or "РќРµ С„РѕСЂСЃРёСЂРѕРІР°С‚СЊ" in line:
            out.append("• Не форсировать публикацию: кандидат должен пройти 2 odds-source, 2 букмекера, 2 контекста, xG/quality/value/line movement.")
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
    "contract": "A/B=2 odds sources + 2 bookmakers + 2 contexts",
})


if __name__ == "__main__":
    raise SystemExit(v8.v7.v5.main())
