from __future__ import annotations

"""HARIZON Telegram run report v8.

Extends v7 with progressive core coverage metrics. The publication price contract is:
- selected market side confirmed by 2+ bookmakers;
- at least one real line source/price feed;
- 2+ context sources still required.

The report also normalizes runtime-patched SStats counters: the provider wrapper
stores real v1 numbers under source_stats.sstats.v1_stats / v1_* fields, while
older renderers only read top-level requests/contexts/rows.
"""

import importlib.util
import json
from pathlib import Path
from typing import Any

V7_PATH = Path(__file__).with_name("send_harizon_telegram_run_report_v7.py")
EXPORT_DIR = Path(".data/exports")
PROGRESSIVE_PLAN = EXPORT_DIR / "latest-progressive-coverage-plan.json"
TRUTH_REPORT = EXPORT_DIR / "latest-day-inventory-coverage-truth.json"
V8_STATUS_PATH = EXPORT_DIR / "latest-harizon-telegram-run-report-v8-status.json"


def _load_v7() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_report_v7", V7_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V7_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v7 = _load_v7()
_base_build_payload = v7.v5.build_payload
_base_render = v7.v5.render


def _as_int(value: Any) -> int:
    try:
        if value in (None, ""):
            return 0
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value)))
    except Exception:
        return 0


def _load_progressive_plan() -> dict[str, Any]:
    try:
        if PROGRESSIVE_PLAN.exists() and PROGRESSIVE_PLAN.stat().st_size > 0:
            payload = json.loads(PROGRESSIVE_PLAN.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
    return {}


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
        V8_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        V8_STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _normalize_runtime_patched_sstats(payload: dict[str, Any]) -> None:
    """Expose nested SStats v1 counters at the top-level report API row."""
    try:
        data = v7.v5.artifacts()
        stats = v7.v5.source_stats(data)
    except Exception:
        return
    row = _first_dict(stats.get("sstats"))
    if not row:
        return
    v1 = _first_dict(row.get("v1_stats"))
    requests = _as_int(row.get("requests")) or _as_int(row.get("v1_requests")) or _as_int(v1.get("requests"))
    contexts = _as_int(row.get("contexts_built")) or _as_int(row.get("v1_contexts_built")) or _as_int(v1.get("contexts_built"))
    rows = _as_int(row.get("rows_fetched")) or _as_int(row.get("v1_games_list_rows_fetched")) or _as_int(v1.get("games_list_rows_fetched"))
    errors = _as_int(row.get("response_errors")) or _as_int(row.get("v1_response_errors")) or _as_int(v1.get("response_errors"))
    deep = _as_int(_first_dict(row.get("sstats_deep")).get("contexts_enriched"))
    deep = deep or _as_int(row.get("v1_last_games_stats_fetched")) or _as_int(v1.get("last_games_stats_fetched"))
    api = payload.setdefault("api", {})
    sstats_api = dict(api.get("sstats") or {})
    if requests:
        sstats_api["requests"] = requests
    if contexts or _as_int(sstats_api.get("contexts")) == 0:
        sstats_api["contexts"] = contexts
    if rows:
        sstats_api["rows"] = rows
    sstats_api["errors"] = errors
    sstats_api["deep_enriched"] = deep
    sstats_api["runtime_patch"] = row.get("runtime_patch") or ""
    sstats_api["team_form_contexts"] = _as_int(row.get("v1_team_form_contexts_built")) or _as_int(v1.get("team_form_contexts_built"))
    sstats_api["direct_contexts"] = _as_int(row.get("v1_direct_contexts_built")) or _as_int(v1.get("direct_contexts_built"))
    api["sstats"] = sstats_api


def build_payload() -> dict[str, Any]:
    payload = _base_build_payload()
    _normalize_runtime_patched_sstats(payload)
    plan = _load_progressive_plan()
    payload["version"] = "harizon-telegram-report-v8-progressive-core-coverage"
    payload.setdefault("diagnostics", {})["progressive_core_coverage"] = {
        "contract": plan.get("contract") if isinstance(plan.get("contract"), dict) else {},
        "counts": plan.get("counts") if isinstance(plan.get("counts"), dict) else {},
        "gap_sample_size": len(plan.get("core_gap_sample") or plan.get("gap_sample") or []) if isinstance(plan, dict) else 0,
    }
    day_summary = _load_json(EXPORT_DIR / "latest-day-inventory-summary.json")
    truth_counts = day_summary.get("coverage_truth_counts") if isinstance(day_summary.get("coverage_truth_counts"), dict) else {}
    truth_report = _load_json(TRUTH_REPORT)
    if not truth_counts and isinstance(truth_report.get("counts"), dict):
        truth_counts = truth_report["counts"]
    if truth_counts:
        sources = day_summary.get("sources") if isinstance(day_summary.get("sources"), dict) else {}
        payload.setdefault("diagnostics", {})["coverage_truth"] = {
            "counts": truth_counts,
            "source": sources.get("coverage_truth") if isinstance(sources.get("coverage_truth"), dict) else {"path": str(TRUTH_REPORT)},
        }
    return payload



def _as_float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(str(value).replace(",", "."))
    except Exception:
        return 0.0


def _pct(part: Any, total: Any) -> str:
    p = _as_int(part)
    t = _as_int(total)
    if t <= 0:
        return "0%"
    return f"{round(p * 100.0 / t)}%"


def _reason_ru(reason: Any) -> str:
    try:
        return v7.v5.reason_ru(str(reason))
    except Exception:
        return str(reason or "").replace("_", " ")


def _top_reasons(payload: dict[str, Any], limit: int = 8) -> list[str]:
    reasons = payload.get("reasons") if isinstance(payload.get("reasons"), list) else []
    if not reasons:
        return ["• Нет reject reasons в свежих артефактах."]
    total = sum(_as_int(row.get("count")) for row in reasons if isinstance(row, dict)) or 1
    out: list[str] = []
    for row in reasons[:limit]:
        if not isinstance(row, dict):
            continue
        count = _as_int(row.get("count"))
        if count <= 0:
            continue
        ru = row.get("reason_ru") or _reason_ru(row.get("reason"))
        out.append(f"• {ru}: {count} ({round(count * 100.0 / total)}%)")
    return out or ["• Нет reject reasons в свежих артефактах."]


def _candidate_lines(payload: dict[str, Any], limit: int = 4) -> list[str]:
    samples = payload.get("samples") if isinstance(payload.get("samples"), dict) else {}
    evaluated = samples.get("fallback_evaluated") if isinstance(samples.get("fallback_evaluated"), list) else []
    rows = [row for row in evaluated if isinstance(row, dict)][:limit]
    if not rows:
        return ["• Нет проверенных reserve-кандидатов в свежем fallback report."]
    out: list[str] = []
    for idx, row in enumerate(rows, 1):
        metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
        odds = _as_float(metrics.get("odds") or row.get("odds"))
        ev = _as_float(metrics.get("canonical_ev_pct") or row.get("ev_pct"))
        edge = _as_float(metrics.get("canonical_edge_pp") or row.get("edge_pct"))
        q = _as_float(metrics.get("quality_score") or row.get("quality_score"))
        out.append(
            f"{idx}. {row.get('home_team')} — {row.get('away_team')} | {row.get('selection')} @{odds:.2f} | "
            f"EV {ev:+.1f}% | edge {edge:+.1f} п.п. | q {q:.1f}"
        )
        reasons = row.get("reject_reasons") if isinstance(row.get("reject_reasons"), list) else []
        if reasons:
            out.append("   • причина: " + ", ".join(_reason_ru(x) for x in reasons[:4]))
    return out


def _safe_line(value: Any, default: str = "н/д") -> str:
    text = str(value or "").strip()
    return text if text else default


def render(payload: dict[str, Any]) -> str:
    """Render the user-facing readable report.

    This intentionally does not call the v7 renderer.  v7 is still used for data
    normalization, but the Telegram message must stay in the clear HARIZON format
    and must describe the current bookmaker-quorum publication contract.
    """
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    funnel = payload.get("funnel") if isinstance(payload.get("funnel"), dict) else {}
    api = payload.get("api") if isinstance(payload.get("api"), dict) else {}
    line = payload.get("line_guard") if isinstance(payload.get("line_guard"), dict) else {}
    diag = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    truth = diag.get("coverage_truth") if isinstance(diag.get("coverage_truth"), dict) else {}
    truth_counts = truth.get("counts") if isinstance(truth.get("counts"), dict) else {}

    inv_total = _as_int(truth_counts.get("matches_total")) or _as_int(coverage.get("day_inventory_total"))
    with_odds = _as_int(truth_counts.get("matches_with_odds")) or _as_int(coverage.get("day_inventory_with_odds")) or _as_int(coverage.get("matches_with_offers"))
    with_context = _as_int(truth_counts.get("matches_with_context")) or _as_int(coverage.get("day_inventory_with_context")) or _as_int(coverage.get("matches_with_context"))
    price2 = _as_int(truth_counts.get("matches_with_2plus_price_confirmations")) or _as_int(coverage.get("matches_with_2plus_books"))
    odds_sources2 = _as_int(truth_counts.get("matches_with_2plus_odds_sources"))
    context2 = _as_int(truth_counts.get("matches_with_2plus_context_sources"))
    ready_model = _as_int(truth_counts.get("matches_ready_for_model")) or _as_int(coverage.get("ready_for_model"))
    ready_publish = _as_int(truth_counts.get("matches_ready_for_publish"))
    run_matches = _as_int(coverage.get("matches_seen"))

    top_reason = _reason_ru(payload.get("top_reason"))
    published = _as_int(funnel.get("published_count")) > 0 or payload.get("status") == "published"
    status_line = "✅ прогноз опубликован" if published else "🟡 Прогнозов нет: текущие кандидаты не прошли финальные guards."

    odds = api.get("odds_api_io") if isinstance(api.get("odds_api_io"), dict) else {}
    sstats = api.get("sstats") if isinstance(api.get("sstats"), dict) else {}
    bzz = api.get("bzzoiro") if isinstance(api.get("bzzoiro"), dict) else {}
    sport = api.get("sportlogic") if isinstance(api.get("sportlogic"), dict) else {}
    combos = coverage.get("secondary_combinations") if isinstance(coverage.get("secondary_combinations"), dict) else {}
    combo_text = ", ".join(f"{k}: {v}" for k, v in sorted(combos.items(), key=lambda item: -_as_int(item[1]))[:6]) or "н/д"

    lines: list[str] = [
        "🧾 HARIZON — понятный отчёт по запуску",
        status_line,
        f"• Главная причина: {top_reason}",
        "",
        "📦 Инвентарь и покрытие",
        f"• Инвентарь дня: {inv_total} матчей. В этом run обработано: {run_matches}.",
        f"• 1+ линия: {with_odds}/{inv_total} ({_pct(with_odds, inv_total)}) | 1+ контекст: {with_context}/{inv_total} ({_pct(with_context, inv_total)})",
        f"• 2+ букмекера: {price2}/{inv_total} ({_pct(price2, inv_total)})",
        f"• 2+ независимых odds-source: {odds_sources2}/{inv_total} ({_pct(odds_sources2, inv_total)}) — диагностика, не блок публикации",
        f"• 2+ контекста: {context2}/{inv_total} ({_pct(context2, inv_total)})",
        f"• Готово для модели: {ready_model}/{inv_total} ({_pct(ready_model, inv_total)})",
        "",
        "🏷️ A/B-tier публикация",
        f"• A-tier bookmaker coverage: {ready_publish} | опубликовано: {_as_int(funnel.get('published_count'))}",
        "  A-tier = 2+ букмекера по той же стороне рынка + 2+ контекста + подтверждённое движение линии + value.",
        f"• B-tier bookmaker coverage: {price2} | fallback опубликовано: {_as_int(funnel.get('fallback_published_count'))}",
        "  B-tier = 2+ букмекера + 1+ контекст + второй снимок линии + value сохранился.",
        "• 2+ independent odds-source теперь только диагностическая метрика, а не обязательный блок публикации.",
        "",
        "🛡️ Движение линии и финальный фильтр",
        f"• Pre-kickoff проверок: {_as_int(line.get('final_pre_kickoff_checks'))} | матчей без следующего регулярного run: {_as_int(line.get('no_more_regular_run_before_kickoff'))}",
        f"• Line guard: увидел {_as_int(line.get('seen'))}, оставил {_as_int(line.get('kept'))}, снял {_as_int(line.get('dropped'))}",
        "",
        "🧪 Воронка кандидатов",
        f"• Raw/candidates before quality: {_as_int(funnel.get('raw_candidates'))}/{_as_int(funnel.get('candidates_before_quality'))}",
        f"• Quality прошло: {_as_int(funnel.get('passed_candidates'))} | publishable: {_as_int(funnel.get('publishable_candidates'))} | опубликовано: {_as_int(funnel.get('published_count'))}",
        f"• Controlled fallback: seen {_as_int(funnel.get('fallback_candidates_seen'))} | evaluated {_as_int(funnel.get('fallback_evaluated'))} | published {_as_int(funnel.get('fallback_published_count'))}",
        "",
        "📡 Провайдеры: запросы и полезные данные",
        f"• odds-api.io: events req {_as_int(odds.get('events_req'))}, odds req {_as_int(odds.get('odds_req'))}; смэтчил матчей {_as_int(odds.get('matched'))}; offers {_as_int(odds.get('offers'))}; 2+ букмекера {_as_int(odds.get('books_2plus'))}; ошибок {_as_int(odds.get('errors'))}.",
        f"• Bzzoiro: direct req {_as_int(bzz.get('requests'))}, v2 req {_as_int(bzz.get('v2_requests'))}; v2 ctx {_as_int(bzz.get('v2_contexts'))}; v2 odds {_as_int(bzz.get('v2_odds_resources'))}; secondary offers {_as_int(bzz.get('secondary_offers_added'))}; overlap odds-api.io {_as_int(bzz.get('overlap'))}; ошибок {max(_as_int(bzz.get('errors')), _as_int(bzz.get('v2_errors')))}.",
        f"• SStats: запросы {_as_int(sstats.get('requests'))}; сырых строк {_as_int(sstats.get('rows'))}; контекстов {_as_int(sstats.get('contexts'))}; deep-enriched {_as_int(sstats.get('deep_enriched'))}; team-form {_as_int(sstats.get('team_form_contexts'))}; direct {_as_int(sstats.get('direct_contexts'))}; ошибок {_as_int(sstats.get('errors'))}.",
        f"• SportLogic: enabled {bool(sport.get('enabled'))}; запросы {_as_int(sport.get('requests'))}; fixtures {_as_int(sport.get('fixtures_fetched'))}; matched {_as_int(sport.get('matched'))}; odds req {_as_int(sport.get('odds_requests'))}; offers {_as_int(sport.get('offers'))}; ошибок {_as_int(sport.get('errors'))}; diag {_safe_line(sport.get('diagnosis'))}.",
        f"• Комбинации источников линий: {combo_text}",
        "",
        "🚫 Почему не опубликовано",
        *_top_reasons(payload),
        "",
        "🔎 Проверенные reserve-кандидаты",
        *_candidate_lines(payload),
        "",
        "📌 Что это значит",
    ]
    if published:
        lines.append("• Прогноз реально опубликован; отчёт выше показывает, через какой контракт он прошёл.")
    elif "line movement" in top_reason.lower() or "line_movement" in str(payload.get("top_reason")):
        lines.append("• Есть кандидат по bookmaker-contract, но нужен второй снимок линии. Ждём следующий регулярный run.")
    else:
        lines.append("• Не форсировать публикацию: текущие кандидаты отрезаны xG/quality/value/line movement, а не старым требованием 2 independent odds sources.")
    lines.append("• Ценовой контракт сейчас: 2+ букмекера по той же стороне рынка; price-integrity guard остаётся обязательным.")
    return "\n".join(lines)


v7.v5.build_payload = build_payload
v7.v5.render = render
v7.build_payload = build_payload
v7.render = render
_write_status({"status": "installed", "renderer": "v8", "main_module": "v7.v5", "format": "readable_bookmaker_quorum", "sstats_nested_normalizer": True})


if __name__ == "__main__":
    raise SystemExit(v7.v5.main())
