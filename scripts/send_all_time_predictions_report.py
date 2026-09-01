from __future__ import annotations

"""Manual all-time prediction performance report.

This report is stricter than the rolling daily report: it is intended for a
button-triggered audit of every prediction stored in the durable ledger.  It
hard-deduplicates re-imported rows across different artifact/report dates using
the business identity of the pick (teams + market + side + point + odds + result)
so old examples or replayed Telegram imports cannot inflate win rate, ROI or PnL.
"""

import argparse
import hashlib
import json
import os
import re
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
STATE = DATA / "state.json"
OUT_JSON = EXPORT / "latest-past-predictions-report.json"
OUT_TXT = EXPORT / "latest-past-predictions-report.txt"
SUMMARY = BET_DIR / "performance-summary.json"
SENT = DATA / "past-predictions-report-sent.json"

CLOSED = {"won", "half_won", "lost", "half_lost", "push", "void", "cancelled", "refunded"}
WINS = {"won", "half_won"}
LOSSES = {"lost", "half_lost"}
PUSHES = {"push"}
VOIDS = {"void", "cancelled", "refunded"}
PUBLISHED = CLOSED | {"pending", "published", "telegram_sent", "sent", "open", "active"}


def tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                item = json.loads(line)
                if isinstance(item, dict):
                    rows.append(item)
    except Exception:
        pass
    return rows


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


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


def num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = text.replace("—", "-").replace("–", "-")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def point(value: Any) -> str:
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


def side(value: Any) -> str:
    text = norm(value)
    if any(x in text for x in ("under", "меньше", "tm", "тм")):
        return "under"
    if any(x in text for x in ("over", "больше", "tb", "тб")):
        return "over"
    return text


def home(row: dict[str, Any]) -> str:
    payload = nested(row, "bet_payload")
    return str(first(row.get("home_team"), row.get("home"), payload.get("home_team"), payload.get("home"), "") or "")


def away(row: dict[str, Any]) -> str:
    payload = nested(row, "bet_payload")
    return str(first(row.get("away_team"), row.get("away"), payload.get("away_team"), payload.get("away"), "") or "")


def kickoff(row: dict[str, Any]) -> datetime | None:
    payload = nested(row, "bet_payload")
    for key in ("commence_time", "kickoff_utc", "kickoff", "start_time"):
        dt = parse_dt(row.get(key)) or parse_dt(payload.get(key))
        if dt is not None:
            return dt
    return None


def published_at(row: dict[str, Any]) -> datetime | None:
    payload = nested(row, "bet_payload")
    for key in ("published_at_utc", "published_at", "sent_at", "telegram_sent_at_utc", "created_at_utc", "created_at"):
        dt = parse_dt(row.get(key)) or parse_dt(payload.get(key))
        if dt is not None:
            return dt
    return kickoff(row)


def local_day(row: dict[str, Any]) -> str:
    dt = published_at(row) or kickoff(row)
    return dt.astimezone(tz()).date().isoformat() if dt else "unknown"


def status(row: dict[str, Any]) -> str:
    value = str(row.get("status") or nested(row, "settlement").get("outcome") or "pending").strip().lower()
    return value if value in CLOSED else "pending"


def odds(row: dict[str, Any]) -> float:
    payload = nested(row, "bet_payload")
    return max(num(row.get("odds")), num(row.get("selected_odds")), num(payload.get("odds")))


def stake(row: dict[str, Any]) -> float:
    payload = nested(row, "bet_payload")
    return max(num(row.get("stake")), num(row.get("stake_amount")), num(payload.get("stake")), num(payload.get("stake_amount")))


def pnl(row: dict[str, Any]) -> float:
    settlement = nested(row, "settlement")
    if settlement.get("pnl") not in (None, ""):
        return num(settlement.get("pnl"))
    if row.get("pnl") not in (None, ""):
        return num(row.get("pnl"))
    s = status(row)
    st = stake(row)
    od = odds(row)
    if s == "won":
        return st * max(0.0, od - 1.0)
    if s == "half_won":
        return st * max(0.0, od - 1.0) / 2.0
    if s == "lost":
        return -st
    if s == "half_lost":
        return -st / 2.0
    return 0.0


def market_family(row: dict[str, Any]) -> str:
    payload = nested(row, "bet_payload")
    label = norm(first(row.get("family"), row.get("market_family"), payload.get("family"), payload.get("market_family"), row.get("market_label"), row.get("selection"), "") or "")
    if "total" in label or "тотал" in label or first(row.get("point"), row.get("line"), payload.get("point"), payload.get("line")) not in (None, ""):
        return "totals"
    return label or "unknown_market"


def pick_point(row: dict[str, Any]) -> str:
    payload = nested(row, "bet_payload")
    label = str(first(row.get("market_label"), row.get("selection"), payload.get("selection"), "") or "")
    direct = first(row.get("point"), row.get("line"), row.get("handicap"), payload.get("point"), payload.get("line"), payload.get("handicap"))
    if direct not in (None, ""):
        return point(direct)
    m = re.search(r"([0-9]+(?:[\.,][0-9]+)?)", label)
    return point(m.group(1)) if m else ""


def pick_side(row: dict[str, Any]) -> str:
    payload = nested(row, "bet_payload")
    return side(first(row.get("selection_key"), row.get("selection"), payload.get("selection_key"), payload.get("selection"), row.get("market_label"), "") or "")


def is_published(row: dict[str, Any]) -> bool:
    s = str(row.get("status") or row.get("publication_lifecycle_status") or "").strip().lower()
    return s in PUBLISHED or bool(row.get("telegram_sent") or row.get("published") or row.get("published_at_utc") or row.get("published_at") or row.get("sent_at"))


def raw_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    state = load_json(STATE, {})
    if isinstance(state, dict):
        rows.extend(dict(x) for x in state.get("bets") or [] if isinstance(x, dict))
    rows.extend(load_jsonl(BET_DIR / "published_bets.jsonl"))
    rows.extend(load_jsonl(BET_DIR / "settled_bets.jsonl"))
    pending = load_json(BET_DIR / "pending_bets.json", [])
    if isinstance(pending, list):
        rows.extend(dict(x) for x in pending if isinstance(x, dict))
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


def business_key(row: dict[str, Any]) -> str:
    raw = "|".join([
        norm(home(row)),
        norm(away(row)),
        market_family(row),
        pick_side(row),
        pick_point(row),
        f"{odds(row):.3f}" if odds(row) else "",
        status(row),
        f"{pnl(row):.2f}" if status(row) in CLOSED else "pending",
    ])
    if not raw.strip("|"):
        raw = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def score(row: dict[str, Any]) -> tuple[int, int, float, int, int]:
    closed = 2 if status(row) in CLOSED else 0
    has_tier = 1 if tier(row) != "?" else 0
    st = stake(row)
    settlement_size = len(json.dumps(row.get("settlement"), ensure_ascii=False, sort_keys=True)) if isinstance(row.get("settlement"), dict) else 0
    metrics_size = len(json.dumps(row.get("metrics"), ensure_ascii=False, sort_keys=True)) if isinstance(row.get("metrics"), dict) else 0
    dt = published_at(row) or datetime.max.replace(tzinfo=UTC)
    return (closed, has_tier, st, settlement_size + metrics_size, -int(dt.timestamp()) if dt.timestamp() > 0 else 0)


def merge(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    base, extra = (b, a) if score(b) >= score(a) else (a, b)
    out = dict(base)
    for key, value in extra.items():
        if out.get(key) in (None, "", [], {}, 0) and value not in (None, "", [], {}):
            out[key] = value
    keys = list(out.get("merged_duplicate_keys") or [])
    for row in (a, b):
        for key in (row.get("ledger_semantic_key"), row.get("dedupe_key"), row.get("fingerprint"), business_key(row)):
            if key and str(key) not in keys:
                keys.append(str(key))
    out["merged_duplicate_keys"] = keys[:50]
    out["business_dedupe_key"] = business_key(out)
    return out


def collect(days: int | None, all_time: bool) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    now = datetime.now(UTC).astimezone(tz()).date()
    allowed = None if all_time else {(now - timedelta(days=i)).isoformat() for i in range(max(1, int(days or 7)))}
    by_key: dict[str, dict[str, Any]] = {}
    raw_seen = 0
    published_seen = 0
    out_of_window = 0
    days_seen: set[str] = set()
    duplicate_rows = 0
    for row in raw_rows():
        raw_seen += 1
        if not is_published(row):
            continue
        published_seen += 1
        day = local_day(row)
        days_seen.add(day)
        if allowed is not None and day not in allowed:
            out_of_window += 1
            continue
        row = dict(row)
        row["report_day"] = day
        key = business_key(row)
        if key in by_key:
            duplicate_rows += 1
            by_key[key] = merge(by_key[key], row)
        else:
            row["business_dedupe_key"] = key
            by_key[key] = row
    rows = sorted(by_key.values(), key=lambda r: (str(r.get("report_day") or ""), str(published_at(r) or kickoff(r) or ""), match_title(r)))
    return rows, {
        "raw_rows": raw_seen,
        "published_rows_seen": published_seen,
        "unique": len(rows),
        "duplicates_removed": duplicate_rows,
        "out_of_window": out_of_window,
        "all_time": all_time,
        "first_day": min(days_seen) if days_seen else None,
        "last_day": max(days_seen) if days_seen else None,
        "window_days": sorted(days_seen if all_time else (allowed or set())),
        "dedupe_policy": "business_key_without_import_date:home/away/market/side/point/odds/result/pnl",
    }


def tier(row: dict[str, Any]) -> str:
    value = str(first(row.get("tier"), row.get("publication_tier"), nested(row, "source_summary").get("tier"), "?") or "?").strip().upper()
    return value if value in {"A", "B", "C"} else "?"


def xg(row: dict[str, Any]) -> str:
    text = json.dumps({"xg": row.get("xg_sanity"), "diag": row.get("diagnostics"), "summary": row.get("source_summary")}, ensure_ascii=False).lower()
    if "missing_xg" in text:
        return "missing_xg"
    if "xg" in text:
        return "xg_checked"
    return "xg_unknown"


def quality(row: dict[str, Any]) -> str:
    metrics = nested(row, "metrics")
    text = str(first(row.get("quality_score_source"), row.get("quality_source"), metrics.get("quality_score_source"), nested(row, "source_summary").get("quality_source"), row.get("published_by"), "") or "").lower()
    if "a_cover" in text:
        return "a_cover_evidence"
    if "controlled" in text or "fallback" in text or "reserve" in text:
        return "controlled_fallback"
    if "proxy" in text:
        return "proxy"
    if "raw" in text:
        return "raw"
    return "unknown_quality"


def review_reason(row: dict[str, Any]) -> str | None:
    if status(row) != "pending":
        return None
    ko = kickoff(row)
    if ko is None:
        return "missing_kickoff"
    if datetime.now(UTC) - ko > timedelta(hours=5):
        return "needs_result_settlement"
    return None


def pending_live_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [r for r in rows if status(r) == "pending" and not review_reason(r)]


def summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    closed = [r for r in rows if status(r) in CLOSED]
    wins = sum(1 for r in closed if status(r) in WINS)
    losses = sum(1 for r in closed if status(r) in LOSSES)
    pushes = sum(1 for r in closed if status(r) in PUSHES)
    voids = sum(1 for r in closed if status(r) in VOIDS)
    pending = [r for r in rows if status(r) == "pending"]
    review = [r for r in rows if review_reason(r)]
    live_pending = [r for r in pending if r not in review]
    staked = sum(stake(r) for r in closed if status(r) not in VOIDS)
    profit = sum(pnl(r) for r in closed)
    roi = profit / staked * 100 if staked else 0.0
    hit = wins / max(1, wins + losses) * 100 if wins + losses else 0.0
    avg_odds = sum(odds(r) for r in closed if odds(r) > 0) / max(1, sum(1 for r in closed if odds(r) > 0))
    return {
        "total": len(rows),
        "closed": len(closed),
        "pending": len(pending),
        "pending_live": len(live_pending),
        "review": len(review),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "voids": voids,
        "stake": round(staked, 2),
        "pnl": round(profit, 2),
        "roi_pct": round(roi, 2),
        "bank_roi_pct_on_start_1000": round(profit / 1000.0 * 100.0, 2),
        "hit_rate_pct": round(hit, 2),
        "avg_odds": round(avg_odds, 3),
    }


def group(rows: list[dict[str, Any]], fn: Any) -> dict[str, dict[str, Any]]:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[str(fn(row))].append(row)
    return {key: summary(value) for key, value in sorted(groups.items())}


def match_title(row: dict[str, Any]) -> str:
    h = home(row).strip()
    a = away(row).strip()
    return f"{h} — {a}" if h and a else str(row.get("match_key") or row.get("canonical_match_id") or "Матч")


def pick_text(row: dict[str, Any]) -> str:
    s = pick_side(row)
    label = "ТБ" if s == "over" else "ТМ" if s == "under" else s or "ставка"
    p = pick_point(row)
    od = odds(row)
    return f"{label} {p}".strip() + (f" @{od:.2f}" if od else "")


def emoji(s: str) -> str:
    if s in WINS:
        return "✅"
    if s in LOSSES:
        return "❌"
    if s in PUSHES:
        return "➖"
    if s in VOIDS:
        return "⚪"
    return "⏳"


def group_lines(title: str, groups: dict[str, dict[str, Any]]) -> list[str]:
    lines = [title]
    for key, s in groups.items():
        lines.append(f"• {key}: {s['closed']}/{s['total']} закрыто | {s['wins']}✅/{s['losses']}❌/{s['pushes']}➖ | ROI {s['roi_pct']:+.1f}% | P&L {s['pnl']:+.2f}")
    return lines


def render(rows: list[dict[str, Any]], meta: dict[str, Any], scope_text: str) -> str:
    s = summary(rows)
    review = [r for r in rows if review_reason(r)]
    live_pending = pending_live_rows(rows)
    lines = [
        f"📊 HARIZON — отчёт по прошлым прогнозам {scope_text}",
        "",
        f"Опубликовано: {s['total']} | закрыто: {s['closed']} | pending live: {len(live_pending)} | needs settlement: {len(review)}",
        f"Итог: {s['wins']}✅ / {s['losses']}❌ / {s['pushes']}➖ / {s['voids']}⚪",
        f"P&L: {s['pnl']:+.2f} | ROI на оборот: {s['roi_pct']:+.2f}% | ROI банка от 1000: {s['bank_roi_pct_on_start_1000']:+.2f}% | Hit rate: {s['hit_rate_pct']:.1f}% | avg odds {s['avg_odds']:.2f}",
        f"Ledger: raw {meta['raw_rows']} / published rows {meta['published_rows_seen']} → unique {meta['unique']} | duplicates removed {meta['duplicates_removed']}",
        f"Период: {meta.get('first_day') or 'н/д'} — {meta.get('last_day') or 'н/д'}",
        "",
    ]
    lines.extend(group_lines("🏷️ По уровню", group(rows, tier)))
    lines.append("")
    lines.extend(group_lines("🧪 По xG", group(rows, xg)))
    lines.append("")
    lines.extend(group_lines("📌 По качеству", group(rows, quality)))
    lines.append("")
    lines.append("🧾 Найденные ставки")
    for idx, row in enumerate(rows[-35:], 1):
        ev = first(row.get("ev_pct"), nested(row, "metrics").get("canonical_ev_pct"))
        edge = first(row.get("edge_pp"), row.get("edge_pct"), nested(row, "metrics").get("canonical_edge_pp"))
        metric = ""
        if ev not in (None, "") or edge not in (None, ""):
            metric = f" | EV {num(ev):+.1f}% edge {num(edge):+.1f}п.п."
        settlement = nested(row, "settlement")
        score_text = f" | счёт {settlement.get('score') or row.get('score')}" if (settlement.get("score") or row.get("score")) else ""
        lines.append(f"{idx}. [{row.get('report_day')}] {match_title(row)} — {pick_text(row)} | {tier(row)} | {emoji(status(row))} {status(row)} | P&L {pnl(row):+.2f}{score_text}{metric}")
    if live_pending:
        lines.append("")
        lines.append("🟢 Ещё открыты / ждут матча")
        for row in live_pending[:12]:
            lines.append(f"• {match_title(row)} — {pick_text(row)}")
    if review:
        lines.append("")
        lines.append("🟡 Нужно дозакрыть")
        for row in review[:12]:
            lines.append(f"• {match_title(row)} — {pick_text(row)} | {review_reason(row)}")
    lines.append("")
    lines.append("Статистика хранится в .data/bets/performance-summary.json; отчёт запускается вручную.")
    return "\n".join(lines).strip() + "\n"


def send_telegram(text: str) -> bool:
    token = os.getenv("TELEGRAM_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return False
    chunks: list[str] = []
    current = ""
    for line in text.splitlines():
        trial = line if not current else current + "\n" + line
        if len(trial) <= 3900:
            current = trial
        else:
            chunks.append(current)
            current = line
    if current:
        chunks.append(current)
    ok = False
    for chunk in chunks:
        data = parse.urlencode({"chat_id": chat_id, "text": chunk, "disable_web_page_preview": "true"}).encode("utf-8")
        try:
            with request.urlopen(f"https://api.telegram.org/bot{token}/sendMessage", data=data, timeout=20) as response:
                ok = ok or 200 <= response.status < 300
        except Exception:
            return False
    return ok


def sent_state() -> dict[str, Any]:
    state = load_json(SENT, {})
    return state if isinstance(state, dict) else {}


def mark_sent(key: str, digest: str, sent: bool) -> None:
    state = sent_state()
    state[key] = {"digest": digest, "telegram_sent": sent, "sent_at_utc": datetime.now(UTC).isoformat()}
    write_json(SENT, state)


def sync_ledger() -> dict[str, Any]:
    try:
        from scripts import sync_publication_ledger
        return sync_publication_ledger.sync_bets()
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--all", action="store_true", default=True)
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--send-telegram", action="store_true")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    ledger_sync = sync_ledger()
    rows, meta = collect(args.days, all_time=args.all)
    scope_text = "за всё время" if args.all else f"за {args.days} дн."
    text = render(rows, meta, scope_text) if rows else "📊 HARIZON — отчёт по прошлым прогнозам\n\nСохранённых прогнозов пока нет.\n"
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()
    sent = False
    skip_reason = None
    key = "all_time" if args.all else f"last_{args.days}_days"
    already = sent_state().get(key, {})
    if not args.force and isinstance(already, dict) and already.get("digest") == digest:
        skip_reason = "already_sent_same_digest"
    elif args.send_telegram:
        sent = send_telegram(text)
        mark_sent(key, digest, sent)
    else:
        skip_reason = "send_telegram_not_requested"
    payload = {
        "policy_version": "all-time-predictions-report-v2-pending-split",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "scope": key,
        "telegram_sent": sent,
        "skip_reason": skip_reason,
        "ledger_sync": ledger_sync,
        "meta": meta,
        "summary": summary(rows),
        "by_tier": group(rows, tier),
        "by_xg": group(rows, xg),
        "by_quality": group(rows, quality),
        "live_pending": pending_live_rows(rows),
        "review_queue": [r for r in rows if review_reason(r)],
        "rows": rows,
        "text": text,
    }
    write_json(OUT_JSON, payload)
    write_json(SUMMARY, {k: payload[k] for k in ("policy_version", "created_at_utc", "scope", "meta", "summary", "by_tier", "by_xg", "by_quality", "live_pending", "review_queue")})
    write_text(OUT_TXT, text)
    print(json.dumps({"status": "ok", "scope": key, "rows": len(rows), "closed": payload["summary"]["closed"], "pending": payload["summary"]["pending"], "live_pending": payload["summary"]["pending_live"], "review": payload["summary"]["review"], "telegram_sent": sent, "skip_reason": skip_reason, "duplicates_removed": meta["duplicates_removed"]}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
