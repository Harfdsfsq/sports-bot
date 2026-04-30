from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path(".").resolve()
STATE_PATH = ROOT / ".data" / "state.json"
OUT_JSON = ROOT / ".data" / "exports" / "latest-settlement-review-report.json"
OUT_TXT = ROOT / ".data" / "exports" / "latest-settlement-review-report.txt"
SENT_PATH = ROOT / ".data" / "settlement-review-report-sent.json"


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def load_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def target_report_date(offset_days: int) -> str:
    today = datetime.now(UTC).astimezone(app_tz()).date()
    return (today - timedelta(days=max(0, offset_days))).isoformat()


def local_date_for_row(row: dict[str, Any]) -> str | None:
    tz = app_tz()
    for key in ("commence_time", "start_time", "kickoff", "published_at", "sent_at", "created_at"):
        dt = parse_dt(row.get(key))
        if dt is not None:
            return dt.astimezone(tz).date().isoformat()
    return None


def published_rows(report_date: str) -> list[dict[str, Any]]:
    state = load_json(STATE_PATH, {})
    rows: list[dict[str, Any]] = []
    if not isinstance(state, dict):
        return rows
    for row in state.get("bets") or []:
        if not isinstance(row, dict):
            continue
        if not bool(row.get("telegram_sent")):
            continue
        if local_date_for_row(row) != report_date:
            continue
        rows.append(dict(row))
    rows.sort(key=lambda item: str(item.get("commence_time") or item.get("published_at") or ""))
    return rows


def is_closed(row: dict[str, Any]) -> bool:
    return str(row.get("status") or "").lower() in {
        "won",
        "half_won",
        "lost",
        "half_lost",
        "push",
        "void",
        "cancelled",
        "refunded",
    }


def row_pnl(row: dict[str, Any]) -> float:
    settlement = row.get("settlement") if isinstance(row.get("settlement"), dict) else {}
    return as_float(settlement.get("pnl"), as_float(row.get("pnl")))


def explain_row(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "pending").lower()
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    ev = as_float(metrics.get("canonical_ev_pct"), as_float(row.get("ev_pct")))
    edge = as_float(metrics.get("canonical_edge_pp"), as_float(row.get("edge_pct")))
    confidence = as_float(metrics.get("confidence"), as_float(row.get("confidence")))
    if status in {"won", "half_won"}:
        return f"Зашло: value подтвердился на дистанции этого матча; до игры EV {ev:+.1f}%, edge {edge:+.1f} п.п., confidence {confidence:.1f}%."
    if status in {"lost", "half_lost"}:
        return f"Не зашло: предматчевый value не реализовался; проверить xG/форму/движение линии для сегмента. До игры EV {ev:+.1f}%, edge {edge:+.1f} п.п."
    if status == "push":
        return "Возврат: ставка не дала прибыли и не дала убытка; сегмент оставить в наблюдении."
    if status in {"void", "cancelled", "refunded"}:
        return "Ставка отменена/возвращена; не учитывать как модельную ошибку."
    return "Матч ещё не закрыт; отчёт не должен быть отправлен."


def render(report_date: str, rows: list[dict[str, Any]], ready: bool) -> str:
    if not rows:
        return f"Итоговый разбор за {report_date}: опубликованных ставок нет.\n"
    closed = [row for row in rows if is_closed(row)]
    stake = sum(as_float(row.get("stake_amount"), as_float(row.get("stake"))) for row in closed)
    pnl = sum(row_pnl(row) for row in closed)
    roi = (pnl / stake * 100.0) if stake > 0 else 0.0
    lines = [
        f"Итоговый разбор за {report_date}",
        "",
        f"Статус: {'все ставки закрыты' if ready else 'ждём завершения матчей'} ({len(closed)}/{len(rows)}).",
        f"P&L: {pnl:+.2f} | ROI: {roi:+.2f}%",
        "",
    ]
    for idx, row in enumerate(rows, start=1):
        home = str(row.get("home_team") or row.get("home") or "").strip()
        away = str(row.get("away_team") or row.get("away") or "").strip()
        match = f"{home} - {away}".strip(" -") or str(row.get("match_key") or "Матч")
        selection = str(row.get("selection") or row.get("market") or "ставка")
        odds = row.get("odds")
        score = (row.get("settlement") or {}).get("score") if isinstance(row.get("settlement"), dict) else row.get("score")
        score_text = f" | счёт {score}" if score else ""
        lines.append(f"{idx}. {match}")
        lines.append(f"   Ставка: {selection}" + (f" @{odds}" if odds not in (None, "") else ""))
        lines.append(f"   Итог: {row.get('status') or 'pending'} | P&L {row_pnl(row):+.2f}{score_text}")
        lines.append(f"   Разбор: {explain_row(row)}")
    return "\n".join(lines).strip() + "\n"


def already_sent(report_date: str, digest: str) -> bool:
    state = load_json(SENT_PATH, {})
    row = state.get(report_date) if isinstance(state, dict) else None
    return isinstance(row, dict) and row.get("digest") == digest


def mark_sent(report_date: str, digest: str, sent: bool) -> None:
    state = load_json(SENT_PATH, {})
    if not isinstance(state, dict):
        state = {}
    state[report_date] = {
        "sent_at_utc": datetime.now(UTC).isoformat(),
        "digest": digest,
        "telegram_sent": bool(sent),
    }
    write_json(SENT_PATH, state)


def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    data = parse.urlencode({"chat_id": chat_id, "text": text[:3900], "disable_web_page_preview": "true"}).encode("utf-8")
    try:
        with request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20) as response:
            return 200 <= response.status < 300
    except Exception:
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-offset-days", type=int, default=int(float(os.getenv("SETTLEMENT_REVIEW_TARGET_OFFSET_DAYS", "1") or 1)))
    parser.add_argument("--send-telegram", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    report_date = target_report_date(args.target_offset_days)
    rows = published_rows(report_date)
    ready = bool(rows) and all(is_closed(row) for row in rows)
    text = render(report_date, rows, ready)
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    sent = False
    skip_reason = None
    if not rows:
        skip_reason = "no_published_picks"
    elif not ready:
        skip_reason = "waiting_for_all_picks_to_close"
    elif already_sent(report_date, digest) and not args.force:
        skip_reason = "already_sent"
    elif args.send_telegram:
        sent = send_telegram(text)
        mark_sent(report_date, digest, sent)

    payload = {
        "policy_version": "settlement-review-v1",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "report_date": report_date,
        "ready": ready,
        "skip_reason": skip_reason,
        "telegram_sent": sent,
        "published_count": len(rows),
        "closed_count": sum(1 for row in rows if is_closed(row)),
        "text": text,
    }
    write_json(OUT_JSON, payload)
    write_text(OUT_TXT, text if ready else "")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
