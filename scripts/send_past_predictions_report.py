from __future__ import annotations

"""Send a manual Telegram retrospective report for HARIZON predictions.

This script is designed for an explicit GitHub Actions workflow_dispatch run.
It reads the durable semantic ledger, deduplicates all historical picks, computes
closed/pending/review stats, writes cumulative performance JSON, and sends a
Telegram report on demand.  It does not run on a schedule by itself.
"""

import argparse
import hashlib
import json
import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import parse, request
from zoneinfo import ZoneInfo

ROOT = Path(".").resolve()
DATA = ROOT / ".data"
BET_DIR = DATA / "bets"
EXPORT = DATA / "exports"
STATE_PATH = DATA / "state.json"
OUT_JSON = EXPORT / "latest-past-predictions-report.json"
OUT_TXT = EXPORT / "latest-past-predictions-report.txt"
SUMMARY_JSON = BET_DIR / "performance-summary.json"
SENT_PATH = DATA / "past-predictions-report-sent.json"

CLOSED_STATUSES = {"won", "half_won", "lost", "half_lost", "push", "void", "cancelled", "refunded"}
WIN_STATUSES = {"won", "half_won"}
LOSS_STATUSES = {"lost", "half_lost"}
PUSH_STATUSES = {"push"}
VOID_STATUSES = {"void", "cancelled", "refunded"}


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def load_json(path: str | Path, default: Any) -> Any:
    try:
        p = Path(path)
        if not p.exists() or p.stat().st_size <= 0:
            return default
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_jsonl(path: str | Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        p = Path(path)
        if not p.exists():
            return rows
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    except Exception:
        pass
    return rows


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
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е").replace("—", "-").replace("–", "-")
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return " ".join(text.split())


def point_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        f = float(str(value).replace(",", "."))
        return str(int(f)) if f.is_integer() else f"{f:.2f}".rstrip("0").rstrip(".")
    except Exception:
        return norm(value)


def nested(row: dict[str, Any], key: str) -> dict[str, Any]:
    value = row.get(key)
    return value if isinstance(value, dict) else {}


def first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def selection_key(value: Any) -> str:
    text = norm(value)
    if any(t in text for t in ("меньше", "under", "тм", "tm")):
        return "under"
    if any(t in text for t in ("больше", "over", "тб", "tb")):
        return "over"
    return text


def local_day_for(row: dict[str, Any], tz: ZoneInfo, *, prefer_publication: bool = True) -> str | None:
    payload = nested(row, "bet_payload")
    keys_pub = ("published_at_utc", "published_at", "sent_at", "telegram_sent_at_utc", "created_at_utc", "created_at")
    keys_kick = ("commence_time", "kickoff_utc", "kickoff", "start_time")
    keys = keys_pub + keys_kick if prefer_publication else keys_kick + keys_pub
    for key in keys:
        dt = parse_dt(row.get(key)) or parse_dt(payload.get(key))
        if dt is not None:
            return dt.astimezone(tz).date().isoformat()
    return None


def kickoff_dt(row: dict[str, Any]) -> datetime | None:
    payload = nested(row, "bet_payload")
    for key in ("commence_time", "kickoff_utc", "kickoff", "start_time"):
        dt = parse_dt(row.get(key)) or parse_dt(payload.get(key))
        if dt is not None:
            return dt
    return None


def semantic_key(row: dict[str, Any]) -> str:
    for key in ("ledger_semantic_key", "canonical_publication_key", "dedupe_key", "fingerprint", "prediction_id"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    payload = nested(row, "bet_payload")
    raw = "|".join([
        norm(first(row.get("match_key"), row.get("canonical_match_id"), payload.get("match_key")) or ""),
        norm(first(row.get("home_team"), row.get("home"), payload.get("home_team"), payload.get("home")) or ""),
        norm(first(row.get("away_team"), row.get("away"), payload.get("away_team"), payload.get("away")) or ""),
        str(kickoff_dt(row) or "")[:16],
        norm(first(row.get("family"), row.get("market_family"), payload.get("family"), payload.get("market_family")) or ""),
        selection_key(first(row.get("selection_key"), row.get("selection"), payload.get("selection_key"), payload.get("selection")) or ""),
        point_text(first(row.get("point"), row.get("line"), row.get("handicap"), payload.get("point"), payload.get("line"))),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def is_published(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or row.get("publication_lifecycle_status") or "").strip().lower()
    if status in CLOSED_STATUSES | {"pending", "published", "telegram_sent", "sent", "open", "active"}:
        return True
    return bool(row.get("telegram_sent") or row.get("published") or row.get("published_at_utc") or row.get("published_at") or row.get("sent_at"))


def raw_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    state = load_json(STATE_PATH, {})
    if isinstance(state, dict):
        rows.extend(dict(x) for x in state.get("bets") or [] if isinstance(x, dict))
    rows.extend(load_jsonl(BET_DIR / "published_bets.jsonl"))
    rows.extend(load_jsonl(BET_DIR / "settled_bets.jsonl"))
    payload = load_json(BET_DIR / "pending_bets.json", [])
    if isinstance(payload, list):
        rows.extend(dict(x) for x in payload if isinstance(x, dict))
    for path in (EXPORT / "latest-picks.json", EXPORT / "latest-pending-bets.json", EXPORT / "latest-settled-bets.json"):
        payload = load_json(path, [])
        if isinstance(payload, list):
            rows.extend(dict(x) for x in payload if isinstance(x, dict))
        elif isinstance(payload, dict):
            for key in ("rows", "bets", "items", "pending", "settled"):
                value = payload.get(key)
                if isinstance(value, list):
                    rows.extend(dict(x) for x in value if isinstance(x, dict))
    return rows


def row_score(row: dict[str, Any]) -> tuple[int, int, int, float, int]:
    status = str(row.get("status") or "").lower()
    closed = 2 if status in CLOSED_STATUSES else 0
    sent = 1 if is_published(row) else 0
    stake = max(as_float(row.get("stake")), as_float(row.get("stake_amount")), as_float(nested(row, "bet_payload").get("stake")), as_float(nested(row, "bet_payload").get("stake_amount")))
    settlement_size = len(json.dumps(row.get("settlement"), ensure_ascii=False, sort_keys=True)) if isinstance(row.get("settlement"), dict) else 0
    metric_size = len(json.dumps(row.get("metrics"), ensure_ascii=False, sort_keys=True)) if isinstance(row.get("metrics"), dict) else 0
    return (closed, sent, 1 if stake > 0 else 0, stake, settlement_size + metric_size)


def merge_rows(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    base, extra = (b, a) if row_score(b) >= row_score(a) else (a, b)
    out = dict(base)
    for key, value in extra.items():
        if out.get(key) in (None, "", [], {}, 0) and value not in (None, "", [], {}):
            out[key] = value
    stake = max(as_float(a.get("stake")), as_float(a.get("stake_amount")), as_float(b.get("stake")), as_float(b.get("stake_amount")))
    if stake > 0:
        out["stake"] = stake
        out["stake_amount"] = stake
    out["ledger_semantic_key"] = semantic_key(out)
    return out


def published_rows(days: int | None, tz: ZoneInfo, *, all_time: bool = False) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    today = datetime.now(UTC).astimezone(tz).date()
    allowed_days = None if all_time else {(today - timedelta(days=i)).isoformat() for i in range(max(1, int(days or 3)))}
    by_key: dict[str, dict[str, Any]] = {}
    total_seen = 0
    out_of_window = 0
    days_seen: set[str] = set()
    for row in raw_rows():
        if not isinstance(row, dict) or not is_published(row):
            continue
        total_seen += 1
        day = local_day_for(row, tz, prefer_publication=True) or local_day_for(row, tz, prefer_publication=False) or "unknown"
        days_seen.add(day)
        if allowed_days is not None and day not in allowed_days:
            out_of_window += 1
            continue
        key = semantic_key(row)
        row = dict(row)
        row["report_day"] = day
        row["ledger_semantic_key"] = key
        by_key[key] = merge_rows(by_key[key], row) if key in by_key else row
    rows = sorted(by_key.values(), key=lambda r: (str(r.get("report_day") or ""), str(r.get("commence_time") or r.get("kickoff") or r.get("published_at_utc") or "")))
    return rows, {
        "raw_seen": total_seen,
        "out_of_window": out_of_window,
        "unique": len(rows),
        "all_time": all_time,
        "window_days": sorted(days_seen if all_time else (allowed_days or set())),
        "first_day": min(days_seen) if days_seen else None,
        "last_day": max(days_seen) if days_seen else None,
    }


def status_of(row: dict[str, Any]) -> str:
    status = str(row.get("status") or "pending").strip().lower()
    if status in CLOSED_STATUSES:
        return status
    return "pending"


def stake_of(row: dict[str, Any]) -> float:
    return max(as_float(row.get("stake")), as_float(row.get("stake_amount")), as_float(nested(row, "bet_payload").get("stake")), as_float(nested(row, "bet_payload").get("stake_amount")))


def odds_of(row: dict[str, Any]) -> float:
    return max(as_float(row.get("odds")), as_float(row.get("selected_odds")), as_float(nested(row, "bet_payload").get("odds")))


def pnl_of(row: dict[str, Any]) -> float:
    settlement = nested(row, "settlement")
    if settlement.get("pnl") not in (None, ""):
        return as_float(settlement.get("pnl"))
    if row.get("pnl") not in (None, ""):
        return as_float(row.get("pnl"))
    status = status_of(row)
    stake = stake_of(row)
    odds = odds_of(row)
    if status == "won":
        return stake * max(0.0, odds - 1.0)
    if status == "half_won":
        return stake * max(0.0, odds - 1.0) / 2.0
    if status == "lost":
        return -stake
    if status == "half_lost":
        return -stake / 2.0
    return 0.0


def review_reason(row: dict[str, Any], now: datetime) -> str | None:
    if status_of(row) != "pending":
        return None
    ko = kickoff_dt(row)
    if ko is None:
        return "missing_kickoff"
    if now - ko > timedelta(hours=5):
        return "needs_result_settlement"
    return None


def tier_of(row: dict[str, Any]) -> str:
    value = str(row.get("tier") or row.get("publication_tier") or nested(row, "source_summary").get("tier") or "?").strip().upper()
    return value if value in {"A", "B", "C"} else "?"


def xg_bucket(row: dict[str, Any]) -> str:
    text = json.dumps({"row": row.get("xg_sanity"), "diagnostics": row.get("diagnostics"), "summary": row.get("source_summary")}, ensure_ascii=False).lower()
    if "missing_xg" in text or "xg sanity: missing" in text:
        return "missing_xg"
    if "xg" in text:
        return "xg_checked"
    return "xg_unknown"


def quality_bucket(row: dict[str, Any]) -> str:
    metrics = nested(row, "metrics")
    text = str(first(row.get("quality_score_source"), row.get("quality_source"), metrics.get("quality_score_source"), nested(row, "source_summary").get("quality_source"), "") or "").lower()
    if "a_cover" in text:
        return "a_cover_evidence"
    if "proxy" in text:
        return "proxy"
    if "raw" in text:
        return "raw"
    return "unknown_quality"


def calc_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if status_of(r) in CLOSED_STATUSES]
    pending = [r for r in rows if status_of(r) == "pending"]
    wins = sum(1 for r in closed if status_of(r) in WIN_STATUSES)
    losses = sum(1 for r in closed if status_of(r) in LOSS_STATUSES)
    pushes = sum(1 for r in closed if status_of(r) in PUSH_STATUSES)
    voids = sum(1 for r in closed if status_of(r) in VOID_STATUSES)
    stake = sum(stake_of(r) for r in closed if status_of(r) not in VOID_STATUSES)
    pnl = sum(pnl_of(r) for r in closed)
    roi = pnl / stake * 100.0 if stake > 0 else 0.0
    hit = wins / max(1, wins + losses) * 100.0 if wins + losses > 0 else 0.0
    avg_odds = sum(odds_of(r) for r in closed if odds_of(r) > 0) / max(1, sum(1 for r in closed if odds_of(r) > 0))
    return {
        "total": len(rows),
        "closed": len(closed),
        "pending": len(pending),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "voids": voids,
        "stake": round(stake, 2),
        "pnl": round(pnl, 2),
        "roi_pct": round(roi, 2),
        "hit_rate_pct": round(hit, 2),
        "avg_odds": round(avg_odds, 3),
    }


def group_summary(rows: list[dict[str, Any]], key_fn: Any) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[str(key_fn(row) or "?")].append(row)
    return {key: calc_summary(value) for key, value in sorted(grouped.items())}


def match_title(row: dict[str, Any]) -> str:
    home = str(row.get("home_team") or row.get("home") or nested(row, "bet_payload").get("home_team") or "").strip()
    away = str(row.get("away_team") or row.get("away") or nested(row, "bet_payload").get("away_team") or "").strip()
    return f"{home} — {away}" if home and away else str(row.get("match_key") or row.get("canonical_match_id") or "Матч")


def pick_text(row: dict[str, Any]) -> str:
    family = str(first(row.get("family"), row.get("market_family"), nested(row, "bet_payload").get("family"), "") or "").lower()
    selection = str(first(row.get("selection"), nested(row, "bet_payload").get("selection"), row.get("selection_key"), "ставка") or "ставка")
    point = first(row.get("point"), row.get("line"), nested(row, "bet_payload").get("point"), nested(row, "bet_payload").get("line"))
    odds = odds_of(row)
    if family == "totals" or point not in (None, ""):
        label = "ТБ" if selection_key(selection) == "over" else "ТМ" if selection_key(selection) == "under" else selection
        main = f"{label} {point_text(point)}".strip()
    else:
        main = selection
    return main + (f" @{odds:.2f}" if odds > 0 else "")


def outcome_emoji(status: str) -> str:
    if status in WIN_STATUSES:
        return "✅"
    if status in LOSS_STATUSES:
        return "❌"
    if status in PUSH_STATUSES:
        return "➖"
    if status in VOID_STATUSES:
        return "⚪"
    return "⏳"


def format_group_lines(title: str, groups: dict[str, dict[str, Any]]) -> list[str]:
    lines = [title]
    for key, summary in groups.items():
        if summary["total"] <= 0:
            continue
        lines.append(
            f"• {key}: {summary['closed']}/{summary['total']} закрыто | "
            f"{summary['wins']}✅/{summary['losses']}❌/{summary['pushes']}➖ | "
            f"ROI {summary['roi_pct']:+.1f}% | P&L {summary['pnl']:+.2f}"
        )
    return lines


def render(rows: list[dict[str, Any]], days: int | None, meta: dict[str, Any]) -> str:
    now = datetime.now(UTC)
    summary = calc_summary(rows)
    review_rows = [r for r in rows if review_reason(r, now)]
    scope = "за всё время" if meta.get("all_time") else f"за {days} дн."
    lines = [
        f"📊 HARIZON — отчёт по прошлым прогнозам {scope}",
        "",
        f"Опубликовано: {summary['total']} | закрыто: {summary['closed']} | pending: {summary['pending']} | review: {len(review_rows)}",
        f"Итог: {summary['wins']}✅ / {summary['losses']}❌ / {summary['pushes']}➖ / {summary['voids']}⚪",
        f"P&L: {summary['pnl']:+.2f} | ROI: {summary['roi_pct']:+.2f}% | Hit rate: {summary['hit_rate_pct']:.1f}% | avg odds {summary['avg_odds']:.2f}",
        f"Ledger: raw {meta.get('raw_seen', 0)} → unique {meta.get('unique', 0)} | период: {meta.get('first_day') or 'н/д'} — {meta.get('last_day') or 'н/д'}",
        "",
    ]
    lines.extend(format_group_lines("🏷️ По уровню", group_summary(rows, tier_of)))
    lines.append("")
    lines.extend(format_group_lines("🧪 По xG", group_summary(rows, xg_bucket)))
    lines.append("")
    lines.extend(format_group_lines("📌 По качеству", group_summary(rows, quality_bucket)))
    lines.append("")
    lines.append("🧾 Последние/найденные ставки")
    for idx, row in enumerate(rows[-35:], start=1):
        status = status_of(row)
        settlement = nested(row, "settlement")
        score = settlement.get("score") or row.get("score") or ""
        score_text = f" | счёт {score}" if score else ""
        day = row.get("report_day") or "?"
        ev = first(row.get("ev_pct"), nested(row, "metrics").get("canonical_ev_pct"))
        edge = first(row.get("edge_pp"), row.get("edge_pct"), nested(row, "metrics").get("canonical_edge_pp"))
        metric_text = ""
        if ev not in (None, "") or edge not in (None, ""):
            metric_text = f" | EV {as_float(ev):+.1f}% edge {as_float(edge):+.1f}п.п."
        lines.append(
            f"{idx}. [{day}] {match_title(row)} — {pick_text(row)} | {tier_of(row)} | "
            f"{outcome_emoji(status)} {status} | P&L {pnl_of(row):+.2f}{score_text}{metric_text}"
        )
    if len(rows) > 35:
        lines.append(f"… всего {len(rows)} ставок; полный список в JSON artifact.")
    if review_rows:
        lines.append("")
        lines.append("🟡 Нужно дозакрыть")
        for row in review_rows[:12]:
            lines.append(f"• {match_title(row)} — {pick_text(row)} | причина: {review_reason(row, now)}")
    lines.append("")
    lines.append("Отчёт запускается вручную. Статистика хранится в ledger и performance-summary.json.")
    return "\n".join(lines).strip() + "\n"


def split_text(text: str, limit: int = 3900) -> list[str]:
    text = str(text or "").strip()
    if len(text) <= limit:
        return [text] if text else []
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        if not current:
            current = line
            continue
        trial = current + "\n" + line
        if len(trial) <= limit:
            current = trial
        else:
            chunks.append(current)
            current = line
    if current:
        chunks.append(current)
    return chunks


def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    ok = False
    for part in split_text(text):
        data = parse.urlencode({"chat_id": chat_id, "text": part, "disable_web_page_preview": "true"}).encode("utf-8")
        try:
            with request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20) as response:
                ok = 200 <= response.status < 300 or ok
        except Exception:
            return False
    return ok


def sent_state() -> dict[str, Any]:
    state = load_json(SENT_PATH, {})
    return state if isinstance(state, dict) else {}


def already_sent(key: str, digest: str) -> bool:
    row = sent_state().get(key)
    return isinstance(row, dict) and row.get("digest") == digest and bool(row.get("sent_at_utc"))


def mark_sent(key: str, digest: str, telegram_sent: bool) -> None:
    state = sent_state()
    state[key] = {"digest": digest, "telegram_sent": bool(telegram_sent), "sent_at_utc": datetime.now(UTC).isoformat()}
    write_json(SENT_PATH, state)


def sync_ledger() -> dict[str, Any]:
    try:
        from scripts import sync_publication_ledger
        return sync_publication_ledger.sync_bets()
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=int(float(os.getenv("PAST_PREDICTIONS_REPORT_DAYS", "3") or 3)))
    parser.add_argument("--all", action="store_true", help="Include every stored prediction from the durable ledger.")
    parser.add_argument("--send-telegram", action="store_true")
    parser.add_argument("--force", action="store_true", help="Send even when the report digest is unchanged.")
    parser.add_argument("--send-empty", action="store_true")
    args = parser.parse_args()

    ledger_sync = sync_ledger()
    tz = app_tz()
    rows, meta = published_rows(args.days, tz, all_time=args.all)
    text = render(rows, None if args.all else args.days, meta) if rows or args.send_empty else ""
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest() if text else ""
    window_key = "all_time" if args.all else f"last_{args.days}_days:{'-'.join(meta.get('window_days', []))}"
    sent = False
    skip_reason = None
    if not text:
        skip_reason = "no_published_predictions"
    elif not args.force and already_sent(window_key, digest):
        skip_reason = "already_sent_same_digest"
    elif args.send_telegram:
        sent = send_telegram(text)
        mark_sent(window_key, digest, sent)
    else:
        skip_reason = "send_telegram_not_requested"

    summary = calc_summary(rows)
    payload = {
        "policy_version": "past-predictions-report-v2-all-time-manual",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "scope": "all_time" if args.all else f"last_{args.days}_days",
        "days": None if args.all else args.days,
        "window_key": window_key,
        "telegram_sent": sent,
        "skip_reason": skip_reason,
        "ledger_sync": ledger_sync,
        "meta": meta,
        "summary": summary,
        "by_tier": group_summary(rows, tier_of),
        "by_xg": group_summary(rows, xg_bucket),
        "by_quality": group_summary(rows, quality_bucket),
        "review_queue": [r for r in rows if review_reason(r, datetime.now(UTC))],
        "rows": rows,
        "text": text,
    }
    write_json(OUT_JSON, payload)
    write_json(SUMMARY_JSON, {k: payload[k] for k in ("policy_version", "created_at_utc", "scope", "meta", "summary", "by_tier", "by_xg", "by_quality")})
    write_text(OUT_TXT, text)
    print(json.dumps({"status": "ok", "scope": payload["scope"], "rows": len(rows), "closed": summary["closed"], "pending": summary["pending"], "telegram_sent": sent, "skip_reason": skip_reason}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
