from __future__ import annotations

"""HARIZON Telegram run report v3.

This is a thin compatibility wrapper over send_harizon_telegram_run_report.py.
It keeps the full observability report, but fixes the main blind spot seen in
live runs: controlled fallback may write detailed `evaluated` rows while the
Telegram report still says "no fresh reject reasons" and shows raw artifact
candidates instead of the actual fallback evaluation.
"""

import importlib.util
import json
from collections import Counter
from pathlib import Path
from typing import Any

BASE_PATH = Path(__file__).with_name("send_harizon_telegram_run_report.py")


def _load_base() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_telegram_report_base", BASE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load base report script: {BASE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


base = _load_base()
_original_as_int = base.as_int
_original_reason_text = base.reason_text
_original_candidate_lines = base.candidate_lines
_original_diagnosis_lines = base.diagnosis_lines


def as_int_v3(value: Any, default: int = 0) -> int:
    if isinstance(value, (list, tuple, set, dict)):
        return len(value)
    return _original_as_int(value, default)


REASON_MAP = {
    "xg_direction_conflict": "направление ставки конфликтует с xG",
    "xg_probability_gap_hard_reject": "модель слишком оптимистична относительно xG-ориентира",
    "post_calibration_probability_guard": "quality: вероятность после калибровки ниже порога",
    "no_bet_quality_score_guard": "quality: итоговый quality score ниже порога",
    "quality_post_calibration_probability_guard": "quality: вероятность после калибровки ниже порога",
    "quality_no_bet_quality_score_guard": "quality: итоговый quality score ниже порога",
    "post_integrity_rescue_built": "post-integrity rescue построил кандидатов",
    "post_integrity_rescue_after_market_integrity": "post-integrity rescue прошёл market-integrity",
    "controlled_rescue_books_guard": "controlled rescue: мало paired bookmakers",
    "controlled_rescue_odds_range_guard": "controlled rescue: коэффициент вне допустимого диапазона",
    "controlled_rescue_edge_guard": "controlled rescue: value-запас ниже минимума",
    "controlled_rescue_ev_guard": "controlled rescue: EV ниже минимума",
}


def reason_text_v3(reason: str) -> str:
    return REASON_MAP.get(str(reason), _original_reason_text(reason))


def _iter_evaluated(fallback: dict[str, Any]) -> list[dict[str, Any]]:
    value = fallback.get("evaluated")
    if isinstance(value, list):
        return [row for row in value if isinstance(row, dict)]
    return []


def _counter_from_evaluated(fallback: dict[str, Any]) -> Counter[str]:
    counters: Counter[str] = Counter()
    for row in _iter_evaluated(fallback):
        for reason in row.get("reject_reasons") or []:
            if str(reason).strip():
                counters[str(reason).strip()] += 1
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        for reason in metrics.get("quality_reasons") or []:
            if str(reason).strip():
                counters["quality_" + str(reason).strip()] += 1
        xg = metrics.get("xg_sanity") if isinstance(metrics.get("xg_sanity"), dict) else {}
        if xg and xg.get("enabled") and not bool(xg.get("xg_direction_ok", True)):
            counters["xg_direction_conflict"] += 0  # already present in reject_reasons, keeps key discoverable
    return counters


def rejection_lines_v3(debug: dict[str, Any], fallback: dict[str, Any], line_guard: dict[str, Any]) -> list[str]:
    counters: Counter[str] = Counter()
    for src in (debug.get("rejections"), fallback.get("reason_counts"), fallback.get("reject_reasons"), fallback.get("top_reject_reasons")):
        if isinstance(src, dict):
            for key, value in src.items():
                counters[str(key)] += as_int_v3(value)
    counters.update(_counter_from_evaluated(fallback))
    if isinstance(line_guard, dict) and as_int_v3(line_guard.get("candidates_dropped")) > 0:
        counters["line_movement_guard_dropped"] += as_int_v3(line_guard.get("candidates_dropped"))
    if not counters:
        if isinstance(fallback.get("reason"), str):
            counters[fallback.get("reason")] += 1
        elif isinstance(fallback.get("diagnostics"), dict) and fallback["diagnostics"].get("reason"):
            counters[str(fallback["diagnostics"].get("reason"))] += 1
    if not counters:
        return ["• Нет свежей расшифровки reject reasons."]
    total = sum(max(0, int(v)) for v in counters.values()) or 1
    out: list[str] = []
    for reason, count in counters.most_common(16):
        if int(count) <= 0:
            continue
        pct = round(int(count) * 100.0 / total)
        out.append(f"• {reason_text_v3(reason)} — {int(count)} ({pct}%)")
    return out or ["• Нет свежей расшифровки reject reasons."]


def border_candidates_v3(fallback: dict[str, Any], limit: int = 5) -> list[dict[str, Any]]:
    evaluated = _iter_evaluated(fallback)
    if evaluated:
        return evaluated[:limit]
    for key in ("borderline_candidates", "evaluated_candidates", "top_candidates", "watchlist", "rejected_candidates"):
        value = fallback.get(key)
        if isinstance(value, list):
            return [x for x in value if isinstance(x, dict)][:limit]
    return []


def _with_metric_confirmation_sources(candidate: dict[str, Any]) -> dict[str, Any]:
    metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}
    if metrics.get("confirmation_sources") and not candidate.get("confirmation_sources"):
        candidate = dict(candidate)
        candidate["confirmation_sources"] = metrics.get("confirmation_sources")
    return candidate


def candidate_lines_v3(candidate: dict[str, Any], idx: int, compact_mode: bool = False) -> list[str]:
    enriched = _with_metric_confirmation_sources(candidate)
    lines = _original_candidate_lines(enriched, idx, compact_mode)
    metrics = enriched.get("metrics") if isinstance(enriched.get("metrics"), dict) else {}
    xg = metrics.get("xg_sanity") if isinstance(metrics.get("xg_sanity"), dict) else {}
    if xg and xg.get("enabled"):
        direction = "ok" if xg.get("xg_direction_ok") else "conflict"
        lines.append(
            "   • xG sanity: "
            f"{direction} | xG prob {base.as_float(xg.get('xg_probability_pct')):.1f}% | "
            f"model gap {base.as_float(xg.get('xg_model_gap_pp')):+.1f} п.п. | "
            f"xG total {base.as_float(xg.get('xg_total')):.2f}"
        )
    quality_reasons = metrics.get("quality_reasons") if isinstance(metrics.get("quality_reasons"), list) else []
    if quality_reasons:
        lines.append("   • quality reasons: " + "; ".join(reason_text_v3(str(x)) for x in quality_reasons[:4]))
    return lines


def diagnosis_lines_v3(summary: dict[str, Any], fallback: dict[str, Any], source_stats: dict[str, Any], refresh_plan: dict[str, Any], line_guard: dict[str, Any]) -> list[str]:
    evaluated = _iter_evaluated(fallback)
    counters = _counter_from_evaluated(fallback)
    if evaluated and counters:
        out = ["📌 Вывод"]
        out.append(f"• Controlled fallback реально проверил {len(evaluated)} кандидата(ов), но публикация запрещена guards.")
        if counters.get("xg_direction_conflict"):
            out.append("• Главный стопор — xG sanity: направление ставки конфликтует с расчётным xG, поэтому отправлять такие прогнозы нельзя.")
        if counters.get("xg_probability_gap_hard_reject"):
            out.append("• Дополнительный стопор — модель слишком оптимистична относительно xG-ориентира; это правильный hard reject.")
        if counters.get("quality_no_bet_quality_score_guard") or counters.get("quality_post_calibration_probability_guard"):
            out.append("• Quality-layer тоже не дал зелёный свет: кандидатам не хватает качества после калибровки.")
        final_checks = as_int_v3(refresh_plan.get("final_pre_kickoff_checks")) if isinstance(refresh_plan, dict) else 0
        if final_checks > 0:
            out.append(f"• Есть {final_checks} final pre-kickoff checks: ближайшие матчи нужно контролировать до старта, но только если xG/value не конфликтуют.")
        dropped = as_int_v3(line_guard.get("candidates_dropped")) if isinstance(line_guard, dict) else 0
        if dropped > 0:
            out.append(f"• Line guard снял {dropped} кандидатов: value/edge/цена ушли до публикации.")
        out.append("• Текущий результат корректный: API и candidate rescue работают, публикации нет из-за качества, а не из-за поломки.")
        return out
    return _original_diagnosis_lines(summary, fallback, source_stats, refresh_plan, line_guard)


base.as_int = as_int_v3
base.reason_text = reason_text_v3
base.rejection_lines = rejection_lines_v3
base.border_candidates = border_candidates_v3
base.candidate_lines = candidate_lines_v3
base.diagnosis_lines = diagnosis_lines_v3


if __name__ == "__main__":
    raise SystemExit(base.main())
