from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request
from zoneinfo import ZoneInfo

try:
    from app.services.telegram_i18n import (
        normalize_telegram_text,
        translate_league_name,
        translate_selection_text,
        translate_team_name,
    )
except Exception:
    def normalize_telegram_text(text: Any) -> str: return str(text or "")
    def translate_league_name(name: Any) -> str: return str(name or "")
    def translate_selection_text(selection: Any, home_team: Any = "", away_team: Any = "") -> str: return str(selection or "")
    def translate_team_name(name: Any) -> str: return str(name or "")

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
OUT_JSON = EXPORT_DIR / "latest-daily-ops-report.json"
OUT_TXT = EXPORT_DIR / "latest-daily-ops-report.txt"
SENT_STATE = Path(".data/daily-ops-report-sent.json")


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


def safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(value)) if value not in (None, "") else default
    except Exception:
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except Exception:
        return default


def app_tz() -> ZoneInfo | timezone:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return UTC


def parse_dt(value: Any) -> datetime | None:
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


def local_date(value: Any, tz: ZoneInfo | timezone) -> str:
    dt = parse_dt(value)
    return dt.astimezone(tz).date().isoformat() if dt else ""


def target_report_date() -> str:
    tz = app_tz()
    offset = max(0, env_int("DAILY_REPORT_TARGET_OFFSET_DAYS", 0))
    local_now = datetime.now(UTC).astimezone(tz)
    # Football-day guard: delayed evening reports that start just after midnight still close previous day.
    if env_bool("DAILY_OPS_FOOTBALL_DAY_GUARD", True) and offset == 0 and local_now.hour < env_int("DAILY_OPS_ROLLOVER_HOUR_LOCAL", 4):
        offset = 1
    return (local_now.date() - timedelta(days=offset)).isoformat()


def collect_runs(report_date: str) -> list[dict[str, Any]]:
    tz = app_tz()
    roots = [Path(".logs/runs"), Path(".data/history/runs")]
    seen: set[str] = set()
    runs: list[dict[str, Any]] = []
    for root in roots:
        if not root.exists():
            continue
        for path in sorted(root.glob("*/*-run.json")):
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            payload = load_json(path, None)
            if not isinstance(payload, dict):
                continue
            summary = dict(payload.get("summary") or {})
            dt_value = payload.get("created_at") or summary.get("current_time_utc") or summary.get("started_time_utc") or summary.get("current_time_local")
            if local_date(dt_value, tz) == report_date:
                payload["_archive_path"] = key
                runs.append(payload)
    runs.sort(key=lambda item: str(item.get("created_at") or ""))
    return runs


def run_totals(runs: list[dict[str, Any]]) -> dict[str, Any]:
    totals = Counter()
    for payload in runs:
        summary = dict(payload.get("summary") or {})
        totals["runs"] += 1
        totals["matches_seen"] += safe_int(summary.get("matches_seen"))
        totals["matches_with_offers"] += safe_int(summary.get("matches_with_offers"))
        totals["contexts_built"] += safe_int(summary.get("contexts_built"))
        totals["candidates_raw"] += safe_int(summary.get("candidates_raw"))
        totals["candidates_before_quality"] += safe_int(summary.get("candidates_before_quality"))
        totals["candidates_publishable"] += safe_int(summary.get("candidates_publishable"))
        totals["published"] += safe_int(summary.get("published") or summary.get("published_to_telegram"))
        totals["telegram_messages_sent"] += safe_int(summary.get("telegram_messages_sent"))
        status = str(summary.get("run_status") or summary.get("status") or "ok").lower()
        if status in {"error", "failed"}:
            totals["errors"] += 1
    return dict(totals)


def tracked_bets() -> list[dict[str, Any]]:
    state = load_json(".data/state.json", {})
    if not isinstance(state, dict):
        return []
    raw = []
    for key in ("bets", "published_candidates"):
        for item in state.get(key) or []:
            if isinstance(item, dict):
                raw.append(dict(item))
    dedup = {}
    for item in raw:
        key = str(item.get("fingerprint") or item.get("prediction_id") or "")
        if not key:
            key = hashlib.sha1(json.dumps(item, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        dedup[key] = item
    return list(dedup.values())


def settlement_date(bet: dict[str, Any], tz: ZoneInfo | timezone) -> str:
    settlement = bet.get("settlement") if isinstance(bet.get("settlement"), dict) else {}
    for key in ("settled_at", "checked_at", "updated_at", "completed_at"):
        date = local_date((settlement or {}).get(key) or bet.get(key), tz)
        if date:
            return date
    return ""


def bet_date(bet: dict[str, Any], tz: ZoneInfo | timezone) -> str:
    for key in ("published_at", "created_at", "commence_time", "start_time", "kickoff"):
        date = local_date(bet.get(key), tz)
        if date:
            return date
    return ""


def bet_line(bet: dict[str, Any]) -> str:
    home = translate_team_name(bet.get("home_team") or bet.get("home") or "")
    away = translate_team_name(bet.get("away_team") or bet.get("away") or "")
    match = f"{home} — {away}".strip(" —") or "матч"
    selection = translate_selection_text(bet.get("selection") or bet.get("market") or "", bet.get("home_team"), bet.get("away_team"))
    odds = bet.get("odds")
    status = str(bet.get("status") or "").strip()
    status_ru = {
        "pending": "ожидает расчёта",
        "generated": "сгенерирован",
        "won": "выигрыш",
        "lost": "проигрыш",
        "push": "возврат",
        "void": "отмена",
        "half_won": "половина выигрыша",
        "half_lost": "половина проигрыша",
    }.get(status, status or "н/д")
    stake = safe_float(bet.get("stake_amount"))
    return f"{match}: {selection} @{odds} | {status_ru} | ставка {stake:.2f}"


def summarize_bets(report_date: str) -> dict[str, Any]:
    tz = app_tz()
    bets = tracked_bets()
    published, settled, pending_today, old_pending = [], [], [], []
    counters = Counter()
    pnl = 0.0
    stake = 0.0

    for bet in bets:
        status = str(bet.get("status") or "").lower()
        bdate = bet_date(bet, tz)
        if bdate == report_date:
            published.append(bet)
        if settlement_date(bet, tz) == report_date:
            settled.append(bet)
            settlement = bet.get("settlement") if isinstance(bet.get("settlement"), dict) else {}
            outcome = str((settlement or {}).get("outcome") or status).lower()
            counters[outcome] += 1
            pnl += safe_float((settlement or {}).get("pnl"))
            stake += safe_float(bet.get("stake_amount"))
        if status in {"pending", "generated"}:
            if bdate == report_date:
                pending_today.append(bet)
            elif bdate and bdate < report_date:
                old_pending.append(bet)

    state = load_json(".data/state.json", {})
    bankroll = state.get("bankroll") if isinstance(state, dict) and isinstance(state.get("bankroll"), dict) else {}
    current_balance = safe_float(bankroll.get("current_balance"))
    open_exposure = safe_float(bankroll.get("open_exposure"))
    available = bankroll.get("available_balance")
    if available in (None, ""):
        available = max(0.0, current_balance - open_exposure)

    return {
        "counts": {
            "published_today": len(published),
            "settled_today": len(settled),
            "pending_today": len(pending_today),
            "old_pending": len(old_pending),
            "won": counters.get("won", 0) + counters.get("half_won", 0),
            "lost": counters.get("lost", 0) + counters.get("half_lost", 0),
            "push": counters.get("push", 0),
            "void": counters.get("void", 0),
        },
        "settled_stake": round(stake, 2),
        "settled_pnl": round(pnl, 2),
        "open_exposure": round(open_exposure, 2),
        "bankroll": {
            **bankroll,
            "current_balance": round(current_balance, 2),
            "open_exposure": round(open_exposure, 2),
            "available_balance": round(float(available), 2),
        },
        "published_today": [bet_line(item) for item in published[:10]],
        "settled_today": [bet_line(item) for item in settled[:10]],
        "pending_today": [bet_line(item) for item in pending_today[:10]],
        "old_pending": [bet_line(item) for item in old_pending[:6]],
    }


def quota_rows() -> list[str]:
    payload = load_json(EXPORT_DIR / "latest-provider-request-budget.json", {})
    if not payload:
        payload = load_json(EXPORT_DIR / "latest-provider-quota-governor.json", {})
    decisions = payload.get("decisions") if isinstance(payload, dict) else []
    important = {"odds_api_io", "bzzoiro", "sstats", "api_football", "football_data", "thesportsdb", "futrixmetrics", "weatherapi", "openweathermap", "oddspapi", "sportsbook_api", "meteostat", "oddsfeed", "freeapilivefootball", "sportapi"}
    if isinstance(decisions, list) and decisions:
        out = []
        for row in decisions:
            if not isinstance(row, dict):
                continue
            provider = str(row.get("provider") or "")
            if provider not in important:
                continue
            grant = safe_int(row.get("grant"))
            reason = str(row.get("reason") or "unknown")
            daily_budget = safe_int(row.get("daily_budget"))
            daily_remaining_after = row.get("daily_remaining_after")
            monthly_budget = safe_int(row.get("monthly_budget"))
            monthly_remaining_after = row.get("monthly_remaining_after")
            parts = [f"grant {grant}", f"reason {reason}"]
            if daily_budget > 0:
                used = daily_budget - safe_int(daily_remaining_after, daily_budget) if daily_remaining_after is not None else safe_int(row.get("daily_used_before"))
                parts.append(f"day {used}/{daily_budget}")
            if monthly_budget > 0:
                used = monthly_budget - safe_int(monthly_remaining_after, monthly_budget) if monthly_remaining_after is not None else safe_int(row.get("monthly_used_before"))
                parts.append(f"month {used}/{monthly_budget}")
            out.append(f"• {provider}: " + ", ".join(parts))
        return out

    rows = payload.get("providers") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        provider = str(row.get("provider") or "")
        if provider not in important:
            continue
        grant = safe_int(row.get("granted"))
        after = row.get("tokens_after")
        skip = row.get("skip_reason")
        tail = f", пропуск: {skip}" if skip else ""
        out.append(f"• {provider}: grant {grant}, остаток {after}{tail}")
    return out



def learning_summary() -> dict[str, Any]:
    payload = load_json(EXPORT_DIR / "latest-auto-learning-report.json", {})
    if not isinstance(payload, dict):
        return {}
    overall = payload.get("overall") if isinstance(payload.get("overall"), dict) else {}
    overrides = payload.get("runtime_overrides") if isinstance(payload.get("runtime_overrides"), dict) else {}
    recommendations = payload.get("recommendations") if isinstance(payload.get("recommendations"), list) else []
    return {
        "enabled": bool(payload.get("enabled", True)),
        "n": safe_int(overall.get("n")),
        "roi": safe_float(overall.get("roi")),
        "calibration_bias_pp": safe_float(overall.get("calibration_bias_pp")),
        "sample_ready": str(overrides.get("AUTO_LEARNING_SAMPLE_READY") or "false"),
        "mode": str(overrides.get("AUTO_LEARNING_MODE") or "unknown"),
        "recommendations": [str(item.get("message")) for item in recommendations[:3] if isinstance(item, dict) and item.get("message")],
    }


def build(report_date: str) -> dict[str, Any]:
    runs = collect_runs(report_date)
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "report_date": report_date,
        "timezone": str(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"),
        "runs": run_totals(runs),
        "runs_count": len(runs),
        "bets": summarize_bets(report_date),
        "quota_lines": quota_rows(),
        "learning": learning_summary(),
    }


def fmt(value: Any) -> str:
    return f"{safe_float(value):.2f}"


def render(payload: dict[str, Any]) -> str:
    totals = Counter(payload.get("runs") or {})
    bets = payload.get("bets") or {}
    counts = bets.get("counts") or {}
    bankroll = bets.get("bankroll") or {}

    lines = [
        f"📊 Дневной отчёт работы бота — {payload.get('report_date')}",
        "",
        "🧠 Работа скрипта",
        f"• Run’ов: {payload.get('runs_count', 0)} | ошибок: {totals.get('errors', 0)} | Telegram-сообщений: {totals.get('telegram_messages_sent', 0)}",
        f"• Матчи: {totals.get('matches_seen', 0)} | с линиями: {totals.get('matches_with_offers', 0)} | контекстов: {totals.get('contexts_built', 0)}",
        f"• Кандидаты: raw {totals.get('candidates_raw', 0)} | до качества {totals.get('candidates_before_quality', 0)} | publishable {totals.get('candidates_publishable', 0)}",
        f"• Опубликовано прогнозов: {totals.get('published', 0)}",
        "",
        "💼 Банк и settlement",
        f"• Банк: {fmt(bankroll.get('current_balance'))} | открытый риск: {fmt(bankroll.get('open_exposure'))} | доступно: {fmt(bankroll.get('available_balance'))}",
        f"• За дату: опубликовано {counts.get('published_today', 0)} | закрыто {counts.get('settled_today', 0)} | pending сегодня {counts.get('pending_today', 0)} | старые pending {counts.get('old_pending', 0)}",
        f"• Итоги закрытых: W {counts.get('won', 0)} / L {counts.get('lost', 0)} / Push {counts.get('push', 0)} / Void {counts.get('void', 0)} | PnL {fmt(bets.get('settled_pnl'))}",
    ]

    for title, key, icon in [
        ("Прогнозы дня", "published_today", "🎯"),
        ("Закрыто сегодня", "settled_today", "✅"),
        ("Открыто сегодня", "pending_today", "⏳"),
        ("Старые pending", "old_pending", "🧾"),
    ]:
        rows = bets.get(key) or []
        if rows:
            lines += ["", f"{icon} {title}"]
            lines += [f"• {item}" for item in rows[:6]]

    quota = payload.get("quota_lines") or []
    if quota:
        lines += ["", "🔌 API / квоты последнего run"]
        lines.extend(quota[:10])


    learning = payload.get("learning") or {}
    if learning:
        lines += ["", "🧠 Автообучение"]
        lines.append(
            f"• Закрытых ставок в обучении: {learning.get('n', 0)} | ROI {safe_float(learning.get('roi')) * 100:+.1f}% | bias {safe_float(learning.get('calibration_bias_pp')):+.1f} п.п."
        )
        lines.append(f"• sample_ready={learning.get('sample_ready')} | mode={learning.get('mode')}")
        for msg in learning.get("recommendations") or []:
            lines.append(f"• {msg}")

    lines += ["", "📝 Settlement запускается перед отчётом. Если матч ещё не закрыт, он остаётся pending и проверяется следующим вечерним/ночным run."]
    return normalize_telegram_text("\n".join(lines))


def split_text(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts, cur, size = [], [], 0
    for line in text.splitlines():
        add = len(line) + 1
        if cur and size + add > limit:
            parts.append("\n".join(cur))
            cur, size = [], 0
        cur.append(line)
        size += add
    if cur:
        parts.append("\n".join(cur))
    return parts


def send_telegram(text: str, report_date: str) -> dict[str, Any]:
    token = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return {"sent": False, "reason": "missing_telegram_credentials"}

    state = load_json(SENT_STATE, {})
    if not isinstance(state, dict):
        state = {}
    h = hashlib.sha1(text.encode("utf-8")).hexdigest()
    previous = state.get(report_date) if isinstance(state.get(report_date), dict) else {}
    if previous.get("hash") == h and not env_bool("DAILY_OPS_REPORT_FORCE_SEND", False):
        return {"sent": False, "reason": "unchanged", "hash": h}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    sent = 0
    for part in split_text(text):
        data = parse.urlencode({"chat_id": chat_id, "text": part, "disable_web_page_preview": "true"}).encode("utf-8")
        req = request.Request(url, data=data, method="POST")
        with request.urlopen(req, timeout=20) as response:
            response.read()
        sent += 1

    state[report_date] = {"sent_at": datetime.now(UTC).isoformat(), "hash": h, "parts": sent}
    write_json(SENT_STATE, state)
    return {"sent": True, "parts": sent, "hash": h}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="")
    parser.add_argument("--send-telegram", action="store_true")
    args = parser.parse_args()

    report_date = args.date.strip() or target_report_date()
    payload = build(report_date)
    text = render(payload)
    payload["text"] = text
    if args.send_telegram:
        try:
            payload["telegram"] = send_telegram(text, report_date)
        except Exception as exc:
            payload["telegram"] = {
                "sent": False,
                "reason": "telegram_send_error",
                "error": str(exc),
            }
            print(f"Telegram send failed: {exc}")
    write_json(OUT_JSON, payload)
    write_text(OUT_TXT, text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
