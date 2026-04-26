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


def app_tz() -> ZoneInfo | timezone:
    name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"
    try:
        return ZoneInfo(str(name))
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
    return (datetime.now(UTC).astimezone(tz).date() - timedelta(days=offset)).isoformat()


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
            dt_value = (
                payload.get("created_at")
                or summary.get("current_time_utc")
                or summary.get("started_time_utc")
                or summary.get("current_time_local")
            )
            if local_date(dt_value, tz) == report_date:
                payload["_archive_path"] = key
                runs.append(payload)
    runs.sort(key=lambda item: str(item.get("created_at") or ""))
    return runs


def run_totals(runs: list[dict[str, Any]]) -> dict[str, Any]:
    totals = Counter()
    providers = {}
    provider_errors = Counter()
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
        source_stats = dict(summary.get("source_stats") or payload.get("source_stats") or {})
        for name, row in source_stats.items():
            if isinstance(row, dict):
                providers[str(name)] = row
                if row.get("runtime_error") or row.get("error"):
                    provider_errors[str(name)] += 1
                if row.get("rate_limited"):
                    provider_errors[f"{name}:rate_limited"] += 1
    return {
        "totals": dict(totals),
        "provider_last": providers,
        "provider_errors": dict(provider_errors),
    }


def tracked_bets() -> list[dict[str, Any]]:
    state = load_json(".data/state.json", {})
    if not isinstance(state, dict):
        return []
    raw: list[dict[str, Any]] = []
    for key in ("bets", "published_candidates"):
        for item in state.get(key) or []:
            if isinstance(item, dict):
                raw.append(dict(item))
    dedup: dict[str, dict[str, Any]] = {}
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
    home = str(bet.get("home_team") or bet.get("home") or "").strip()
    away = str(bet.get("away_team") or bet.get("away") or "").strip()
    match = f"{home} — {away}".strip(" —") or str(bet.get("match_key") or "матч")
    selection = str(bet.get("selection") or bet.get("market") or "").strip()
    odds = bet.get("odds")
    status = str(bet.get("status") or "").strip()
    stake = safe_float(bet.get("stake_amount"))
    return f"{match}: {selection} @{odds} | {status} | stake {stake:.2f}"


def summarize_bets(report_date: str) -> dict[str, Any]:
    tz = app_tz()
    bets = tracked_bets()
    published = []
    settled = []
    pending = []
    counters = Counter()
    pnl = 0.0
    stake = 0.0

    for bet in bets:
        status = str(bet.get("status") or "").lower()
        if bet_date(bet, tz) == report_date:
            published.append(bet)
        if settlement_date(bet, tz) == report_date:
            settled.append(bet)
            settlement = bet.get("settlement") if isinstance(bet.get("settlement"), dict) else {}
            outcome = str((settlement or {}).get("outcome") or status).lower()
            counters[outcome] += 1
            pnl += safe_float((settlement or {}).get("pnl"))
            stake += safe_float(bet.get("stake_amount"))
        if status in {"pending", "generated"} and bet_date(bet, tz) <= report_date:
            pending.append(bet)

    state = load_json(".data/state.json", {})
    bankroll = state.get("bankroll") if isinstance(state, dict) and isinstance(state.get("bankroll"), dict) else {}

    return {
        "counts": {
            "published_today": len(published),
            "settled_today": len(settled),
            "pending_relevant": len(pending),
            "won": counters.get("won", 0) + counters.get("half_won", 0),
            "lost": counters.get("lost", 0) + counters.get("half_lost", 0),
            "push": counters.get("push", 0),
            "void": counters.get("void", 0),
        },
        "settled_stake": round(stake, 2),
        "settled_pnl": round(pnl, 2),
        "open_exposure": round(sum(safe_float(item.get("stake_amount")) for item in pending), 2),
        "bankroll": bankroll,
        "published_today": [bet_line(item) for item in published[:10]],
        "settled_today": [bet_line(item) for item in settled[:10]],
        "pending_relevant": [bet_line(item) for item in pending[:10]],
    }


def fallback_reasons() -> dict[str, int]:
    paths = [Path("artifacts/controlled-fallback-report.json"), EXPORT_DIR / "latest-controlled-fallback-report.json"]
    for path in paths:
        payload = load_json(path, None)
        if not isinstance(payload, dict):
            continue
        reasons = Counter()
        evaluated = payload.get("evaluated")
        if isinstance(evaluated, list):
            for item in evaluated:
                if isinstance(item, dict):
                    for reason in item.get("reject_reasons") or []:
                        reasons[str(reason)] += 1
        for key in ("reject_reasons", "reason_counts", "rejection_reasons"):
            raw = payload.get(key)
            if isinstance(raw, dict):
                reasons.update({str(k): safe_int(v) for k, v in raw.items()})
        if reasons:
            return dict(reasons.most_common(12))
    return {}


def quota_rows() -> list[dict[str, Any]]:
    payload = load_json(EXPORT_DIR / "latest-provider-quota-governor.json", {})
    rows = payload.get("providers") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    important = {"odds_api_io", "bzzoiro", "sstats", "api_football", "football_data", "thesportsdb", "futrixmetrics", "weather", "oddspapi", "rapidapi_odds_feed", "rapidapi_sportsbook"}
    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        provider = str(row.get("provider") or "")
        if provider in important:
            out.append({
                "provider": provider,
                "granted": safe_int(row.get("granted")),
                "tokens_after": row.get("tokens_after"),
                "skip_reason": row.get("skip_reason"),
            })
    return out


def build(report_date: str) -> dict[str, Any]:
    runs = collect_runs(report_date)
    return {
        "created_at": datetime.now(UTC).isoformat(),
        "report_date": report_date,
        "timezone": str(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"),
        "runs": run_totals(runs),
        "runs_count": len(runs),
        "bets": summarize_bets(report_date),
        "quota": quota_rows(),
        "fallback_reasons": fallback_reasons(),
    }


def fmt(value: Any) -> str:
    return f"{safe_float(value):.2f}"


def render(payload: dict[str, Any]) -> str:
    totals = Counter(payload.get("runs", {}).get("totals") or {})
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
        f"• За дату: опубликовано {counts.get('published_today', 0)} | закрыто {counts.get('settled_today', 0)} | pending {counts.get('pending_relevant', 0)}",
        f"• Итоги закрытых: W {counts.get('won', 0)} / L {counts.get('lost', 0)} / Push {counts.get('push', 0)} / Void {counts.get('void', 0)} | PnL {fmt(bets.get('settled_pnl'))}",
    ]

    for title, key, icon in [
        ("Прогнозы дня", "published_today", "🎯"),
        ("Закрыто сегодня", "settled_today", "✅"),
        ("Ещё открыто", "pending_relevant", "⏳"),
    ]:
        rows = bets.get(key) or []
        if rows:
            lines += ["", f"{icon} {title}"]
            lines += [f"• {item}" for item in rows[:6]]

    quota = payload.get("quota") or []
    if quota:
        lines += ["", "🔌 API / квоты последнего run"]
        for row in quota[:10]:
            tail = f", skip={row.get('skip_reason')}" if row.get("skip_reason") else ""
            lines.append(f"• {row.get('provider')}: grant {row.get('granted')}, остаток {row.get('tokens_after')}{tail}")

    reasons = payload.get("fallback_reasons") or {}
    if reasons:
        lines += ["", "🧾 Главные причины отказов fallback"]
        for reason, count in list(reasons.items())[:8]:
            lines.append(f"• {reason} — {count}")

    lines += ["", "📝 Settlement запускается перед отчётом. Если матч ещё не закрыт, он остаётся pending и проверяется следующим вечерним/ночным run."]
    return "\n".join(lines)


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
        payload["telegram"] = send_telegram(text, report_date)
    write_json(OUT_JSON, payload)
    write_text(OUT_TXT, text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
