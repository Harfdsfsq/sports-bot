from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request
from zoneinfo import ZoneInfo

try:
    from app.services.telegram_i18n import (
        normalize_telegram_text,
        translate_league_name,
        translate_reject_reason,
        translate_selection_text,
        translate_team_name,
    )
except Exception:
    _FALLBACK_REASONS = {
        "canonical_negative_value": "отрицательная контрольная ценность",
        "xg_probability_gap_hard_reject": "слишком большой разрыв между моделью и xG",
        "xg_direction_conflict": "конфликт направления ставки с xG",
        "match_time_outside_window": "слишком мало времени до начала матча",
        "family_not_allowed:spreads": "закрытая семья рынка: форы",
        "family_not_allowed:teamtotals": "закрытая семья рынка: индивидуальные тоталы",
        "family_not_allowed:btts": "закрытая семья рынка: обе забьют",
        "family_not_allowed:h2h": "закрытая семья рынка: исходы 1X2",
        "tier_a_quality_below_min": "качество ниже минимума уровня A",
        "tier_a_proxy_quality_not_allowed": "уровень A не принимает proxy-качество",
        "tier_c_confidence_below_min": "уверенность ниже минимума уровня C",
        "tier_a_confidence_below_min": "уверенность ниже минимума уровня A",
        "tier_a_canonical_edge_below_min": "запас value ниже минимума уровня A",
        "tier_a_canonical_ev_below_min": "EV ниже минимума уровня A",
        "tier_a_odds_above_max": "уровень A: коэффициент выше безопасного максимума",
        "tier_b_odds_above_max": "уровень B: коэффициент выше безопасного максимума",
        "tier_c_odds_above_max": "уровень C: коэффициент выше безопасного максимума",
        "tier_a_odds_below_min": "уровень A: коэффициент ниже минимума",
        "tier_b_odds_below_min": "уровень B: коэффициент ниже минимума",
        "tier_c_odds_below_min": "уровень C: коэффициент ниже минимума",
        "tier_a_books_below_min": "линий букмекеров меньше минимума уровня A",
        "tier_b_books_below_min": "линий букмекеров меньше минимума уровня B",
        "tier_c_books_below_min": "линий букмекеров меньше минимума уровня C",
        "tier_a_sources_below_min": "источников меньше минимума уровня A",
        "tier_b_sources_below_min": "источников меньше минимума уровня B",
        "tier_c_sources_below_min": "источников меньше минимума уровня C",
        "tier_a_xg_gap_above_max": "уровень A: разрыв с xG выше лимита",
        "tier_b_xg_gap_above_max": "уровень B: разрыв с xG выше лимита",
        "tier_c_xg_gap_above_max": "уровень C: разрыв с xG выше лимита",
        "tier_a_xg_confirmation_missing": "уровень A: нет подтверждения xG",
        "tier_b_xg_confirmation_missing": "уровень B: нет подтверждения xG",
        "tier_c_xg_confirmation_missing": "уровень C: нет подтверждения xG",
        "tier_a_market_confirmation_missing": "уровень A: нет рыночного подтверждения",
        "tier_b_market_confirmation_missing": "уровень B: нет рыночного подтверждения",
        "tier_c_market_confirmation_missing": "уровень C: нет рыночного подтверждения",
        "tier_b_canonical_edge_below_min": "запас value ниже минимума уровня B",
        "tier_b_canonical_ev_below_min": "EV ниже минимума уровня B",
        "tier_c_canonical_edge_below_min": "запас value ниже минимума уровня C",
        "tier_c_canonical_ev_below_min": "EV ниже минимума уровня C",
    }

    _TEAM_FALLBACK = {
        "AC Milan": "Милан",
        "Juventus Turin": "Ювентус",
        "Club Santos Laguna": "Сантос Лагуна",
        "CF Monterrey": "Монтеррей",
        "Llaneros FC": "Льянерос",
        "Alianza FC Valledupar": "Альянса Вальедупар",
        "Tacoma Defiance": "Такома Дифайенс",
        "Los Angeles FC 2": "Лос-Анджелес 2",
        "Tepatitlan FC": "Тепатитлан",
        "Atlante FC": "Атланте",
        "Los Angeles Galaxy": "Лос-Анджелес Гэлакси",
        "Real Salt Lake": "Реал Солт-Лейк",
        "Atletico Tucuman": "Атлетико Тукуман",
        "CA Banfield": "Банфилд",
        "Oriente Petrolero": "Ориенте Петролеро",
        "Real Potosi": "Реал Потоси",
        "Christchurch United FC": "Крайстчерч Юнайтед",
        "Christchurch United": "Крайстчерч Юнайтед",
        "Northern AFC": "Нортерн",
        "Atletico Mineiro MG": "Атлетико Минейро",
        "CR Flamengo RJ": "Фламенго",
    }

    def _fallback_translit(value: Any) -> str:
        text = str(value or "")
        table = str.maketrans({
            "a":"а","b":"б","c":"к","d":"д","e":"е","f":"ф","g":"г","h":"х","i":"и","j":"дж","k":"к","l":"л","m":"м",
            "n":"н","o":"о","p":"п","q":"к","r":"р","s":"с","t":"т","u":"у","v":"в","w":"у","x":"кс","y":"и","z":"з",
            "A":"А","B":"Б","C":"К","D":"Д","E":"Е","F":"Ф","G":"Г","H":"Х","I":"И","J":"Дж","K":"К","L":"Л","M":"М",
            "N":"Н","O":"О","P":"П","Q":"К","R":"Р","S":"С","T":"Т","U":"У","V":"В","W":"У","X":"Кс","Y":"И","Z":"З",
        })
        return text.translate(table)

    def normalize_telegram_text(text: Any) -> str: return str(text or "")
    def translate_league_name(name: Any) -> str:
        text = str(name or "")
        for en, ru in {"Mexico": "Мексика", "USA": "США", "Italy": "Италия", "Spain": "Испания"}.items():
            text = text.replace(en, ru)
        return text
    def translate_reject_reason(reason: Any) -> str:
        text = str(reason or "")
        if text in _FALLBACK_REASONS:
            return _FALLBACK_REASONS[text]
        if text.startswith("family_not_allowed:"):
            return "закрытая семья рынка: " + text.split(":", 1)[1]
        return text.replace("_", " ")
    def translate_selection_text(selection: Any, home_team: Any = "", away_team: Any = "") -> str:
        text = str(selection or "")
        text = text.replace("Over", "Больше").replace("Under", "Меньше")
        if home_team:
            text = text.replace(str(home_team), translate_team_name(home_team))
        if away_team:
            text = text.replace(str(away_team), translate_team_name(away_team))
        return text
    def translate_team_name(name: Any) -> str:
        text = str(name or "")
        return _TEAM_FALLBACK.get(text, _fallback_translit(text))

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
OUT_JSON = EXPORT_DIR / "latest-detailed-run-report.json"
OUT_TXT = EXPORT_DIR / "latest-detailed-run-report.txt"
SENT_STATE = Path(".data/detailed-run-report-sent.json")


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    try:
        return int(float(str(raw).strip())) if raw not in (None, "") else default
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def load_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def parse_dt(value: Any):
    if value in (None, ""):
        return None
    try:
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def app_tz():
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return UTC


def fmt_time(value: Any) -> str:
    dt = parse_dt(value)
    if dt is None:
        return "н/д"
    return dt.astimezone(app_tz()).strftime("%d.%m.%Y %H:%M MSK")


def latest_existing(paths: list[str | Path]) -> dict[str, Any]:
    for path in paths:
        payload = load_json(path, None)
        if isinstance(payload, dict):
            return payload
    return {}


def fallback_report() -> dict[str, Any]:
    return latest_existing([
        "artifacts/controlled-fallback-report.json",
        ".data/exports/latest-controlled-fallback-report.json",
    ])


def debug_last_run() -> dict[str, Any]:
    return load_json(".logs/debug-last-run.json", {})


def quota_report() -> dict[str, Any]:
    return load_json(".data/exports/latest-provider-quota-governor.json", {})


def extract_evaluated(report: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("evaluated", "candidates", "checked_candidates", "rejected_candidates"):
        rows = report.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
    return []


def unwrap_candidate(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    candidate = row.get("candidate") if isinstance(row.get("candidate"), dict) else row
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    if not metrics:
        metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}

    reasons = (
        row.get("reject_reasons")
        or row.get("reasons")
        or row.get("hard_reject_reasons")
        or candidate.get("reject_reasons")
        or candidate.get("reasons")
        or []
    )
    if isinstance(reasons, str):
        reasons = [reasons]
    reasons = [str(item) for item in reasons if str(item).strip()]
    return candidate, metrics, reasons


def metric(candidate: dict[str, Any], metrics: dict[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        if key in metrics:
            return as_float(metrics.get(key), default)
        if key in candidate:
            return as_float(candidate.get(key), default)
    return default


def candidate_identity(candidate: dict[str, Any]) -> dict[str, str]:
    home = translate_team_name(candidate.get("home_team") or candidate.get("home") or "")
    away = translate_team_name(candidate.get("away_team") or candidate.get("away") or "")
    league = translate_league_name(candidate.get("league") or candidate.get("competition") or candidate.get("tournament") or "")
    selection = translate_selection_text(candidate.get("selection") or candidate.get("market") or "", candidate.get("home_team"), candidate.get("away_team"))
    return {"home": home, "away": away, "league": league, "selection": selection}


def reason_counter(report: dict[str, Any], evaluated: list[dict[str, Any]]) -> Counter:
    counter = Counter()
    for key in ("reject_reasons", "reason_counts", "rejection_reasons"):
        raw = report.get(key)
        if isinstance(raw, dict):
            for reason, count in raw.items():
                counter[str(reason)] += as_int(count)
    if not counter:
        for row in evaluated:
            _, _, reasons = unwrap_candidate(row)
            counter.update(reasons)
    return counter


def is_positive_value(candidate: dict[str, Any], metrics: dict[str, Any]) -> bool:
    ev = metric(candidate, metrics, "canonical_ev_pct", "ev_pct")
    edge = metric(candidate, metrics, "canonical_edge_pp", "edge_pp")
    return ev > 0 or edge > 0


def near_miss_score(candidate: dict[str, Any], metrics: dict[str, Any], reasons: list[str]) -> tuple:
    ev = metric(candidate, metrics, "canonical_ev_pct", "ev_pct")
    edge = metric(candidate, metrics, "canonical_edge_pp", "edge_pp")
    confidence = metric(candidate, metrics, "confidence")
    quality = metric(candidate, metrics, "quality_score")
    hard_penalty = sum(1 for reason in reasons if reason in {
        "canonical_negative_value",
        "match_time_outside_window",
        "match_already_started",
        "xg_probability_gap_hard_reject",
        "xg_direction_conflict",
        "btts_probability_gap_hard_reject",
        "btts_direction_conflict",
    } or reason.startswith("family_not_allowed:"))
    return (hard_penalty == 0, ev, edge, confidence, quality, -len(reasons))


def pick_near_misses(evaluated: list[dict[str, Any]], limit: int) -> list[dict[str, Any]]:
    rows = []
    seen = set()
    for row in evaluated:
        candidate, metrics, reasons = unwrap_candidate(row)
        ident = candidate_identity(candidate)
        key = (
            ident["home"],
            ident["away"],
            ident["selection"],
            str(candidate.get("point") or ""),
            str(candidate.get("odds") or ""),
        )
        if key in seen:
            continue
        seen.add(key)

        # Borderline candidates should have at least some value or have failed only by tier thresholds.
        tierish = any("tier_" in reason or "proxy_single_source" in reason for reason in reasons)
        if not is_positive_value(candidate, metrics) and not tierish:
            continue

        rows.append({
            "candidate": candidate,
            "metrics": metrics,
            "reasons": reasons,
            "score": near_miss_score(candidate, metrics, reasons),
        })
    rows.sort(key=lambda item: item["score"], reverse=True)
    return rows[:limit]


def explain_thresholds(candidate: dict[str, Any], metrics: dict[str, Any], reasons: list[str]) -> list[str]:
    out: list[str] = []
    ev = metric(candidate, metrics, "canonical_ev_pct", "ev_pct")
    edge = metric(candidate, metrics, "canonical_edge_pp", "edge_pp")
    confidence = metric(candidate, metrics, "confidence")
    quality = metric(candidate, metrics, "quality_score")
    books = as_int(metrics.get("books_count", candidate.get("books_count", 0)))
    sources = as_int(metrics.get("sources_count", candidate.get("sources_count", 0)))

    if ev > 0:
        out.append(f"EV {ev:+.1f}%")
    if edge > 0:
        out.append(f"запас {edge:+.1f} п.п.")
    if confidence > 0:
        out.append(f"уверенность {confidence:.1f}%")
    if quality > 0:
        out.append(f"качество {quality:.1f}")
    if books or sources:
        out.append(f"линии {books}, источники {sources}")

    translated_reasons = [translate_reject_reason(reason) for reason in reasons[:4]]
    if translated_reasons:
        out.append("не прошло: " + "; ".join(translated_reasons))
    return out


def provider_summary() -> list[str]:
    payload = quota_report()
    providers = payload.get("providers") if isinstance(payload, dict) else []
    if not isinstance(providers, list):
        return []
    important = {"odds_api_io", "bzzoiro", "sstats", "api_football", "football_data", "thesportsdb", "futrixmetrics", "weather"}
    lines = []
    for row in providers:
        if not isinstance(row, dict):
            continue
        provider = str(row.get("provider") or "")
        if provider not in important:
            continue
        grant = as_int(row.get("granted"))
        after = row.get("tokens_after")
        skip = row.get("skip_reason")
        tail = f", пропуск: {skip}" if skip else ""
        lines.append(f"• {provider}: grant {grant}, остаток {after}{tail}")
    return lines



def learning_lines() -> list[str]:
    payload = load_json(".data/exports/latest-auto-learning-report.json", {})
    if not isinstance(payload, dict) or not payload:
        return []
    overall = payload.get("overall") if isinstance(payload.get("overall"), dict) else {}
    overrides = payload.get("runtime_overrides") if isinstance(payload.get("runtime_overrides"), dict) else {}
    n = as_int(overall.get("n"))
    min_total = 30
    try:
        pol = load_json("config/auto_learning_policy.json", {})
        if isinstance(pol, dict):
            min_total = as_int(pol.get("min_settled_total"), 30)
    except Exception:
        pass
    mode = str(overrides.get("AUTO_LEARNING_MODE") or "unknown")
    ready = str(overrides.get("AUTO_LEARNING_SAMPLE_READY") or "false")
    lines = [
        "🧠 Автообучение",
        f"• Выборка: {n}/{min_total} закрытых ставок | sample_ready={ready}",
        f"• Режим: {mode}",
    ]
    if n < min_total:
        lines.append("• Фильтры не менялись: идёт накопление статистики.")
    else:
        roi = as_float(overall.get("roi")) * 100.0
        bias = as_float(overall.get("calibration_bias_pp"))
        lines.append(f"• ROI {roi:+.1f}% | bias {bias:+.1f} п.п.")
    lines.append("")
    return lines

def build_payload() -> dict[str, Any]:
    report = fallback_report()
    debug = debug_last_run()
    summary = debug.get("summary") if isinstance(debug.get("summary"), dict) else {}
    evaluated = extract_evaluated(report)
    reasons = reason_counter(report, evaluated)
    near = pick_near_misses(evaluated, env_int("DETAILED_RUN_REPORT_TOP_NEAR_MISSES", 8))

    published = bool(report.get("published") or report.get("telegram_sent") or report.get("selected_count"))
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "published": published,
        "status": report.get("status") or ("published" if published else "no_pick"),
        "summary": summary,
        "candidate_counts": {
            "evaluated": len(evaluated),
            "rescue_checked": as_int(report.get("rescue_candidates_checked") or report.get("checked") or len(evaluated)),
            "selected_count": as_int(report.get("selected_count")),
        },
        "reason_counts": dict(reasons.most_common(20)),
        "near_misses": near,
        "provider_lines": provider_summary(),
    }


def render(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    counts = payload.get("candidate_counts") or {}
    reasons = Counter(payload.get("reason_counts") or {})
    near = payload.get("near_misses") or []

    lines = []
    title = "🧾 Подробный отчёт run"
    if payload.get("published"):
        title += " — прогноз опубликован"
    else:
        title += " — прогнозов нет"
    lines.append(title)
    lines.append("")

    lines.append("⚙️ Что сделал скрипт")
    lines.append(f"• Матчи: {as_int(summary.get('matches_seen'))} | с линиями: {as_int(summary.get('matches_with_offers'))} | контекстов: {as_int(summary.get('contexts_built'))}")
    lines.append(f"• Кандидаты: raw {as_int(summary.get('candidates_raw'))} | до качества {as_int(summary.get('candidates_before_quality'))} | publishable {as_int(summary.get('candidates_publishable'))}")
    lines.append(f"• Резерв проверил: {counts.get('rescue_checked', 0)} | оценено в отчёте: {counts.get('evaluated', 0)} | выбрано: {counts.get('selected_count', 0)}")
    lines.append("")

    if reasons:
        total = sum(reasons.values())
        lines.append("🚫 Почему не прошли")
        for reason, count in reasons.most_common(10):
            pct = count / total * 100.0 if total else 0.0
            lines.append(f"• {translate_reject_reason(reason)} — {count} ({pct:.0f}%)")
        lines.append("")

    if near:
        lines.append("⚠️ Пограничные кандидаты")
        for idx, item in enumerate(near[: env_int("DETAILED_RUN_REPORT_TOP_NEAR_MISSES", 8)], start=1):
            candidate = item["candidate"]
            metrics = item["metrics"]
            ident = candidate_identity(candidate)
            odds = metric(candidate, metrics, "odds", default=as_float(candidate.get("odds")))
            kickoff = fmt_time(candidate.get("commence_time") or candidate.get("start_time") or candidate.get("kickoff"))
            match = f"{ident['home']} — {ident['away']}".strip(" —")
            lines.append(f"{idx}. {match}")
            if ident["league"]:
                lines.append(f"   🏆 {ident['league']}")
            lines.append(f"   🎯 {ident['selection']} @{odds:.2f} | 🕒 {kickoff}")
            for part in explain_thresholds(candidate, metrics, item["reasons"]):
                lines.append(f"   • {part}")
        lines.append("")
    else:
        lines.append("⚠️ Пограничные кандидаты: не найдено ставок с положительным EV/edge после жёстких guard’ов.")
        lines.append("")

    lines.extend(learning_lines())

    provider_lines = payload.get("provider_lines") or []
    if provider_lines:
        lines.append("🔌 API / квоты последнего run")
        lines.extend(provider_lines[:10])
        lines.append("")

    # Operational conclusion.
    if reasons:
        top_reason = reasons.most_common(1)[0][0]
        lines.append("📌 Вывод")
        if top_reason == "canonical_negative_value":
            lines.append("• Главный фильтр — отрицательная контрольная ценность. Скрипт видел матчи, но рынок не дал достаточного value.")
        elif top_reason == "match_time_outside_window":
            lines.append("• Главный фильтр — время. Кандидаты были слишком близко к началу матча или вне окна публикации.")
        elif top_reason.startswith("family_not_allowed:"):
            lines.append("• Главный фильтр — закрытые семьи рынков. Они остаются в watchlist, но не публикуются в Telegram без отдельного safe-tier.")
        elif "xg" in top_reason:
            lines.append("• Главный фильтр — конфликт с xG. Модельный value не подтвердился базовой xG-проверкой.")
        else:
            lines.append("• Прогнозов нет из-за комбинации value, xG, качества и ограничений семейств рынков.")
    return normalize_telegram_text("\n".join(lines))



def semantic_message_hash(payload: dict[str, Any]) -> str:
    """Stable hash for cooldown/dedupe that ignores tiny token/quota/count drift."""
    reasons = payload.get("reason_counts") or {}
    top_reasons = []
    if isinstance(reasons, dict):
        top_reasons = [str(k) for k, _ in Counter(reasons).most_common(5)]
    near = []
    for item in (payload.get("near_misses") or [])[:5]:
        try:
            candidate = item["candidate"]
            ident = candidate_identity(candidate)
            near.append([ident.get("home"), ident.get("away"), ident.get("selection"), str(candidate.get("odds") or "")])
        except Exception:
            continue
    raw = {
        "published": bool(payload.get("published")),
        "status": payload.get("status"),
        "top_reasons": top_reasons,
        "near": near,
    }
    return hashlib.sha1(json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def split_text(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, current, size = [], [], 0
    for line in text.splitlines():
        add = len(line) + 1
        if current and size + add > limit:
            parts.append("\n".join(current))
            current, size = [], 0
        current.append(line)
        size += add
    if current:
        parts.append("\n".join(current))

    if len(parts) > 1:
        total = len(parts)
        parts = [f"🧾 Подробный отчёт run — часть {idx}/{total}\n\n{part}" for idx, part in enumerate(parts, start=1)]
    return parts



def send_telegram(text: str) -> dict[str, Any]:
    token = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return {"sent": False, "reason": "missing_credentials"}

    h = hashlib.sha1(text.encode("utf-8")).hexdigest()
    state = load_json(SENT_STATE, {})
    if not isinstance(state, dict):
        state = {}

    if state.get("last_hash") == h and not env_bool("DETAILED_RUN_REPORT_FORCE_SEND", False):
        return {"sent": False, "reason": "unchanged", "hash": h}

    cooldown = env_int("DETAILED_RUN_REPORT_MIN_INTERVAL_MINUTES", 20)
    sent_at_raw = state.get("sent_at")
    if sent_at_raw and cooldown > 0 and not env_bool("DETAILED_RUN_REPORT_FORCE_SEND", False):
        try:
            sent_at = datetime.fromisoformat(str(sent_at_raw).replace("Z", "+00:00"))
            if sent_at.tzinfo is None:
                sent_at = sent_at.replace(tzinfo=UTC)
            age_minutes = (datetime.now(UTC) - sent_at.astimezone(UTC)).total_seconds() / 60.0
            if age_minutes < cooldown:
                return {
                    "sent": False,
                    "reason": "cooldown_active",
                    "age_minutes": round(age_minutes, 1),
                    "cooldown_minutes": cooldown,
                    "hash": h,
                }
        except Exception:
            pass

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    parts = split_text(text)
    try:
        for part in parts:
            data = parse.urlencode({
                "chat_id": chat_id,
                "text": part,
                "disable_web_page_preview": "true",
            }).encode("utf-8")
            req = request.Request(url, data=data, method="POST")
            with request.urlopen(req, timeout=20) as response:
                response.read()
    except Exception as exc:
        return {"sent": False, "reason": "telegram_send_error", "error": repr(exc), "hash": h}

    state["last_hash"] = h
    state["sent_at"] = datetime.now(UTC).isoformat()
    state["parts"] = len(parts)
    write_json(SENT_STATE, state)
    return {"sent": True, "parts": len(parts), "hash": h}


def main() -> int:
    payload = build_payload()
    text = render(payload)
    payload["text"] = text

    should_send = env_bool("DETAILED_RUN_REPORT_SEND_TELEGRAM", False)
    # By default send detailed report only when no forecast was published.
    if payload.get("published") and not env_bool("DETAILED_RUN_REPORT_SEND_WHEN_PUBLISHED", False):
        should_send = False

    if should_send:
        try:
            payload["telegram"] = send_telegram(text)
        except Exception as exc:
            payload["telegram"] = {
                "sent": False,
                "reason": "telegram_send_error",
                "error": str(exc),
            }
            print(f"Telegram send failed: {exc}")
    else:
        payload["telegram"] = {"sent": False, "reason": "disabled_or_published"}

    write_json(OUT_JSON, payload)
    write_text(OUT_TXT, text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
