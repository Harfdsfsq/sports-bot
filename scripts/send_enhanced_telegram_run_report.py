from __future__ import annotations

import json
import os
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
DEBUG_PATH = Path(".logs/debug-last-run.json")
RUN_BOT_LOG_PATH = EXPORT_DIR / "latest-run-bot.log"
OUT_TXT = EXPORT_DIR / "latest-enhanced-telegram-run-report.txt"
OUT_JSON = EXPORT_DIR / "latest-enhanced-telegram-run-report.json"
FRESHNESS_MINUTES = 45


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(float(str(raw).strip())) if raw not in (None, "") else default
    except Exception:
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value))
    except Exception:
        return default


def load_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def read_text(path: str | Path) -> str:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def write_text(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def payload_timestamp(payload: dict[str, Any]) -> datetime | None:
    if not isinstance(payload, dict):
        return None
    candidates = [
        payload.get("created_at_utc"),
        payload.get("created_at"),
        payload.get("updated_at"),
        payload.get("reference_run_utc"),
    ]
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    candidates.extend([
        summary.get("current_time_utc"),
        summary.get("started_time_utc"),
        summary.get("current_time_local"),
        summary.get("started_time_local"),
    ])
    for value in candidates:
        dt = parse_dt(value)
        if dt is not None:
            return dt
    return None


def is_fresh(payload: dict[str, Any], reference: datetime | None, max_minutes: int = FRESHNESS_MINUTES) -> bool:
    if not isinstance(payload, dict) or not payload:
        return False
    ts = payload_timestamp(payload)
    if ts is None:
        return False
    if reference is None:
        return ts >= datetime.now(UTC) - timedelta(minutes=max_minutes)
    return abs((reference - ts).total_seconds()) <= max_minutes * 60


def newest_timestamp(*payloads: dict[str, Any]) -> datetime | None:
    timestamps = [payload_timestamp(payload) for payload in payloads if isinstance(payload, dict)]
    timestamps = [ts for ts in timestamps if ts is not None]
    return max(timestamps) if timestamps else None


def freshness_row(name: str, payload: dict[str, Any], reference: datetime | None) -> dict[str, Any]:
    ts = payload_timestamp(payload) if isinstance(payload, dict) else None
    fresh = is_fresh(payload, reference)
    age_minutes = None
    if ts is not None and reference is not None:
        age_minutes = round((reference - ts).total_seconds() / 60.0, 1)
    return {
        "name": name,
        "present": bool(isinstance(payload, dict) and payload),
        "fresh": fresh,
        "timestamp_utc": ts.isoformat() if ts else None,
        "age_minutes_vs_reference": age_minutes,
    }


def source_freshness_lines(rows: list[dict[str, Any]]) -> list[str]:
    important = [row for row in rows if row.get("present") or row.get("name") in {"debug", "run_summary", "detailed", "fallback"}]
    if not important:
        return []
    lines = ["🧭 Состояние артефактов"]
    for row in important:
        status = "свежий" if row.get("fresh") else ("устарел" if row.get("present") else "нет")
        age = row.get("age_minutes_vs_reference")
        suffix = f", Δ {age:+.1f} мин" if isinstance(age, (int, float)) else ""
        ts = row.get("timestamp_utc") or "н/д"
        lines.append(f"• {row.get('name')}: {status} | {ts}{suffix}")
    return lines


def clean_gha_log(text: str) -> str:
    lines: list[str] = []
    for line in str(text or "").splitlines():
        match = re.match(r"^\ufeff?\d{4}-\d{2}-\d{2}T[^Z]+Z\s+(.*)$", line)
        if match:
            line = match.group(1)
        line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        lines.append(line)
    return "\n".join(lines)


def extract_json_objects(text: str) -> list[dict[str, Any]]:
    cleaned = clean_gha_log(text)
    decoder = json.JSONDecoder()
    out: list[dict[str, Any]] = []
    idx = 0
    while True:
        start = cleaned.find("{", idx)
        if start < 0:
            break
        try:
            payload, end = decoder.raw_decode(cleaned[start:])
            if isinstance(payload, dict):
                out.append(payload)
            idx = start + max(end, 1)
        except Exception:
            idx = start + 1
    return out


def load_stdout_summary() -> dict[str, Any]:
    for payload in reversed(extract_json_objects(read_text(RUN_BOT_LOG_PATH))):
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else payload
        if isinstance(summary, dict) and (
            "matches_seen" in summary
            or "matches_with_offers" in summary
            or "candidates_before_quality" in summary
        ):
            return payload if "summary" in payload else {"summary": summary}
    return {}


def compact_value(value: Any) -> str:
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    return str(value)


def top_counter(counter_like: Any, limit: int = 8) -> list[tuple[str, int]]:
    counter = Counter()
    if isinstance(counter_like, dict):
        for key, value in counter_like.items():
            counter[str(key)] += as_int(value)
    return counter.most_common(limit)


def reason_ru(reason: str) -> str:
    reason = str(reason or "")
    mapping = {
        "quarter_total_line_removed": "quarter total line удалена старым guard’ом",
        "quarter_total_line_allowed": "quarter total line разрешена",
        "quarter_total_insufficient_books": "quarter total line: мало букмекеров",
        "unsupported_total_line": "unsupported total line",
        "market_derived_signal_guard_totals": "market-derived totals: сигнал ещё не готов",
        "market_derived_signal_guard_spreads": "market-derived spreads: сигнал ещё не готов",
        "market_derived_signal_guard_h2h": "market-derived 1X2: сигнал ещё не готов",
        "publish_books_guard": "недостаточно линий букмекеров для публикации",
        "edge_below_threshold": "value-edge ниже порога",
        "ev_below_threshold": "EV ниже порога",
        "confidence_below_threshold": "уверенность ниже порога",
        "missing_context_totals": "нет контекста для totals-модели",
        "missing_context_h2h": "нет контекста для 1X2-модели",
        "missing_context_spreads": "нет контекста для handicap-модели",
        "market_integrity_insufficient_market_depth": "market integrity: малая глубина рынка",
        "market_integrity_spreads_quarantined": "market integrity: форы в карантине",
        "fallback_publish_no_candidate": "fallback-публикация: нет кандидата",
    }
    return mapping.get(reason, reason.replace("_", " "))


def get_summary(debug: dict[str, Any], stdout_payload: dict[str, Any], detailed: dict[str, Any], run_summary: dict[str, Any], reference: datetime | None) -> dict[str, Any]:
    candidates = [
        debug.get("summary") if isinstance(debug, dict) else None,
        stdout_payload.get("summary") if isinstance(stdout_payload, dict) else None,
    ]
    if is_fresh(detailed, reference):
        candidates.append(detailed.get("summary") if isinstance(detailed.get("summary"), dict) else None)
    if is_fresh(run_summary, reference):
        candidates.append(run_summary.get("summary") if isinstance(run_summary.get("summary"), dict) else None)
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def get_source_stats(summary: dict[str, Any], debug: dict[str, Any], stdout_payload: dict[str, Any], detailed: dict[str, Any], reference: datetime | None) -> dict[str, Any]:
    candidates = [
        summary.get("source_stats"),
        debug.get("source_stats") if isinstance(debug, dict) else None,
        (stdout_payload.get("summary") or {}).get("source_stats") if isinstance(stdout_payload.get("summary"), dict) else None,
    ]
    if is_fresh(detailed, reference):
        candidates.append((detailed.get("summary") or {}).get("source_stats") if isinstance(detailed.get("summary"), dict) else None)
    for candidate in candidates:
        if isinstance(candidate, dict):
            return candidate
    return {}


def get_rejections(summary: dict[str, Any], debug: dict[str, Any], stdout_payload: dict[str, Any], detailed: dict[str, Any], reference: datetime | None) -> dict[str, Any]:
    candidates = [
        debug.get("rejections") if isinstance(debug, dict) else None,
        stdout_payload.get("rejections") if isinstance(stdout_payload, dict) else None,
        (stdout_payload.get("summary") or {}).get("rejections") if isinstance(stdout_payload.get("summary"), dict) else None,
        summary.get("rejections"),
    ]
    if is_fresh(detailed, reference):
        candidates.extend([
            detailed.get("rejections") if isinstance(detailed, dict) else None,
            (detailed.get("summary") or {}).get("rejections") if isinstance(detailed.get("summary"), dict) else None,
        ])
    for candidate in candidates:
        if isinstance(candidate, dict) and candidate:
            return candidate
    return {}


def get_run_summary_rejections(run_summary: dict[str, Any]) -> dict[str, Any]:
    for key in ("top_reject_reasons", "reason_counts", "reject_reasons", "rejections"):
        raw = run_summary.get(key) if isinstance(run_summary, dict) else None
        if isinstance(raw, dict) and raw:
            return raw
    return {}


def get_provider_diag(summary: dict[str, Any], debug: dict[str, Any], stdout_payload: dict[str, Any]) -> dict[str, Any]:
    for candidate in (
        debug.get("provider_diagnostics") if isinstance(debug, dict) else None,
        stdout_payload.get("provider_diagnostics") if isinstance(stdout_payload, dict) else None,
        (stdout_payload.get("summary") or {}).get("provider_diagnostics") if isinstance(stdout_payload.get("summary"), dict) else None,
        summary.get("provider_diagnostics"),
    ):
        if isinstance(candidate, dict):
            return candidate
    return {}


def provider_rows(provider_diag: dict[str, Any], source_stats: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    providers = provider_diag.get("providers") if isinstance(provider_diag.get("providers"), dict) else {}
    rows: dict[str, dict[str, Any]] = {}
    if isinstance(providers, dict):
        for name, row in providers.items():
            if isinstance(row, dict):
                rows[str(name)] = row
    for name, stats in source_stats.items():
        if isinstance(stats, dict):
            rows.setdefault(str(name), {"stats": stats})
    order = [
        "match_bootstrap", "odds_api_io", "odds_api_io_bootstrap", "allsportsapi", "sportlogic",
        "sstats", "bzzoiro", "api_football", "espn", "football_data", "thesportsdb",
        "futrixmetrics", "openfootball", "newsapi", "gnews", "weather", "self_history", "market_monitor",
    ]
    return sorted(rows.items(), key=lambda item: (order.index(item[0]) if item[0] in order else 99, item[0]))


def stat_value(row: dict[str, Any], key: str, default: Any = None) -> Any:
    for src in (row.get("stats"), row.get("status"), row):
        if isinstance(src, dict) and key in src:
            return src.get(key)
    return default


def provider_line(name: str, row: dict[str, Any]) -> str:
    parts: list[str] = []
    matches_with_data = row.get("matches_with_data")
    items_total = row.get("items_total")
    if matches_with_data is not None or items_total is not None:
        parts.append(f"data {as_int(matches_with_data)}/{as_int(items_total)}")
    for key, label in (
        ("event_requests", "events req"),
        ("odds_requests", "odds req"),
        ("requests", "req"),
        ("response_errors", "err"),
        ("events_fetched", "events"),
        ("matches_built", "matches"),
        ("events_matched", "matched"),
        ("offers_parsed", "offers"),
        ("contexts_built", "ctx"),
        ("rows_fetched", "rows"),
        ("weatherapi_requests", "weatherapi"),
        ("openweathermap_requests", "owm"),
        ("cache_hits", "cache"),
        ("rate_limited", "429"),
        ("budget_exhausted", "budget_exhausted"),
    ):
        value = stat_value(row, key)
        if value not in (None, "", [], {}):
            if isinstance(value, bool):
                if value:
                    parts.append(label)
            else:
                parts.append(f"{label} {compact_value(value)}")
    if not parts:
        enabled = stat_value(row, "enabled")
        if enabled is False:
            parts.append("disabled")
        else:
            parts.append("нет данных")
    return f"• {name}: " + ", ".join(parts)


def quota_lines() -> list[str]:
    quota = load_json(EXPORT_DIR / "latest-provider-quota-governor.json", {})
    rows = quota.get("providers") if isinstance(quota, dict) else []
    if not isinstance(rows, list):
        return []
    important = {"odds_api_io", "sportlogic", "sstats", "bzzoiro", "football_data", "thesportsdb", "weatherapi", "openweathermap", "newsapi", "gnews", "guardian", "currents"}
    out: list[str] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        provider = str(row.get("provider") or "")
        if provider not in important:
            continue
        grant = as_int(row.get("granted"))
        after = row.get("tokens_after")
        skip = row.get("skip_reason")
        suffix = f", skip {skip}" if skip else ""
        out.append(f"• {provider}: grant {grant}, tokens_after {after}{suffix}")
    return out


def lifecycle_lines(reference: datetime | None, limit: int = 5) -> list[str]:
    payload = load_json(EXPORT_DIR / "latest-candidate-lifecycle-report.json", {})
    if not is_fresh(payload, reference):
        return []
    decision = payload.get("decision") if isinstance(payload, dict) else {}
    blocked = decision.get("blocked_top") if isinstance(decision, dict) else []
    if not isinstance(blocked, list):
        return []
    out: list[str] = []
    for row in blocked[:limit]:
        if not isinstance(row, dict):
            continue
        home = str(row.get("home_team") or "?")
        away = str(row.get("away_team") or "?")
        family = str(row.get("family") or "?")
        selection = str(row.get("selection") or "?")
        point = row.get("point")
        odds = row.get("last_odds")
        metrics = row.get("last_metrics") if isinstance(row.get("last_metrics"), dict) else {}
        reasons = row.get("block_reasons") if isinstance(row.get("block_reasons"), list) else []
        reason_text = "; ".join(str(x) for x in reasons[:3]) or "нет причины"
        out.append(
            f"• {home} — {away}: {family} {selection}"
            f"{f' ({point})' if point not in (None, '') else ''} @ {odds}; "
            f"EV {as_float(metrics.get('ev_pct')):+.1f}% | edge {as_float(metrics.get('edge_pp')):+.1f} п.п. | "
            f"block: {reason_text}"
        )
    return out


def build_report() -> str:
    debug = load_json(DEBUG_PATH, {})
    stdout_payload = load_stdout_summary()
    detailed_raw = load_json(EXPORT_DIR / "latest-detailed-run-report.json", {})
    run_summary_raw = load_json(EXPORT_DIR / "latest-run-summary.json", {})
    fallback_raw = load_json(EXPORT_DIR / "latest-controlled-fallback-report.json", {})
    reference = newest_timestamp(debug, stdout_payload, detailed_raw, run_summary_raw, fallback_raw) or datetime.now(UTC)
    debug_fresh = debug if is_fresh(debug, reference) else {}
    stdout_fresh = stdout_payload if is_fresh(stdout_payload, reference) else {}
    detailed = detailed_raw if is_fresh(detailed_raw, reference) else {}
    run_summary = run_summary_raw if is_fresh(run_summary_raw, reference) else {}
    fallback = fallback_raw if is_fresh(fallback_raw, reference) else {}
    freshness_rows = [
        freshness_row("debug", debug, reference),
        freshness_row("stdout", stdout_payload, reference),
        freshness_row("detailed", detailed_raw, reference),
        freshness_row("run_summary", run_summary_raw, reference),
        freshness_row("fallback", fallback_raw, reference),
    ]
    summary = get_summary(debug_fresh, stdout_fresh, detailed, run_summary, reference)
    source_stats = get_source_stats(summary, debug_fresh, stdout_fresh, detailed, reference)
    rejections = get_rejections(summary, debug_fresh, stdout_fresh, detailed, reference) or get_run_summary_rejections(run_summary)
    provider_diag = get_provider_diag(summary, debug_fresh, stdout_fresh)

    started = summary.get("started_time_local") or summary.get("current_time_local") or run_summary.get("created_at_utc") or datetime.now(UTC).isoformat()
    publish_window = summary.get("publish_window_hours") or ((summary.get("filtering") or {}).get("publish_window_hours") if isinstance(summary.get("filtering"), dict) else None) or os.getenv("PUBLISH_WINDOW_HOURS") or "12"
    min_lead = ((summary.get("filtering") or {}).get("min_kickoff_lead_minutes") if isinstance(summary.get("filtering"), dict) else None) or os.getenv("MIN_KICKOFF_LEAD_MINUTES") or "30"
    bankroll = load_json(EXPORT_DIR / "latest-bankroll.json", {})
    current_bank = bankroll.get("current_balance", summary.get("bankroll_current_balance", 0)) if isinstance(bankroll, dict) else 0
    open_risk = bankroll.get("open_exposure", summary.get("bankroll_open_exposure", 0)) if isinstance(bankroll, dict) else 0

    matches_before = summary.get("matches_before_publish_window") or summary.get("matches_seen") or 0
    matches_seen = summary.get("matches_seen") or 0
    matches_with_offers = summary.get("matches_with_offers") if summary.get("matches_with_offers") is not None else 0
    contexts = summary.get("contexts_built") or 0
    candidates_raw = summary.get("candidates_raw") if summary.get("candidates_raw") is not None else 0
    candidates_before_quality = summary.get("candidates_before_quality") if summary.get("candidates_before_quality") is not None else 0
    candidates_after_quality = summary.get("candidates") if summary.get("candidates") is not None else 0
    publishable = summary.get("candidates_publishable") if summary.get("candidates_publishable") is not None else 0
    published = summary.get("published") if summary.get("published") is not None else 0

    filtering = summary.get("filtering") if isinstance(summary.get("filtering"), dict) else {}
    context_enrichment = summary.get("context_enrichment") if isinstance(summary.get("context_enrichment"), dict) else {}
    provider_targets = summary.get("provider_context_targets") if isinstance(summary.get("provider_context_targets"), dict) else {}
    mapping = summary.get("mapping") if isinstance(summary.get("mapping"), dict) else {}

    lines: list[str] = []
    lines.append("🧾 Подробный отчёт run")
    lines.append(f"🕒 Время запуска: {started}")
    lines.append(f"📅 Окно публикации: {publish_window} ч | Мин. запас до матча: {min_lead} мин")
    lines.append(f"💼 Банк: {as_float(current_bank):.2f} | Открытый риск: {as_float(open_risk):.2f}")
    if not debug_fresh and not stdout_fresh:
        lines.append("⚠️ Внимание: свежего debug/stdout payload нет; отчёт собран по последнему run-summary.")
    lines.append("")
    freshness = source_freshness_lines(freshness_rows)
    if freshness:
        lines.extend(freshness)
        lines.append("")
    lines.append("⚙️ Воронка run")
    lines.append(f"• Матчи всего/до фильтра: {matches_before}")
    lines.append(f"• Матчи в окне: {matches_seen} | с офферами: {matches_with_offers} | контекстов: {contexts}")
    lines.append(f"• Кандидаты: raw {candidates_raw} | до quality {candidates_before_quality} | после quality {candidates_after_quality} | publishable {publishable} | published {published}")
    if filtering:
        lines.append(
            "• Фильтр времени: "
            f"after {as_int(filtering.get('total_after'))}/{as_int(filtering.get('total_before'))}, "
            f"outside {as_int(filtering.get('skipped_outside_window'))}, "
            f"too soon {as_int(filtering.get('skipped_too_soon'))}, started {as_int(filtering.get('skipped_started'))}"
        )
    if context_enrichment:
        lines.append(
            "• Context shortlist: "
            f"eligible {as_int(context_enrichment.get('eligible_matches'))}, "
            f"selected {as_int(context_enrichment.get('selected_matches'))}, "
            f"value_hint {as_int(context_enrichment.get('matches_with_value_hint'))}, "
            f"limit {as_int(context_enrichment.get('limit'))}"
        )
    if provider_targets:
        rendered_targets = ", ".join(f"{k}:{v}" for k, v in provider_targets.items() if as_int(v) > 0)
        if rendered_targets:
            lines.append(f"• Provider targets: {rendered_targets}")
    if isinstance(mapping, dict) and mapping:
        lines.append(
            "• Matching: "
            f"odds exact {as_int(mapping.get('matched_exact'))}, loose {as_int(mapping.get('matched_loose'))}, fuzzy {as_int(mapping.get('matched_fuzzy'))}; "
            f"sstats exact {as_int(mapping.get('sstats_exact'))}, bzzoiro ctx {as_int(mapping.get('bzzoiro_contexts'))}, thesportsdb ctx {as_int(mapping.get('thesportsdb_contexts'))}"
        )

    lines.append("")
    lines.append("📡 Источники / фактическая работа")
    for name, row in provider_rows(provider_diag, source_stats)[:22]:
        if name in {"bookies_api", "oddspapi"}:
            continue
        text = provider_line(name, row)
        if "нет данных" in text and name not in {"market_monitor", "self_history"}:
            continue
        lines.append(text)

    quota = quota_lines()
    if quota:
        lines.append("")
        lines.append("🔌 Квоты / grants")
        lines.extend(quota[:12])

    lines.append("")
    lines.append("🚫 Почему не прошли")
    top_reasons = top_counter(rejections, 10)
    if top_reasons:
        for reason, count in top_reasons:
            lines.append(f"• {reason_ru(reason)} — {count}")
    else:
        if as_int(candidates_before_quality) > 0 and as_int(publishable) == 0:
            lines.append("• Кандидаты были, но свежий fallback/detailed отчёт не дал расшифровку reject reasons.")
        else:
            lines.append("• Нет детальных reject reasons в свежих артефактах run.")

    lifecycle = lifecycle_lines(reference, 6)
    if lifecycle:
        lines.append("")
        lines.append("⚠️ Пограничные кандидаты / watchlist")
        lines.extend(lifecycle)

    lines.append("")
    lines.append("📌 Диагноз")
    if as_int(matches_with_offers) <= 0:
        lines.append("• Главный стопор этого run — нет свежих odds-offers. Модель не должна строить прогнозы без актуальной линии.")
    elif as_int(matches_with_offers) >= max(1, as_int(matches_seen) - 3):
        lines.append("• Инвентарь и odds-matching работают: почти все матчи в окне получили офферы.")
    elif as_int(matches_seen) > 0:
        lines.append("• Инвентарь частично просел: мало матчей с офферами относительно окна публикации.")
    if as_int(candidates_before_quality) == 0:
        if as_int(matches_with_offers) <= 0:
            lines.append("• Кандидаты не построились, потому что актуальные odds были отключены или не получены.")
        elif "quarter_total_line_removed" in rejections:
            lines.append("• Главный стопор кандидатов — старый guard quarter totals; он уже заменён на поддержку .25/.75 при 2+ букмекерах.")
        else:
            lines.append("• Кандидаты не построились до quality: надо смотреть market-family guards и supported lines.")
    elif as_int(publishable) == 0:
        lines.append("• Кандидаты строятся, но финальные quality/publication guards их не выпускают — это защита качества, не ошибка API.")
        if not fallback and not detailed:
            lines.append("• Диагностический пробел: для текущего run нет свежего fallback/detailed отчёта с причинами отказа.")
    if as_int(contexts) < as_int(matches_with_offers):
        lines.append("• Контекст покрывает не все матчи с офферами; приоритет — усилить SStats/Bzzoiro/TheSportsDB matching и self-history.")
    if as_int(published) <= 0:
        lines.append("• Прогноз не отправлен, потому что не было publishable-кандидата после guards.")
    else:
        lines.append("• Прогноз опубликован; подробности по ставке ушли отдельным сообщением.")

    lines.append("")
    lines.append("🛠 Исправления по аудиту")
    lines.append("• Отчёт больше не смешивает старый debug/fallback с новым run-summary.")
    lines.append("• Если reject reasons не выгрузились, это показывается как отдельная проблема отчётности.")
    lines.append("• Следующий приоритет: unit-тесты daily top-5/fallback, общий модуль дневной квоты и перенос истории run в SQLite.")
    lines.append("")
    lines.append("⚖️ Дисклеймер: это аналитический отчёт бота, не гарантия результата и не финансовая рекомендация.")

    return "\n".join(lines).strip() + "\n"


def split_messages(text: str, limit: int) -> list[str]:
    text = text.strip()
    if len(text) <= limit:
        return [text]
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0
    for line in text.splitlines():
        extra = len(line) + 1
        if current and current_len + extra > limit:
            chunks.append("\n".join(current).strip())
            current = []
            current_len = 0
        current.append(line)
        current_len += extra
    if current:
        chunks.append("\n".join(current).strip())
    if len(chunks) <= 1:
        return chunks
    total = len(chunks)
    return [f"🧾 Подробный отчёт run — часть {idx}/{total}\n\n{chunk}" for idx, chunk in enumerate(chunks, 1)]


def telegram_send(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")
    try:
        with request.urlopen(url, data=data, timeout=20) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
            return response.status == 200, body[:500]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def main() -> int:
    text = build_report()
    write_text(OUT_TXT, text)
    token = str(os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    chat_id = str(os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    should_send = env_bool("ENHANCED_RUN_REPORT_SEND_TELEGRAM", True)
    chunks = split_messages(text, env_int("TELEGRAM_MESSAGE_SOFT_LIMIT", 3600))
    sent: list[dict[str, Any]] = []
    if should_send and token and chat_id:
        for chunk in chunks:
            ok, preview = telegram_send(token, chat_id, chunk)
            sent.append({"ok": ok, "response_preview": preview})
    else:
        sent.append({"ok": False, "response_preview": "send_disabled_or_missing_telegram_credentials"})
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "text_path": str(OUT_TXT),
        "chunks": len(chunks),
        "sent": sent,
        "status": "sent" if sent and all(item.get("ok") for item in sent) else "not_sent_or_partial",
    }
    write_json(OUT_JSON, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
