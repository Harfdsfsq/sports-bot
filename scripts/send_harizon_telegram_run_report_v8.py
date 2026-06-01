from __future__ import annotations

"""HARIZON Telegram run report v8: human-readable coverage report.

This renderer keeps the v7/v5 payload builder, but replaces the Telegram text
with a clear operational summary:
- inventory and current-run coverage;
- 1+ and 2+ line/context coverage;
- A-tier / B-tier publication readiness;
- line movement lifecycle;
- concise rejection reasons and next technical action.
"""

import importlib.util
import json
from pathlib import Path
from typing import Any

V7_PATH = Path(__file__).with_name("send_harizon_telegram_run_report_v7.py")
EXPORT_DIR = Path(".data/exports")
PROGRESSIVE_PLAN = EXPORT_DIR / "latest-progressive-coverage-plan.json"
TRUTH_REPORT = EXPORT_DIR / "latest-day-inventory-coverage-truth.json"
FALLBACK_REPORT = EXPORT_DIR / "latest-controlled-fallback-report.json"
FALLBACK_PUBLISHED_PICKS = EXPORT_DIR / "latest-controlled-fallback-published-picks.json"
BZZOIRO_CONTEXT_GAP_REPORT = EXPORT_DIR / "latest-bzzoiro-context-gap-finalizer.json"
DAY_SUMMARY = EXPORT_DIR / "latest-day-inventory-summary.json"
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


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value)))
    except Exception:
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value))
    except Exception:
        return default


def _pct(part: Any, total: Any) -> str:
    p = _as_int(part)
    t = _as_int(total)
    if t <= 0:
        return "0%"
    return f"{round(p * 100.0 / t)}%"


def _first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _load_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists() and path.stat().st_size > 0:
            payload = json.loads(path.read_text(encoding="utf-8"))
            return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}
    return {}


def _load_json_any(path: Path) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return None


def _tier_code(row: dict[str, Any]) -> str:
    raw = str(row.get("tier") or row.get("publication_tier") or row.get("tier_code") or "").strip().lower()
    if raw in {"a", "tier_a", "a-tier", "уровень a", "уровень а", "а"}:
        return "A"
    if raw in {"b", "tier_b", "b-tier", "уровень b", "уровень б", "б"}:
        return "B"
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    odds_sources = _as_int(row.get("odds_sources_count") or metrics.get("odds_sources_count"))
    confirmations = _as_int(row.get("confirmation_sources_count") or metrics.get("confirmation_sources_count") or row.get("sources_count") or metrics.get("sources_count"))
    books = _as_int(row.get("books_count") or metrics.get("books_count"))
    if odds_sources >= 2 and confirmations >= 2 and books >= 2:
        return "A"
    if odds_sources >= 1 or confirmations >= 1 or books >= 1:
        return "B"
    return ""


def _fallback_tier_counts(report: dict[str, Any]) -> dict[str, int]:
    rows = report.get("selected_all") if isinstance(report.get("selected_all"), list) else []
    if not rows and isinstance(report.get("selected"), dict):
        rows = [report["selected"]]
    selected = [row for row in rows if isinstance(row, dict)]

    published_picks = _load_json_any(FALLBACK_PUBLISHED_PICKS)
    if isinstance(published_picks, list) and published_picks:
        selected = [row for row in published_picks if isinstance(row, dict)] or selected

    published = bool(report.get("published")) or str(report.get("status") or "") == "published" or bool(selected and isinstance(published_picks, list))
    out = {
        "published_total": len(selected) if published else 0,
        "selected_total": len(selected),
        "tier_a_published": 0,
        "tier_b_published": 0,
        "tier_a_selected": 0,
        "tier_b_selected": 0,
    }
    for row in selected:
        tier = _tier_code(row)
        if tier == "A":
            out["tier_a_selected"] += 1
            if published:
                out["tier_a_published"] += 1
        elif tier == "B":
            out["tier_b_selected"] += 1
            if published:
                out["tier_b_published"] += 1
    return out

def _write_status(payload: dict[str, Any]) -> None:
    try:
        V8_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        V8_STATUS_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def _reason_ru(reason: Any) -> str:
    text = str(reason or "n/a")
    try:
        return str(v7.v5.reason_ru(text))
    except Exception:
        return text


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



def _augment_bzzoiro_from_gap_report(payload: dict[str, Any]) -> None:
    gap = _load_json(BZZOIRO_CONTEXT_GAP_REPORT)
    stats = gap.get("stats") if isinstance(gap.get("stats"), dict) else {}
    if not stats:
        return
    api = payload.setdefault("api", {})
    bzz = dict(api.get("bzzoiro") or {})
    gap_requests = _as_int(stats.get("requests"))
    if gap_requests:
        bzz["context_gap_requests"] = gap_requests
        bzz["requests_total_effective"] = _as_int(bzz.get("requests_total_effective")) + gap_requests
    bzz["context_gap_targets"] = _as_int(stats.get("target_matches"))
    bzz["context_gap_added"] = _as_int(stats.get("contexts_added"))
    bzz["context_gap_matched"] = _as_int(stats.get("matched"))
    bzz["context_gap_v1_events"] = _as_int(stats.get("v1_events_fetched"))
    bzz["context_gap_v1_predictions"] = _as_int(stats.get("v1_predictions_fetched"))
    bzz["context_gap_v2_events"] = _as_int(stats.get("v2_events_fetched"))
    bzz["context_gap_errors"] = _as_int(stats.get("errors"))
    bzz["v2_stats_resources"] = _as_int(bzz.get("v2_stats_resources")) + _as_int(stats.get("stats_resources"))
    bzz["v2_odds_resources"] = _as_int(bzz.get("v2_odds_resources")) + _as_int(stats.get("odds_resources"))
    bzz["v2_lineups_resources"] = _as_int(bzz.get("v2_lineups_resources")) + _as_int(stats.get("lineups_resources"))
    bzz["contexts_total_effective"] = max(_as_int(bzz.get("contexts_total_effective")), _as_int(bzz.get("contexts")) + _as_int(stats.get("contexts_added")))
    api["bzzoiro"] = bzz

def build_payload() -> dict[str, Any]:
    payload = _base_build_payload()
    _normalize_runtime_patched_sstats(payload)
    _augment_bzzoiro_from_gap_report(payload)

    plan = _load_json(PROGRESSIVE_PLAN)
    day_summary = _load_json(DAY_SUMMARY)
    truth_report = _load_json(TRUTH_REPORT)
    fallback_report = _load_json(FALLBACK_REPORT)
    truth_counts = day_summary.get("coverage_truth_counts") if isinstance(day_summary.get("coverage_truth_counts"), dict) else {}
    if not truth_counts and isinstance(truth_report.get("counts"), dict):
        truth_counts = truth_report["counts"]

    payload["version"] = "harizon-telegram-report-v8-human-readable-provider-coverage"
    diagnostics = payload.setdefault("diagnostics", {})
    diagnostics["progressive_core_coverage"] = {
        "contract": plan.get("contract") if isinstance(plan.get("contract"), dict) else {},
        "counts": plan.get("counts") if isinstance(plan.get("counts"), dict) else {},
        "gap_sample_size": len(plan.get("core_gap_sample") or plan.get("gap_sample") or []) if isinstance(plan, dict) else 0,
    }
    if truth_counts:
        sources = day_summary.get("sources") if isinstance(day_summary.get("sources"), dict) else {}
        diagnostics["coverage_truth"] = {
            "counts": truth_counts,
            "source": sources.get("coverage_truth") if isinstance(sources.get("coverage_truth"), dict) else {"path": str(TRUTH_REPORT)},
        }
    if fallback_report:
        diagnostics["controlled_fallback_report"] = fallback_report
        diagnostics["controlled_fallback_tiers"] = _fallback_tier_counts(fallback_report)
        funnel = payload.setdefault("funnel", {})
        if isinstance(funnel, dict):
            tier_counts = diagnostics["controlled_fallback_tiers"]
            funnel["fallback_published_tier_a"] = tier_counts.get("tier_a_published", 0)
            funnel["fallback_published_tier_b"] = tier_counts.get("tier_b_published", 0)
    if day_summary:
        diagnostics["day_inventory_summary"] = day_summary.get("counts") if isinstance(day_summary.get("counts"), dict) else {}
    return payload


def _coverage_counts(payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    coverage = payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {}
    diag = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    truth = diag.get("coverage_truth") if isinstance(diag.get("coverage_truth"), dict) else {}
    truth_counts = truth.get("counts") if isinstance(truth.get("counts"), dict) else {}
    summary_counts = diag.get("day_inventory_summary") if isinstance(diag.get("day_inventory_summary"), dict) else {}
    prog = diag.get("progressive_core_coverage") if isinstance(diag.get("progressive_core_coverage"), dict) else {}
    progressive_counts = prog.get("counts") if isinstance(prog.get("counts"), dict) else {}
    merged_truth = {**summary_counts, **truth_counts}
    return coverage, merged_truth, progressive_counts


def _status_explanation(payload: dict[str, Any], truth: dict[str, Any]) -> str:
    published = _as_int(_first_dict(payload.get("funnel")).get("published_count"))
    diag = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    fb_tiers = diag.get("controlled_fallback_tiers") if isinstance(diag.get("controlled_fallback_tiers"), dict) else {}
    if published > 0 or _as_int(fb_tiers.get("published_total")) > 0 or payload.get("status") == "published":
        return "✅ Прогноз опубликован. Ниже — проверка покрытия и причин, почему опубликован именно этот кандидат."
    waiting = _as_int(truth.get("matches_waiting_line_movement")) + _as_int(truth.get("candidate_lifecycle_waiting_line_movement"))
    tier_b_cov = _as_int(truth.get("matches_tier_b_coverage_ready"))
    tier_a_cov = _as_int(truth.get("matches_tier_a_coverage_ready"))
    if waiting > 0:
        return f"🟡 Прогнозов нет: есть {waiting} матчей, которые ждут второй снимок линии. Это нормально для B-tier/A-tier lifecycle."
    if tier_b_cov > 0 and _as_int(truth.get("matches_ready_for_publish_tier_b")) == 0:
        return "🟡 Прогнозов нет: B-tier покрытие есть, но нет подтверждённого движения линии или value не сохранился."
    if tier_a_cov > 0 and _as_int(truth.get("matches_ready_for_publish_tier_a")) == 0:
        return "🟡 Прогнозов нет: A-tier покрытие есть, но финальные guards не дали безопасную ставку."
    return f"🟡 Прогнозов нет: { _reason_ru(payload.get('top_reason')) }."


def _source_summary(coverage: dict[str, Any]) -> str:
    combos = coverage.get("secondary_combinations") if isinstance(coverage.get("secondary_combinations"), dict) else {}
    if not combos:
        return "• Комбинации источников линий: данных нет."
    parts = [f"{name}: {count}" for name, count in sorted(combos.items(), key=lambda item: -_as_int(item[1]))[:6]]
    return "• Комбинации источников линий: " + ", ".join(parts)


def _safe_rate(numerator: Any, denominator: Any) -> str:
    den = _as_int(denominator)
    if den <= 0:
        return "0/запрос"
    value = _as_float(numerator) / den
    if value >= 100:
        return f"{value:.0f}/запрос"
    if value >= 10:
        return f"{value:.1f}/запрос"
    return f"{value:.2f}/запрос"


def _core_provider_lines(api: dict[str, Any], coverage: dict[str, Any]) -> list[str]:
    odds_api = api.get("odds_api_io") if isinstance(api.get("odds_api_io"), dict) else {}
    sstats = api.get("sstats") if isinstance(api.get("sstats"), dict) else {}
    bzz = api.get("bzzoiro") if isinstance(api.get("bzzoiro"), dict) else {}
    sport = api.get("sportlogic") if isinstance(api.get("sportlogic"), dict) else {}

    odds_requests = _as_int(odds_api.get("events_req")) + _as_int(odds_api.get("odds_req"))
    if odds_requests <= 0:
        odds_requests = _as_int(odds_api.get("requests"))
    bzz_direct_requests = _as_int(bzz.get("requests"))
    bzz_v2_requests = _as_int(bzz.get("v2_requests"))
    bzz_gap_requests = _as_int(bzz.get("context_gap_requests"))
    bzz_requests = bzz_direct_requests + bzz_v2_requests + bzz_gap_requests
    if bzz_requests <= 0:
        bzz_requests = _as_int(bzz.get("requests_total_effective"))
    sstats_requests = _as_int(sstats.get("requests"))
    sport_requests = _as_int(sport.get("requests")) or _as_int(sport.get("odds_requests"))

    bzz_context = _as_int(bzz.get("contexts_total_effective")) or _as_int(bzz.get("contexts")) or _as_int(bzz.get("v2_contexts"))
    bzz_events = _as_int(bzz.get("events_total_effective")) or _as_int(bzz.get("events")) or _as_int(bzz.get("v2_events"))
    bzz_secondary = _as_int(bzz.get("secondary_offers_added"))
    bzz_overlap = _as_int(bzz.get("overlap"))
    bzz_resources = _as_int(bzz.get("v2_stats_resources")) + _as_int(bzz.get("v2_odds_resources")) + _as_int(bzz.get("v2_lineups_resources"))

    lines = [
        "📡 Провайдеры: запросы и полезные данные",
        (
            f"• odds-api.io: запросы {odds_requests} "
            f"(events {_as_int(odds_api.get('events_req'))}, odds {_as_int(odds_api.get('odds_req'))}); "
            f"смэтчил матчей {_as_int(odds_api.get('matched'))}; "
            f"дал offers {_as_int(odds_api.get('offers'))} ({_safe_rate(odds_api.get('offers'), odds_requests)}); "
            f"2+ букмекера {_as_int(odds_api.get('books_2plus'))}; "
            f"ошибок {_as_int(odds_api.get('errors'))}."
        ),
        (
            f"• Bzzoiro: запросы {bzz_requests} "
            f"(direct {bzz_direct_requests}, v2 {bzz_v2_requests}, gap {bzz_gap_requests}); "
            f"событий {bzz_events}; контекстов {bzz_context}; "
            f"gap targets {_as_int(bzz.get('context_gap_targets'))}, matched {_as_int(bzz.get('context_gap_matched'))}, added {_as_int(bzz.get('context_gap_added'))}; "
            f"v2 resources stats/odds/lineups {bzz_resources}; "
            f"secondary offers {bzz_secondary}; overlap с odds-api.io {bzz_overlap}; "
            f"ошибок {_as_int(bzz.get('errors')) or _as_int(bzz.get('v2_errors'))}."
        ),
        (
            f"• SStats: запросы {sstats_requests}; "
            f"сырых строк {_as_int(sstats.get('rows'))} ({_safe_rate(sstats.get('rows'), sstats_requests)}); "
            f"контекстов {_as_int(sstats.get('contexts'))}; deep-enriched {_as_int(sstats.get('deep_enriched'))}; "
            f"team-form {_as_int(sstats.get('team_form_contexts'))}; direct {_as_int(sstats.get('direct_contexts'))}; "
            f"ошибок {_as_int(sstats.get('errors'))}."
        ),
        (
            f"• SportLogic: enabled {bool(sport.get('enabled'))}; запросы {sport_requests}; "
            f"fixtures/games {_as_int(sport.get('fixtures_fetched')) or _as_int(sport.get('games_fetched'))}; "
            f"matched {_as_int(sport.get('matched'))}; odds req {_as_int(sport.get('odds_requests'))}; "
            f"offers {_as_int(sport.get('offers'))}; ошибок {_as_int(sport.get('errors'))}; "
            f"diag {sport.get('diagnosis') or 'n/a'}."
        ),
        _source_summary(coverage),
    ]
    return lines


def _coverage_window_lines(truth: dict[str, Any], progressive: dict[str, Any], inventory_total: int) -> list[str]:
    active = _as_int(truth.get("active_matches")) or _as_int(progressive.get("matches_tracked"))
    odds_refresh = _as_int(truth.get("odds_refresh_needed"))
    win4_total = _as_int(progressive.get("window_0_4h"))
    win4_ready = _as_int(progressive.get("window_0_4h_core_ready_2plus_both") or progressive.get("window_0_4h_ready_2plus_both"))
    win12_total = _as_int(progressive.get("window_0_12h"))
    win12_ready = _as_int(progressive.get("window_0_12h_core_ready_2plus_both") or progressive.get("window_0_12h_ready_2plus_both"))
    core_ready = _as_int(progressive.get("core_ready_2plus_both") or progressive.get("ready_2plus_both"))
    lines = [
        "🧭 Покрытие ближайших окон",
        f"• Активных матчей сейчас: {active or inventory_total}. Нужно обновить линии: {odds_refresh}.",
        f"• Core-ready 2+/2+ по всем tracked: {core_ready}.",
        f"• 0–4 часа: {win4_ready}/{win4_total} core-ready.",
        f"• 0–12 часов: {win12_ready}/{win12_total} core-ready.",
    ]
    return lines


def _render_reasons(payload: dict[str, Any]) -> list[str]:
    reasons = payload.get("reasons") if isinstance(payload.get("reasons"), list) else []
    lines = ["🚫 Почему не опубликовано"]
    if payload.get("status") == "published":
        return ["🚫 Почему часть кандидатов отсеяна", "• Не применимо к публикации: один прогноз уже отправлен."]
    if not reasons:
        return lines + ["• В свежих артефактах нет явных reject reasons."]
    total = sum(_as_int(row.get("count")) for row in reasons if isinstance(row, dict)) or 1
    for row in [x for x in reasons if isinstance(x, dict)][:8]:
        count = _as_int(row.get("count"))
        lines.append(f"• {_reason_ru(row.get('reason_ru') or row.get('reason'))}: {count} ({_pct(count, total)})")
    return lines


def _render_samples(payload: dict[str, Any]) -> list[str]:
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
        selection = row.get("selection") or row.get("market") or "ставка"
        odds = _as_float(metrics.get("odds"))
        ev = _as_float(metrics.get("canonical_ev_pct"))
        edge = _as_float(metrics.get("canonical_edge_pp"))
        q = _as_float(metrics.get("quality_score"))
        odds_text = f" @{odds:.2f}" if odds > 0 else ""
        lines.append(f"{idx}. {home} — {away} | {selection}{odds_text} | EV {ev:+.1f}% | edge {edge:+.1f} п.п. | q {q:.1f}")
        reject = ", ".join(_reason_ru(x) for x in (row.get("reject_reasons") or [])[:3])
        if reject:
            lines.append(f"   • причина: {reject}")
    return lines


def _reason_tokens(payload: dict[str, Any]) -> list[str]:
    tokens: list[str] = []
    for value in (payload.get("top_reason"), payload.get("status"), payload.get("status_ru")):
        if value not in (None, ""):
            tokens.append(str(value).lower())
    reasons = payload.get("reasons") if isinstance(payload.get("reasons"), list) else []
    for row in reasons:
        if not isinstance(row, dict):
            continue
        for key in ("reason", "reason_ru"):
            value = row.get(key)
            if value not in (None, ""):
                tokens.append(str(value).lower())
    samples = payload.get("samples") if isinstance(payload.get("samples"), dict) else {}
    for key in ("fallback_evaluated", "candidates", "recent_candidates"):
        rows = samples.get(key) if isinstance(samples.get(key), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            for reason in row.get("reject_reasons") or []:
                tokens.append(str(reason).lower())
            for reason in row.get("reject_reasons_ru") or []:
                tokens.append(str(reason).lower())
    return tokens


def _current_candidates_wait_for_line_movement(payload: dict[str, Any]) -> bool:
    for token in _reason_tokens(payload):
        if "line_movement" in token and ("awaiting_next_run" in token or "needs_next_cron" in token or "not_confirmed:awaiting" in token):
            return True
        if "line movement" in token and ("awaiting next run" in token or "needs next cron" in token):
            return True
    return False


def _current_candidates_blocked_by_quality(payload: dict[str, Any]) -> bool:
    quality_markers = (
        "xg_direction_conflict",
        "xg direction",
        "quality",
        "post_calibration",
        "market_sanity",
        "value",
        "edge",
        "ev",
        "вероятность",
        "направление ставки конфликтует",
    )
    return any(any(marker in token for marker in quality_markers) for token in _reason_tokens(payload))


def _next_step(payload: dict[str, Any], truth: dict[str, Any], progressive: dict[str, Any]) -> str:
    if _as_int(_first_dict(payload.get("funnel")).get("published_count")) > 0:
        return "• Следующий шаг: не форсировать объём, а ждать новых top-кандидатов в следующих окнах."
    if _current_candidates_wait_for_line_movement(payload):
        return "• Следующий шаг: дождаться следующего регулярного run — он сделает второй снимок линии и решит publish/decline."
    if _current_candidates_blocked_by_quality(payload):
        return "• Следующий шаг: не форсировать публикацию; текущие кандидаты отрезаны quality/xG/value, ждать новых top-кандидатов."
    if _as_int(_first_dict(payload.get("funnel")).get("published_count")) > 0:
        return "• Следующий шаг: не форсировать объём, а ждать новых top-кандидатов в следующих окнах."
    if _as_int(truth.get("matches_waiting_line_movement")) > 0:
        return "• Следующий шаг: дождаться следующего регулярного run — он сделает второй снимок линии и решит publish/decline."
    if _as_int(truth.get("matches_with_2plus_odds_sources")) == 0 and _as_int(truth.get("matches_with_2plus_price_confirmations")) > 0:
        return "• Главный gap: есть подтверждения цены, но нет 2 независимых odds-source. Нужно добирать Bzzoiro/SportLogic overlap."
    if _as_int(truth.get("matches_with_2plus_context_sources")) < max(5, _as_int(truth.get("matches_total")) // 10):
        return "• Главный gap: мало 2+ контекста. Нужно добирать Bzzoiro/SStats context на ближайшие окна."
    if _as_int(progressive.get("window_0_4h_core_ready_2plus_both") or progressive.get("window_0_4h_ready_2plus_both")) == 0:
        return "• Главный gap: ближайшее окно 0–4ч не готово. Нужно приоритизировать coverage именно по матчам, которые скоро начнутся."
    return "• Следующий шаг: не ослаблять guards; проблема сейчас в качестве кандидатов/value, а не только в покрытии."


def render(payload: dict[str, Any]) -> str:
    coverage, truth, progressive = _coverage_counts(payload)
    funnel = payload.get("funnel") if isinstance(payload.get("funnel"), dict) else {}
    api = payload.get("api") if isinstance(payload.get("api"), dict) else {}
    line_guard = payload.get("line_guard") if isinstance(payload.get("line_guard"), dict) else {}

    inventory_total = _as_int(truth.get("matches_total")) or _as_int(coverage.get("day_inventory_total"))
    run_matches = _as_int(coverage.get("matches_seen"))
    lines_1plus = _as_int(truth.get("matches_with_1plus_line_evidence")) or _as_int(truth.get("matches_with_odds")) or _as_int(coverage.get("day_inventory_with_odds")) or _as_int(coverage.get("matches_with_offers"))
    context_1plus = _as_int(truth.get("matches_with_context")) or _as_int(coverage.get("day_inventory_with_context")) or _as_int(coverage.get("matches_with_context"))
    price_2plus = _as_int(truth.get("matches_with_2plus_price_confirmations"))
    odds_source_2plus = _as_int(truth.get("matches_with_2plus_odds_sources"))
    context_2plus = _as_int(truth.get("matches_with_2plus_context_sources"))
    ready_model = _as_int(truth.get("matches_ready_for_model")) or _as_int(coverage.get("ready_for_model"))

    tier_a_cov = _as_int(truth.get("matches_tier_a_coverage_ready"))
    tier_b_cov = _as_int(truth.get("matches_tier_b_coverage_ready"))
    tier_a_ready = _as_int(truth.get("matches_ready_for_publish_tier_a"))
    tier_b_ready = _as_int(truth.get("matches_ready_for_publish_tier_b"))
    waiting_movement = _as_int(truth.get("matches_waiting_line_movement")) + _as_int(truth.get("candidate_lifecycle_waiting_line_movement"))
    declined_after_second = _as_int(truth.get("matches_declined_after_second_snapshot"))
    diag = payload.get("diagnostics") if isinstance(payload.get("diagnostics"), dict) else {}
    fallback_tiers = diag.get("controlled_fallback_tiers") if isinstance(diag.get("controlled_fallback_tiers"), dict) else {}
    fallback_a_published = _as_int(fallback_tiers.get("tier_a_published"))
    fallback_b_published = _as_int(fallback_tiers.get("tier_b_published"))

    odds_api = api.get("odds_api_io") if isinstance(api.get("odds_api_io"), dict) else {}
    sstats = api.get("sstats") if isinstance(api.get("sstats"), dict) else {}
    bzz = api.get("bzzoiro") if isinstance(api.get("bzzoiro"), dict) else {}
    sport = api.get("sportlogic") if isinstance(api.get("sportlogic"), dict) else {}

    core_ready = _as_int(progressive.get("core_ready_2plus_both") or progressive.get("ready_2plus_both"))
    win4_total = _as_int(progressive.get("window_0_4h"))
    win4_ready = _as_int(progressive.get("window_0_4h_core_ready_2plus_both") or progressive.get("window_0_4h_ready_2plus_both"))
    win12_total = _as_int(progressive.get("window_0_12h"))
    win12_ready = _as_int(progressive.get("window_0_12h_core_ready_2plus_both") or progressive.get("window_0_12h_ready_2plus_both"))

    lines: list[str] = [
        "🧾 HARIZON — понятный отчёт по запуску",
        _status_explanation(payload, truth),
        f"• Главная причина: {_reason_ru(payload.get('top_reason'))}",
        "",
        "📦 Инвентарь и покрытие",
        f"• Инвентарь дня: {inventory_total} матчей. В этом run обработано: {run_matches}.",
        f"• 1+ линия: {lines_1plus}/{inventory_total} ({_pct(lines_1plus, inventory_total)}) | 1+ контекст: {context_1plus}/{inventory_total} ({_pct(context_1plus, inventory_total)})",
        f"• 2+ подтверждения цены: {price_2plus}/{inventory_total} ({_pct(price_2plus, inventory_total)})",
        f"• 2+ независимых odds-source: {odds_source_2plus}/{inventory_total} ({_pct(odds_source_2plus, inventory_total)})",
        f"• 2+ контекста: {context_2plus}/{inventory_total} ({_pct(context_2plus, inventory_total)})",
        f"• Готово для модели: {ready_model}/{inventory_total} ({_pct(ready_model, inventory_total)})",
        "",
        "🏷️ A/B-tier публикация",
        f"• A-tier coverage: {tier_a_cov} | A-tier готово main: {tier_a_ready} | опубликовано fallback: {fallback_a_published}",
        f"  A-tier = 2+ odds-source + 2+ context + подтверждённое движение линии + value.",
        f"• B-tier coverage: {tier_b_cov} | B-tier готово main: {tier_b_ready} | опубликовано fallback: {fallback_b_published}",
        f"  B-tier = 1+ odds-source + 2+ букмекера + 1+ context + второй снимок линии + value сохранился.",
        f"• Ждут второй снимок линии: {waiting_movement} | отклонены после второго снимка: {declined_after_second}",
        "",
        "🛡️ Движение линии и финальный фильтр",
        f"• Pre-kickoff проверок: {_as_int(line_guard.get('final_pre_kickoff_checks'))} | матчей без следующего регулярного run: {_as_int(line_guard.get('no_more_regular_run_before_kickoff'))}",
        f"• Line guard: увидел {_as_int(line_guard.get('seen'))}, оставил {_as_int(line_guard.get('kept'))}, снял {_as_int(line_guard.get('dropped'))}",
        "",
        "🧪 Воронка кандидатов",
        f"• Raw/candidates before quality: {_as_int(funnel.get('raw_candidates'))}/{_as_int(funnel.get('candidates_before_quality'))}",
        f"• Quality прошло: {_as_int(funnel.get('passed_candidates'))} | publishable: {_as_int(funnel.get('publishable_candidates'))} | опубликовано: {_as_int(funnel.get('published_count'))}",
        f"• Controlled fallback: seen {_as_int(funnel.get('fallback_candidates_seen'))} | evaluated {_as_int(funnel.get('fallback_evaluated'))} | published {_as_int(funnel.get('fallback_published_count'))}",
        "",
    ]

    lines += _core_provider_lines(api, coverage)

    if progressive:
        lines += [""] + _coverage_window_lines(truth, progressive, inventory_total)

    lines += [""] + _render_reasons(payload)
    samples = _render_samples(payload)
    if samples:
        lines += [""] + samples

    lines += [
        "",
        "📌 Что это значит",
        _next_step(payload, truth, progressive),
        "• Коротко: отчёт теперь разделяет покрытие, качество, A/B-tier и line movement — чтобы было видно, где именно узкое место.",
    ]
    return "\n".join(lines)


v7.v5.build_payload = build_payload
v7.v5.render = render
v7.build_payload = build_payload
v7.render = render
_write_status({"status": "installed", "renderer": "v8-human-readable-provider-coverage", "main_module": "v7.v5", "sstats_nested_normalizer": True})


if __name__ == "__main__":
    raise SystemExit(v7.v5.main())
