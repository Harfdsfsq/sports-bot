from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter, defaultdict
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


def write_text(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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


def local_date(dt_value: Any, tz: ZoneInfo | timezone) -> str:
    dt = parse_dt(dt_value)
    if dt is None:
        return ""
    return dt.astimezone(tz).date().isoformat()


def target_report_date() -> str:
    tz = app_tz()
    offset = max(0, env_int("DAILY_REPORT_TARGET_OFFSET_DAYS", 0))
    return (datetime.now(UTC).astimezone(tz).date() - timedelta(days=offset)).isoformat()


def load_run_archives(report_date: str) -> list[dict[str, Any]]:
    roots = [Path(".logs/runs"), Path(".data/history/runs")]
    seen: set[str] = set()
    runs: list[dict[str, Any]] = []
    tz = app_tz()

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
            run_dt = (
                summary.get("current_time_local")
                or summary.get("started_time_local")
                or payload.get("created_at")
                or summary.get("current_time_utc")
                or summary.get("started_time_utc")
            )
            if local_date(run_dt, tz) != report_date:
                continue
            payload["_archive_path"] = str(path)
            runs.append(payload)

    runs.sort(key=lambda item: str(item.get("created_at") or (item.get("summary") or {}).get("started_time_utc") or ""))
    return runs


def safe_num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def aggregate_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    totals = Counter()
    source_errors = Counter()
    provider_last: dict[str, dict[str, Any]] = {}
    no_pick_reasons = Counter()

    for payload in runs:
        summary = dict(payload.get("summary") or {})
        totals["runs"] += 1
        totals["published"] += safe_int(summary.get("published") or summary.get("published_to_telegram"))
        totals["telegram_messages_sent"] += safe_int(summary.get("telegram_messages_sent"))
        totals["matches_seen"] += safe_int(summary.get("matches_seen"))
        totals["matches_with_offers"] += safe_int(summary.get("matches_with_offers"))
        totals["contexts_built"] += safe_int(summary.get("contexts_built"))
        totals["candidates_raw"] += safe_int(summary.get("candidates_raw"))
        totals["candidates_before_quality"] += safe_int(summary.get("candidates_before_quality"))
        totals["candidates_rejected_by_quality"] += safe_int(summary.get("candidates_rejected_by_quality"))
        totals["candidates_publishable"] += safe_int(summary.get("candidates_publishable"))

        if str(summary.get("run_status") or summary.get("status") or "ok").lower() in {"error", "failed"}:
            totals["errors"] += 1

        source_stats = dict(summary.get("source_stats") or payload.get("source_stats") or {})
        for provider, row in source_stats.items():
            if not isinstance(row, dict):
                continue
            provider_last[str(provider)] = row
            if row.get("runtime_error") or row.get("error"):
                source_errors[str(provider)] += 1
            if row.get("rate_limited"):
                source_errors[f"{provider}:rate_limited"] += 1

        # Some archives include fallback/no-pick data; latest fallback file is handled separately.
        report = payload.get("controlled_fallback_report") or payload.get("fallback_report")
        if isinstance(report, dict):
            for reason, count in (report.get("reject_reasons") or report.get("reasons") or {}).items():
                no_pick_reasons[str(reason)] += safe_int(count)

    return {
        "totals": dict(totals),
        "provider_last": provider_last,
        "source_errors": dict(source_errors),
        "no_pick_reasons": dict(no_pick_reasons),
    }


def load_latest_fallback_reasons() -> dict[str, int]:
    candidates = [
        Path("artifacts/controlled-fallback-report.json"),
        Path(".data/exports/latest-controlled-fallback-report.json"),
    ]
    for path in candidates:
        payload = load_json(path, None)
        if not isinstance(payload, dict):
            continue
        raw = (
            payload.get("reject_reasons")
            or payload.get("rejection_reasons")
            or payload.get("reason_counts")
            or payload.get("reasons")
            or {}
        )
        if isinstance(raw, dict):
            return {str(k): safe_int(v) for k, v in raw.items()}
    return {}


def tracked_bets() -> list[dict[str, Any]]:
    state = load_json(".data/state.json", {})
    if not isinstance(state, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key in ("bets", "published_candidates"):
        for item in state.get(key) or []:
            if isinstance(item, dict):
                rows.append(dict(item))
    # Dedupe by fingerprint/prediction id.
    out: dict[str, dict[str, Any]] = {}
    for item in rows:
        key = str(item.get("fingerprint") or item.get("prediction_id") or "")
        if not key:
            key = hashlib.sha1(json.dumps(item, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
        out[key] = item
    return list(out.values())


def settlement_dt(bet: dict[str, Any]) -> datetime | None:
    settlement = bet.get("settlement") if isinstance(bet.get("settlement"), dict) else {}
    for key in ("settled_at", "completed_at", "checked_at", "updated_at"):
        dt = parse_dt((settlement or {}).get(key) or bet.get(key))
        if dt is not None:
            return dt
    return None


def bet_published_date(bet: dict[str, Any], tz: ZoneInfo | timezone) -> str:
    return local_date(
        bet.get("published_at")
        or bet.get("created_at")
        or bet.get("sent_at")
        or bet.get("commence_time")
        or bet.get("start_time"),
        tz,
    )


def bet_kickoff_date(bet: dict[str, Any], tz: ZoneInfo | timezone) -> str:
    return local_date(bet.get("commence_time") or bet.get("start_time") or bet.get("kickoff"), tz)


def summarize_bets(report_date: str) -> dict[str, Any]:
    tz = app_tz()
    bets = tracked_bets()
    published_today: list[dict[str, Any]] = []
    settled_today: list[dict[str, Any]] = []
    pending_relevant: list[dict[str, Any]] = []

    for bet in bets:
        status = str(bet.get("status") or "").lower()
        pub_date = bet_published_date(bet, tz)
        ko_date = bet_kickoff_date(bet, tz)
        settle_date = local_date(settlement_dt(bet), tz)
        if pub_date == report_date or ko_date == report_date:
            published_today.append(bet)
        if settle_date == report_date:
            settled_today.append(bet)
        if status in {"pending", "generated"} and (pub_date <= report_date or ko_date <= report_date):
            pending_relevant.append(bet)

    counters = Counter()
    pnl = 0.0
    stake = 0.0
    for bet in settled_today:
        status = str(bet.get("status") or "").lower()
        outcome = str((bet.get("settlement") or {}).get("outcome") or status).lower()
        counters[outcome] += 1
        pnl += safe_num((bet.get("settlement") or {}).get("pnl"))
        stake += safe_num(bet.get("stake_amount"))

    open_exposure = sum(safe_num(item.get("stake_amount")) for item in pending_relevant)
    bankroll = load_json(".data/state.json", {}).get("bankroll", {}) if isinstance(load_json(".data/state.json", {}), dict) else {}

    return {
        "published_today": published_today,
        "settled_today": settled_today,
        "pending_relevant": pending_relevant,
        "counts": {
            "published_today": len(published_today),
            "settled_today": len(settled_today),
            "pending_relevant": len(pending_relevant),
            "won": counters.get("won", 0) + counters.get("half_won", 0),
            "lost": counters.get("lost", 0) + counters.get("half_lost", 0),
            "push": counters.get("push", 0),
            "void": counters.get("void", 0),
        },
        "settled_stake": round(stake, 2),
        "settled_pnl": round(pnl, 2),
        "open_exposure": round(open_exposure, 2),
        "bankroll": bankroll if isinstance(bankroll, dict) else {},
    }


def load_quota_status() -> list[dict[str, Any]]:
    payload = load_json(".data/exports/latest-provider-quota-governor.json", {})
    rows = payload.get("providers") if isinstance(payload, dict) else []
    if not isinstance(rows, list):
        return []
    important = {
        "odds_api_io",
        "bzzoiro",
        "sstats",
        "api_football",
        "football_data",
        "thesportsdb",
        "futrixmetrics",
        "weather",
        "oddspapi",
        "rapidapi_odds_feed",
        "rapidapi_sportsbook",
    }
    result = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        provider = str(row.get("provider") or "")
        if provider in important:
            result.append({
                "provider": provider,
                "granted": safe_int(row.get("granted")),
                "tokens_after": row.get("tokens_after"),
                "skip_reason": row.get("skip_reason"),
            })
    return result


def short_bet_line(bet: dict[str, Any]) -> str:
    home = str(bet.get("home_team") or bet.get("home") or "").strip()
    away = str(bet.get("away_team") or bet.get("away") or "").strip()
    selection = str(bet.get("selection") or bet.get("market") or "").strip()
    odds = bet.get("odds")
    status = str(bet.get("status") or "").strip()
    stake = safe_num(bet.get("stake_amount"))
    match = f"{home} — {away}".strip(" —")
    if not match:
        match = str(bet.get("match_key") or "матч")
    return f"{match}: {selection} @{odds} | {status} | stake {stake:.2f}"


def build_report(report_date: str) -> dict[str, Any]:
    runs = load_run_archives(report_date)
    run_summary = aggregate_runs(runs)
    bet_summary = summarize_bets(report_date)
    fallback_reasons = Counter(run_summary.get("no_pick_reasons") or {})
    fallback_reasons.update(load_latest_fallback_reasons())

    return {
        "created_at": datetime.now(UTC).isoformat(),
        "report_date": report_date,
        "timezone": str(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"),
        "runs_count": len(runs),
        "runs": run_summary,
        "bets": {
            "counts": bet_summary["counts"],
            "settled_stake": bet_summary["settled_stake"],
            "settled_pnl": bet_summary["settled_pnl"],
            "open_exposure": bet_summary["open_exposure"],
            "bankroll": bet_summary["bankroll"],
            "published_today": [short_bet_line(item) for item in bet_summary["published_today"][:12]],
            "settled_today": [short_bet_line(item) for item in bet_summary["settled_today"][:12]],
            "pending_relevant": [short_bet_line(item) for item in bet_summary["pending_relevant"][:12]],
        },
        "quota": load_quota_status(),
        "fallback_reasons": dict(fallback_reasons.most_common(12)),
    }


def fmt_money(value: Any) -> str:
    return f"{safe_num(value):.2f}"


def render_report(payload: dict[str, Any]) -> str:
    totals = Counter(payload.get("runs", {}).get("totals") or {})
    bets = payload.get("bets") or {}
    counts = bets.get("counts") or {}
    bankroll = bets.get("bankroll") or {}
    quota = payload.get("quota") or []
    reasons = payload.get("fallback_reasons") or {}

    lines: list[str] = []
    lines.append(f"📊 Дневной отчёт работы бота — {payload.get('report_date')}")
    lines.append("")
    lines.append("🧠 Работа скрипта")
    lines.append(
        f"• Run’ов: {payload.get('runs_count', 0)} | ошибок: {totals.get('errors', 0)} | Telegram-сообщений: {totals.get('telegram_messages_sent', 0)}"
    )
    lines.append(
        f"• Матчи: {totals.get('matches_seen', 0)} | с линиями: {totals.get('matches_with_offers', 0)} | контекстов: {totals.get('contexts_built', 0)}"
    )
    lines.append(
        f"• Кандидаты: raw {totals.get('candidates_raw', 0)} | до качества {totals.get('candidates_before_quality', 0)} | publishable {totals.get('candidates_publishable', 0)}"
    )
    lines.append(f"• Опубликовано прогнозов: {totals.get('published', 0)}")
    lines.append("")

    lines.append("💼 Банк и закрытие прогнозов")
    lines.append(
        f"• Банк: {fmt_money(bankroll.get('current_balance'))} | открытый риск: {fmt_money(bankroll.get('open_exposure'))} | доступно: {fmt_money(bankroll.get('available_balance'))}"
    )
    lines.append(
        f"• За дату: опубликовано {counts.get('published_today', 0)} | закрыто {counts.get('settled_today', 0)} | pending {counts.get('pending_relevant', 0)}"
    )
    lines.append(
        f"• Итоги закрытых: W {counts.get('won', 0)} / L {counts.get('lost', 0)} / Push {counts.get('push', 0)} / Void {counts.get('void', 0)} | PnL {fmt_money(bets.get('settled_pnl'))}"
    )

    published = bets.get("published_today") or []
    if published:
        lines.append("")
        lines.append("🎯 Прогнозы дня")
        for item in published[:6]:
            lines.append(f"• {item}")

    settled = bets.get("settled_today") or []
    if settled:
        lines.append("")
        lines.append("✅ Закрыто сегодня")
        for item in settled[:6]:
            lines.append(f"• {item}")

    pending = bets.get("pending_relevant") or []
    if pending:
        lines.append("")
        lines.append("⏳ Ещё открыто / ждёт settlement")
        for item in pending[:6]:
            lines.append(f"• {item}")

    if quota:
        lines.append("")
        lines.append("🔌 API / квоты последнего run")
        for row in quota[:10]:
            grant = row.get("granted")
            tokens = row.get("tokens_after")
            reason = row.get("skip_reason")
            tail = f", skip={reason}" if reason else ""
            lines.append(f"• {row.get('provider')}: grant {grant}, остаток {tokens}{tail}")

    if reasons:
        lines.append("")
        lines.append("🧾 Главные причины отказа последнего fallback")
        for reason, count in list(reasons.items())[:8]:
            lines.append(f"• {reason} — {count}")

    lines.append("")
    lines.append("📝 Settlement запускается перед отчётом. Если матч ещё не закрыт, он остаётся в pending и будет проверен следующим вечерним/ночным run.")
    return "\n".join(lines)


def message_hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def split_telegram(text: str, limit: int = 3900) -> list[str]:
    if len(text) <= limit:
        return [text]
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for line in text.splitlines():
        add = len(line) + 1
        if current and size + add > limit:
            parts.append("\n".join(current))
            current = []
            size = 0
        current.append(line)
        size += add
    if current:
        parts.append("\n".join(current))
    return parts


def send_telegram(text: str, *, report_date: str) -> dict[str, Any]:
    token = os.getenv("TELEGRAM_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return {"sent": False, "reason": "missing_telegram_credentials"}

    sent_state = load_json(SENT_STATE, {})
    if not isinstance(sent_state, dict):
        sent_state = {}

    h = message_hash(text)
    previous = sent_state.get(report_date) if isinstance(sent_state.get(report_date), dict) else {}
    if previous.get("hash") == h and not env_bool("DAILY_OPS_REPORT_FORCE_SEND", False):
        return {"sent": False, "reason": "unchanged", "hash": h}

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    sent_parts = 0
    for part in split_telegram(text):
        data = parse.urlencode({
            "chat_id": chat_id,
            "text": part,
            "disable_web_page_preview": "true",
        }).encode("utf-8")
        req = request.Request(url, data=data, method="POST")
        with request.urlopen(req, timeout=20) as response:
            response.read()
        sent_parts += 1

    sent_state[report_date] = {
        "sent_at": datetime.now(UTC).isoformat(),
        "hash": h,
        "parts": sent_parts,
    }
    write_json(SENT_STATE, sent_state)
    return {"sent": True, "parts": sent_parts, "hash": h}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--date", default="", help="Report date YYYY-MM-DD. Default uses DAILY_REPORT_TARGET_OFFSET_DAYS.")
    parser.add_argument("--send-telegram", action="store_true")
    args = parser.parse_args()

    report_date = args.date.strip() or target_report_date()
    payload = build_report(report_date)
    text = render_report(payload)
    payload["text"] = text

    if args.send_telegram:
        payload["telegram"] = send_telegram(text, report_date=report_date)

    write_json(OUT_JSON, payload)
    write_text(OUT_TXT, text)
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
