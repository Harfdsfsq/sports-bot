from __future__ import annotations

"""HARIZON Telegram run report v5.

Single-source factual report for Telegram.

Older report versions mixed numbers from debug summary, fallback report,
line-guard files, signal-stack files and post-run install-only reports. That made
Telegram sections contradict each other. This version first builds one normalized
payload from fresh artifacts and then renders all Telegram text from that single
payload.
"""

import json
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
DEBUG_PATH = Path(".logs/debug-last-run.json")
OUT_TXT = EXPORT_DIR / "latest-harizon-telegram-run-report.txt"
OUT_JSON = EXPORT_DIR / "latest-harizon-telegram-run-report.json"
OUT_V5_JSON = EXPORT_DIR / "latest-harizon-telegram-run-report-v5.json"
OUT_V5_TXT = EXPORT_DIR / "latest-harizon-telegram-run-report-v5.txt"


def load_json(path: str | Path, default: Any = None) -> Any:
    try:
        p = Path(path)
        if not p.exists() or p.stat().st_size <= 0:
            return default
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_text(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def as_int(value: Any, default: int = 0) -> int:
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


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def freshness_minutes(path: str | Path) -> float | None:
    try:
        p = Path(path)
        if not p.exists():
            return None
        return max(0.0, (datetime.now(UTC).timestamp() - p.stat().st_mtime) / 60.0)
    except Exception:
        return None


def is_fresh(path: str | Path, max_minutes: float = 120.0) -> bool:
    age = freshness_minutes(path)
    return age is not None and age <= max_minutes


def nested(row: dict[str, Any], key: str, default: Any = None) -> Any:
    if not isinstance(row, dict):
        return default
    for src in (row, row.get("stats"), row.get("status"), row.get("summary")):
        if isinstance(src, dict) and key in src:
            return src.get(key)
    return default


def first_dict(*values: Any) -> dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def reason_ru(reason: str) -> str:
    mapping = {
        "canonical_negative_value": "отрицательная canonical value после пересчёта по выбранному коэффициенту",
        "xg_direction_conflict": "направление ставки конфликтует с xG",
        "post_calibration_probability_guard": "вероятность после калибровки ниже порога",
        "quality_post_calibration_probability_guard": "quality: вероятность после калибровки ниже порога",
        "no_bet_quality_score_guard": "quality score ниже порога",
        "quality_no_bet_quality_score_guard": "quality score ниже порога",
        "odds_sources_below_2": "меньше 2 независимых источников линий",
        "context_sources_below_2": "меньше 2 источников контекста",
        "core_api_coverage_below_2_of_3": "меньше 2 из 3 core API",
        "needs_next_cron_line_movement_recheck": "нужен следующий cron для проверки движения линии",
        "price_dropped_too_much_since_previous_window": "коэффициент сильно просел после прошлого окна",
        "controlled_rescue_no_candidate": "controlled reserve не нашёл безопасного кандидата",
        "fallback_publish_no_candidate": "fallback-публикация: нет кандидата",
        "line_movement_guard_dropped": "line movement guard снял кандидата",
    }
    return mapping.get(str(reason), str(reason).replace("_", " "))


def load_artifacts() -> dict[str, Any]:
    artifacts = {
        "debug": load_json(DEBUG_PATH, {}),
        "fallback": load_json(EXPORT_DIR / "latest-controlled-fallback-report.json", {}),
        "fallback_guard": load_json(EXPORT_DIR / "latest-controlled-fallback-prepublish-guard.json", {}),
        "request_budget": load_json(EXPORT_DIR / "latest-provider-request-budget.json", {}),
        "quota": load_json(EXPORT_DIR / "latest-provider-quota-governor.json", {}),
        "runtime_policy": load_json(EXPORT_DIR / "latest-harizon-runtime-policy.json", {}),
        "refresh_plan": load_json(EXPORT_DIR / "latest-day-inventory-refresh-plan.json", {}),
        "priority_state": load_json(EXPORT_DIR / "latest-day-inventory-priority-and-line-state.json", {}),
        "line_guard": load_json(EXPORT_DIR / "latest-line-movement-guard-report.json", {}),
        "rescue": load_json(EXPORT_DIR / "latest-rescue-candidates.json", {}),
        "picks": load_json(EXPORT_DIR / "latest-picks.json", []),
        "pending": load_json(EXPORT_DIR / "latest-pending-bets.json", []),
        "signal_stack": load_json(EXPORT_DIR / "latest-signal-stack-runtime.json", {}),
        "secondary_matching": load_json(EXPORT_DIR / "latest-secondary-provider-matching.json", {}),
        "windowed_audit": load_json(EXPORT_DIR / "latest-windowed-core-candidate-audit.json", {}),
        "windowed_coverage": load_json(EXPORT_DIR / "latest-windowed-core-coverage.json", {}),
        "windowed_filter": load_json(EXPORT_DIR / "latest-windowed-core-publication-filter.json", {}),
        "sportlogic_debug": load_json(EXPORT_DIR / "latest-sportlogic-debug.json", {}),
        "sportlogic_final_guard": load_json(EXPORT_DIR / "latest-windowed-core-report-and-sportlogic-final-guard.json", {}),
        "run_log_exists": Path(EXPORT_DIR / "latest-run-bot.log").exists(),
    }
    return artifacts


def source_stats_from(artifacts: dict[str, Any]) -> dict[str, Any]:
    debug = first_dict(artifacts.get("debug"))
    summary = first_dict(debug.get("summary"))
    source_stats = first_dict(summary.get("source_stats"), debug.get("source_stats"))
    normalized = {str(k): dict(v) for k, v in source_stats.items() if isinstance(v, dict)}

    signal = first_dict(artifacts.get("signal_stack"))
    secondary = first_dict(artifacts.get("secondary_matching"), signal.get("secondary_matching"))
    if signal:
        bzz = dict(normalized.get("bzzoiro", {}))
        bzz["secondary_offers_added"] = as_int(signal.get("bzzoiro_secondary_offers_added"))
        bzz["metric_contexts_enhanced"] = as_int(signal.get("metric_contexts_enhanced"))
        bzz["secondary_offer_matches"] = as_int(first_dict(secondary.get("offer_sources_match_counts")).get("bzzoiro"))
        bzz["combo_with_odds_api_io"] = as_int(first_dict(secondary.get("offer_source_combinations")).get("bzzoiro+odds_api_io"))
        if bzz:
            normalized["bzzoiro"] = bzz
    if secondary:
        normalized["secondary_matching"] = {
            "matches_with_offers": as_int(secondary.get("matches_with_offers")),
            "source_combinations": secondary.get("offer_source_combinations") or {},
            "source_match_counts": secondary.get("offer_sources_match_counts") or {},
        }

    sportlogic_dbg = first_dict(artifacts.get("sportlogic_debug"))
    if sportlogic_dbg:
        stats = first_dict(sportlogic_dbg.get("stats"))
        if stats:
            row = dict(normalized.get("sportlogic", {}))
            row.update({k: v for k, v in stats.items() if k != "last_body_preview"})
            normalized["sportlogic"] = row
    return normalized


def build_truth_payload() -> dict[str, Any]:
    artifacts = load_artifacts()
    debug = first_dict(artifacts.get("debug"))
    summary = first_dict(debug.get("summary"))
    source_stats = source_stats_from(artifacts)
    fallback = first_dict(artifacts.get("fallback"))
    rescue = first_dict(artifacts.get("rescue"))
    rescue_counts = first_dict(rescue.get("counts"))
    refresh_plan = first_dict(artifacts.get("refresh_plan"))
    priority_state = first_dict(artifacts.get("priority_state"))
    line_guard = first_dict(artifacts.get("line_guard"))
    windowed = first_dict(artifacts.get("windowed_audit"), artifacts.get("windowed_coverage"))
    windowed_filter = first_dict(artifacts.get("windowed_filter"))
    picks = artifacts.get("picks") if isinstance(artifacts.get("picks"), list) else []
    pending = artifacts.get("pending") if isinstance(artifacts.get("pending"), list) else []

    odds = first_dict(source_stats.get("odds_api_io"))
    bzz = first_dict(source_stats.get("bzzoiro"))
    sstats = first_dict(source_stats.get("sstats"))
    sportlogic = first_dict(source_stats.get("sportlogic"))
    secondary = first_dict(source_stats.get("secondary_matching"))

    candidates_before_quality = max(
        as_int(summary.get("candidates_before_quality")),
        as_int(rescue_counts.get("candidates_before_quality")),
        as_int(fallback.get("pool_counts", {}).get("debug_candidates_before_quality") if isinstance(fallback.get("pool_counts"), dict) else 0),
    )
    raw_candidates = max(as_int(summary.get("candidates_raw")), candidates_before_quality)
    publishable_candidates = max(as_int(summary.get("publishable_candidates")), as_int(rescue_counts.get("publishable_candidates")), as_int(windowed_filter.get("kept")))
    published_count = len(picks) + len([x for x in pending if isinstance(x, dict) and x.get("telegram_sent")])

    evaluated = fallback.get("evaluated") if isinstance(fallback.get("evaluated"), list) else []
    reason_counter: Counter[str] = Counter()
    for row in evaluated:
        if not isinstance(row, dict):
            continue
        for reason in row.get("reject_reasons") or []:
            reason_counter[str(reason)] += 1
        metrics = first_dict(row.get("metrics"))
        for reason in metrics.get("quality_reasons") or []:
            reason_counter["quality_" + str(reason)] += 1
    for src in (debug.get("rejections"), fallback.get("reason_counts"), fallback.get("reject_reasons"), fallback.get("top_reject_reasons")):
        if isinstance(src, dict):
            for key, value in src.items():
                reason_counter[str(key)] += as_int(value)
    if as_int(line_guard.get("candidates_dropped")) > 0:
        reason_counter["line_movement_guard_dropped"] += as_int(line_guard.get("candidates_dropped"))

    coverage = {
        "matches_seen": as_int(summary.get("matches_seen")),
        "day_inventory_total": as_int(summary.get("day_inventory_total"), as_int(priority_state.get("active_matches"))),
        "matches_with_offers": as_int(summary.get("matches_with_offers")),
        "matches_with_context": as_int(summary.get("contexts_built")),
        "ready_for_model": as_int(summary.get("ready_for_model"), as_int(summary.get("matches_ready_for_model"))),
        "odds_offers_primary": as_int(odds.get("offers_parsed")),
        "bzzoiro_secondary_offers_added": as_int(bzz.get("secondary_offers_added")),
        "matches_with_2plus_books": as_int(odds.get("matches_with_2plus_books")),
        "secondary_matches_with_offers": as_int(secondary.get("matches_with_offers")),
        "secondary_combinations": secondary.get("source_combinations") or {},
        "bzzoiro_odds_overlap_with_odds_api_io": as_int(bzz.get("combo_with_odds_api_io")),
    }

    funnel = {
        "raw_candidates": raw_candidates,
        "candidates_before_quality": candidates_before_quality,
        "passed_candidates": as_int(rescue_counts.get("passed_candidates")),
        "publishable_candidates": publishable_candidates,
        "published_count": published_count,
        "fallback_candidates_seen": as_int(fallback.get("candidates_seen")),
        "fallback_evaluated": len(evaluated),
        "fallback_published": bool(fallback.get("published")),
        "windowed_audit_candidates": as_int(windowed.get("candidates_in")),
        "windowed_publish_allowed": as_int(windowed.get("publish_allowed_by_coverage")),
        "windowed_publish_blocked": as_int(windowed.get("publish_blocked_by_coverage")),
        "publish_filter_input": as_int(windowed_filter.get("input")),
        "publish_filter_kept": as_int(windowed_filter.get("kept")),
        "publish_filter_blocked": as_int(windowed_filter.get("blocked")),
    }

    api = {
        "odds_api_io": {
            "events_req": as_int(odds.get("event_requests")),
            "odds_req": as_int(odds.get("odds_requests")),
            "errors": as_int(odds.get("response_errors")),
            "matched": as_int(odds.get("events_matched")),
            "offers": as_int(odds.get("offers_parsed")),
            "books_2plus": as_int(odds.get("matches_with_2plus_books")),
            "bookmakers": odds.get("bookmakers_seen_names") or [],
        },
        "sstats": {
            "requests": as_int(sstats.get("requests")),
            "errors": as_int(sstats.get("response_errors")),
            "contexts": as_int(sstats.get("contexts_built")),
            "rows": as_int(sstats.get("rows_fetched")),
            "deep": sstats.get("sstats_deep") or {},
        },
        "bzzoiro": {
            "requests": as_int(bzz.get("requests")),
            "errors": as_int(bzz.get("response_errors")),
            "events": as_int(bzz.get("events_fetched"), as_int(bzz.get("rows_fetched"))),
            "contexts": as_int(bzz.get("contexts_built")),
            "secondary_offers_added": as_int(bzz.get("secondary_offers_added")),
            "secondary_offer_matches": as_int(bzz.get("secondary_offer_matches")),
            "overlap_with_odds_api_io": as_int(bzz.get("combo_with_odds_api_io")),
        },
        "sportlogic": {
            "enabled": bool(first_dict(artifacts.get("sportlogic_final_guard")).get("sportlogic", {}).get("enabled") if isinstance(first_dict(artifacts.get("sportlogic_final_guard")).get("sportlogic"), dict) else sportlogic.get("enabled")),
            "requests": as_int(sportlogic.get("requests")),
            "odds_requests": as_int(sportlogic.get("odds_requests")),
            "errors": as_int(sportlogic.get("response_errors")),
            "matched": as_int(sportlogic.get("events_matched")),
            "offers": as_int(sportlogic.get("offers_parsed")),
            "fixtures": as_int(sportlogic.get("fixtures_fetched")),
        },
    }

    final_checks = as_int(refresh_plan.get("final_pre_kickoff_checks"))
    line_guard_summary = {
        "candidates_seen": as_int(line_guard.get("candidates_seen")),
        "kept": as_int(line_guard.get("candidates_kept")),
        "dropped": as_int(line_guard.get("candidates_dropped")),
        "final_pre_kickoff_checks": final_checks,
        "no_more_regular_run_before_kickoff": as_int(refresh_plan.get("no_more_regular_run_before_kickoff")),
    }

    status = "no_picks"
    status_ru = "🟡 прогнозов нет"
    if published_count > 0:
        status = "published"
        status_ru = "✅ прогноз опубликован"
    elif coverage["matches_with_offers"] <= 0:
        status = "no_lines"
        status_ru = "🔴 нет свежих линий"
    elif raw_candidates <= 0:
        status = "lines_but_no_raw_candidates"
        status_ru = "🟠 линии есть, raw-кандидатов нет"
    elif publishable_candidates <= 0:
        status = "candidates_but_quality_rejected"
        status_ru = "🟡 кандидаты есть, quality/value не пропустили"
    elif funnel["publish_filter_blocked"] > 0:
        status = "coverage_guard_blocked"
        status_ru = "🟡 кандидаты есть, coverage/movement guard заблокировал публикацию"

    if reason_counter:
        top_reason = reason_counter.most_common(1)[0][0]
    elif fallback.get("status"):
        top_reason = str(fallback.get("status"))
    else:
        top_reason = "n/a"

    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "version": "harizon-telegram-report-v5-single-source",
        "status": status,
        "status_ru": status_ru,
        "top_reason": top_reason,
        "coverage": coverage,
        "funnel": funnel,
        "api": api,
        "line_guard": line_guard_summary,
        "reasons": [{"reason": k, "reason_ru": reason_ru(k), "count": int(v)} for k, v in reason_counter.most_common(12) if int(v) > 0],
        "samples": {
            "fallback_evaluated": evaluated[:5],
            "top_priority_matches": (refresh_plan.get("top_priority_matches") if isinstance(refresh_plan.get("top_priority_matches"), list) else [])[:6],
        },
        "artifacts": {
            "debug_age_min": freshness_minutes(DEBUG_PATH),
            "run_log_age_min": freshness_minutes(EXPORT_DIR / "latest-run-bot.log"),
            "rescue_age_min": freshness_minutes(EXPORT_DIR / "latest-rescue-candidates.json"),
            "fallback_age_min": freshness_minutes(EXPORT_DIR / "latest-controlled-fallback-report.json"),
            "signal_stack_age_min": freshness_minutes(EXPORT_DIR / "latest-signal-stack-runtime.json"),
            "fresh_run_log": is_fresh(EXPORT_DIR / "latest-run-bot.log", 120),
        },
    }
    return payload


def fmt_pct_prob(value: Any) -> str:
    return f"{as_float(value) * 100:.1f}%"


def render_report(payload: dict[str, Any]) -> str:
    coverage = payload["coverage"]
    funnel = payload["funnel"]
    api = payload["api"]
    line_guard = payload["line_guard"]
    lines: list[str] = []
    lines.append("🧾 HARIZON run report v5 — единая фактическая сводка")
    lines.append(f"• Итог: {payload['status_ru']}")
    lines.append(f"• Главная причина: {reason_ru(payload.get('top_reason'))}")
    lines.append("")

    lines.append("📦 Покрытие")
    lines.append(f"• Матчи в run: {coverage['matches_seen']} | day inventory: {coverage['day_inventory_total']}")
    lines.append(f"• С линиями: {coverage['matches_with_offers']} | с контекстом: {coverage['matches_with_context']} | ready model: {coverage['ready_for_model']}")
    lines.append(f"• odds-api.io offers: {coverage['odds_offers_primary']} | Bzzoiro secondary offers: {coverage['bzzoiro_secondary_offers_added']}")
    lines.append(f"• 2+ букмекера odds-api.io: {coverage['matches_with_2plus_books']} | 2-source overlap Bzzoiro+odds-api.io: {coverage['bzzoiro_odds_overlap_with_odds_api_io']}")
    combos = coverage.get("secondary_combinations") if isinstance(coverage.get("secondary_combinations"), dict) else {}
    if combos:
        combo_text = ", ".join(f"{k}: {v}" for k, v in sorted(combos.items(), key=lambda item: -as_int(item[1]))[:6])
        lines.append(f"• Source combinations: {combo_text}")
    lines.append("")

    lines.append("🧪 Воронка кандидатов")
    lines.append(f"• Raw/candidates before quality: {funnel['raw_candidates']} / {funnel['candidates_before_quality']}")
    lines.append(f"• Passed quality: {funnel['passed_candidates']} | publishable: {funnel['publishable_candidates']} | опубликовано: {funnel['published_count']}")
    lines.append(f"• Controlled fallback: seen {funnel['fallback_candidates_seen']} | evaluated {funnel['fallback_evaluated']} | published {funnel['fallback_published']}")
    if funnel["windowed_audit_candidates"] or funnel["publish_filter_input"]:
        lines.append(f"• Windowed coverage: audit {funnel['windowed_audit_candidates']} | allowed {funnel['windowed_publish_allowed']} | blocked {funnel['windowed_publish_blocked']} | publish-filter input {funnel['publish_filter_input']}")
    lines.append("")

    lines.append("🛡️ Pre-publish / line movement")
    lines.append(f"• Final pre-kickoff checks: {line_guard['final_pre_kickoff_checks']} | no next regular run: {line_guard['no_more_regular_run_before_kickoff']}")
    lines.append(f"• Line guard: seen {line_guard['candidates_seen']} | kept {line_guard['kept']} | dropped {line_guard['dropped']}")
    lines.append("")

    lines.append("📡 Core API")
    odds = api["odds_api_io"]
    lines.append(f"• odds_api_io: events req {odds['events_req']}, odds req {odds['odds_req']}, matched {odds['matched']}, offers {odds['offers']}, 2+ books {odds['books_2plus']}, err {odds['errors']}")
    sstats = api["sstats"]
    deep = sstats.get("deep") if isinstance(sstats.get("deep"), dict) else {}
    deep_text = f", deep enriched {as_int(deep.get('contexts_enriched'))}" if deep else ""
    lines.append(f"• sstats: req {sstats['requests']}, ctx {sstats['contexts']}, rows {sstats['rows']}, err {sstats['errors']}{deep_text}")
    bzz = api["bzzoiro"]
    lines.append(f"• bzzoiro: req {bzz['requests']}, ctx {bzz['contexts']}, events {bzz['events']}, secondary offers {bzz['secondary_offers_added']}, overlap odds-api.io {bzz['overlap_with_odds_api_io']}, err {bzz['errors']}")
    sport = api["sportlogic"]
    lines.append(f"• sportlogic: enabled {sport['enabled']}, req {sport['requests']}, odds req {sport['odds_requests']}, matched {sport['matched']}, offers {sport['offers']}, err {sport['errors']}")
    lines.append("")

    reasons = payload.get("reasons") if isinstance(payload.get("reasons"), list) else []
    lines.append("🚫 Почему не опубликовано")
    if reasons:
        total = sum(as_int(x.get("count")) for x in reasons) or 1
        for row in reasons[:8]:
            count = as_int(row.get("count"))
            lines.append(f"• {row.get('reason_ru')}: {count} ({round(count * 100.0 / total)}%)")
    else:
        lines.append("• Нет reject reasons в свежих артефактах.")

    evaluated = payload.get("samples", {}).get("fallback_evaluated") if isinstance(payload.get("samples"), dict) else []
    if isinstance(evaluated, list) and evaluated:
        lines.append("")
        lines.append("🔎 Проверенные reserve-кандидаты")
        for idx, row in enumerate([x for x in evaluated if isinstance(x, dict)][:4], 1):
            metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
            ev = as_float(metrics.get("canonical_ev_pct"))
            edge = as_float(metrics.get("canonical_edge_pp"))
            q = as_float(metrics.get("quality_score"))
            odds = as_float(metrics.get("odds"))
            reasons = ", ".join(reason_ru(str(x)) for x in (row.get("reject_reasons") or [])[:3])
            lines.append(f"{idx}. {row.get('home_team')} — {row.get('away_team')} | {row.get('selection')} @{odds:.2f} | EV {ev:+.1f}% | edge {edge:+.1f} п.п. | q {q:.1f}")
            if reasons:
                lines.append(f"   • reject: {reasons}")
    lines.append("")
    lines.append("📌 Вывод")
    if payload["status"] == "published":
        lines.append("• Прогноз реально опубликован; все цифры выше взяты из одного нормализованного payload v5.")
    elif coverage["bzzoiro_odds_overlap_with_odds_api_io"] < 15:
        lines.append("• Главный технический bottleneck: мало матчей с 2 independent odds sources. Нужно добирать SportLogic/Bzzoiro overlap, а не ослаблять guards.")
    elif funnel["fallback_evaluated"] > 0:
        lines.append("• Candidate pipeline работает: резерв проверял кандидатов, но value/xG/quality не разрешили публикацию.")
    else:
        lines.append("• Нужно смотреть candidate factory/mapping: линий и контекста достаточно, но кандидаты не дошли до проверки.")
    lines.append("• Отчёт v5 использует один payload, поэтому секции не должны противоречить друг другу.")
    return "\n".join(lines)


def split_message(text: str, soft_limit: int = 3600) -> list[str]:
    if len(text) <= soft_limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        add_len = len(line) + 1
        if current and current_len + add_len > soft_limit:
            chunks.append("\n".join(current))
            current = []
            current_len = 0
        current.append(line)
        current_len += add_len
    if current:
        chunks.append("\n".join(current))
    return chunks


def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    ok = True
    for idx, part in enumerate(split_message(text, int(os.getenv("TELEGRAM_MESSAGE_SOFT_LIMIT") or 3600)), 1):
        if len(split_message(text)) > 1:
            part = f"🧾 Подробный отчёт run v5 — часть {idx}/{len(split_message(text))}\n\n" + part
        data = parse.urlencode({"chat_id": chat_id, "text": part, "disable_web_page_preview": "true"}).encode("utf-8")
        try:
            req = request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=data, method="POST")
            with request.urlopen(req, timeout=20) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                if '"ok":true' not in body:
                    ok = False
        except Exception:
            ok = False
    return ok


def main() -> int:
    payload = build_truth_payload()
    text = render_report(payload)
    payload["text_length"] = len(text)
    payload["telegram_sent"] = False
    write_json(OUT_V5_JSON, payload)
    write_text(OUT_V5_TXT, text + "\n")
    write_json(OUT_JSON, payload)
    write_text(OUT_TXT, text + "\n")
    if os.getenv("TELEGRAM_CHAT_ID") and (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")):
        payload["telegram_sent"] = send_telegram(text)
        write_json(OUT_V5_JSON, payload)
        write_json(OUT_JSON, payload)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
