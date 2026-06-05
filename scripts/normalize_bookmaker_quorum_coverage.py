from __future__ import annotations

"""Normalize bookmaker-quorum coverage truth after the daily inventory merge.

This script is deliberately API-free and does not create or change odds. It only
relabels existing coverage evidence so the publication contract matches the
current HARIZON rule:

    2+ real bookmakers on the same market side + context + line movement/value.

The legacy coverage truth still treated 2 independent API odds-sources as a
hard A-tier condition. That made reports show low/zero A-tier readiness even
when the actual publisher uses bookmaker quorum. This normalizer fixes the
truth/report artifacts after build_day_inventory_coverage_truth.py has run.
"""

import csv
import json
import math
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
DAY_INV_DIR = Path(".data/day_inventory")
TRUTH_JSON = EXPORT_DIR / "latest-day-inventory-coverage-truth.json"
TRUTH_CSV = EXPORT_DIR / "latest-day-inventory-coverage-truth.csv"
SUMMARY_JSON = EXPORT_DIR / "latest-day-inventory-summary.json"
REPORT_JSON = EXPORT_DIR / "latest-bookmaker-quorum-coverage-normalizer.json"
HIGHWATER_PATH = DAY_INV_DIR / "coverage_truth_highwater.json"

WINDOWS: list[tuple[str, float | None, float | None]] = [
    ("0-4", 0.0, 4.0),
    ("4-8", 4.0, 8.0),
    ("8-12", 8.0, 12.0),
    ("12-16", 12.0, 16.0),
    ("16-20", 16.0, 20.0),
    ("20-24", 20.0, 24.0),
    (">24", 24.0, None),
]


def load_json(path: str | Path, default: Any) -> Any:
    try:
        p = Path(path)
        if p.exists() and p.stat().st_size > 0:
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


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
        return int(float(str(value)))
    except Exception:
        return default


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on", "passed", "confirmed", "kept"}


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


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


def target_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or os.getenv("DAY_INVENTORY_CACHE_DATE") or "").strip()
    if explicit:
        return explicit
    return datetime.now(UTC).astimezone(app_tz()).date().isoformat()


def list_from_any(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k) for k, v in value.items() if str(k).strip() and v not in (None, "", False, [], {})]
    if isinstance(value, (list, tuple, set)):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in re.split(r"[,|;/]+", value) if x.strip()]
    return []


def norm_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", "_", str(value or "").strip().lower()).strip("_")


def row_keys(row: dict[str, Any]) -> set[str]:
    keys = {str(row.get("match_key") or "").strip(), str(row.get("canonical_match_id") or "").strip()}
    home = norm_key(row.get("home_team") or row.get("home"))
    away = norm_key(row.get("away_team") or row.get("away"))
    kickoff = str(row.get("kickoff_utc") or row.get("commence_time") or row.get("start_time") or "")[:10]
    if home and away and kickoff:
        a, b = sorted([home, away])
        keys.update({f"soccer|{home}|{away}|{kickoff}", f"soccer|{away}|{home}|{kickoff}", f"soccer|{a}|{b}|{kickoff}", f"{kickoff}|{home}|{away}", f"{kickoff}|{a}|{b}"})
    return {k for k in keys if k}


def line_movement_confirmed(row: dict[str, Any]) -> bool:
    if as_bool(row.get("line_movement_confirmed")) or as_bool(row.get("line_guard_kept")):
        return True
    status = str(row.get("line_movement_status") or row.get("movement_status") or "").strip().lower()
    return any(x in status for x in ("confirmed", "passed", "kept", "publish_now_no_next_cron", "movement_ok"))


def line_movement_waiting(row: dict[str, Any]) -> bool:
    if as_bool(row.get("line_movement_waiting")):
        return True
    status = str(row.get("line_movement_status") or row.get("movement_status") or "").strip().lower()
    return any(x in status for x in ("awaiting", "wait", "needs_next", "next_cron"))


def line_movement_declined(row: dict[str, Any]) -> bool:
    if as_bool(row.get("line_movement_declined")):
        return True
    status = str(row.get("line_movement_status") or row.get("movement_status") or "").strip().lower()
    return any(x in status for x in ("declined", "rejected", "dropped", "failed"))


def normalize_row(row: dict[str, Any], min_books: int, min_context: int) -> tuple[dict[str, Any], dict[str, Any]]:
    before = {
        "tier_a_coverage_ready": bool(row.get("tier_a_coverage_ready")),
        "tier_b_coverage_ready": bool(row.get("tier_b_coverage_ready")),
        "ready_for_publish": bool(row.get("ready_for_publish")),
        "price_confirmations": as_int(row.get("price_confirmations")),
        "books_count": as_int(row.get("books_count")),
        "context_sources_count": as_int(row.get("context_sources_count")),
        "odds_sources_count": as_int(row.get("odds_sources_count")),
    }
    out = dict(row)
    price_confirmations = max(as_int(out.get("price_confirmations")), as_int(out.get("books_count")))
    books = max(as_int(out.get("books_count")), price_confirmations)
    context_count = as_int(out.get("context_sources_count"))
    odds_sources_count = as_int(out.get("odds_sources_count"))
    has_odds = bool(out.get("has_odds")) or price_confirmations > 0 or odds_sources_count > 0
    has_context = bool(out.get("has_context")) or context_count > 0
    movement_ok = line_movement_confirmed(out)
    movement_declined = line_movement_declined(out)
    b_ready = has_odds and has_context and books >= min_books and context_count >= 1
    a_ready = b_ready and context_count >= min_context
    if not movement_ok and not movement_declined and (a_ready or b_ready):
        out["line_movement_waiting"] = True
    out.update({
        "price_confirmations": price_confirmations,
        "books_count": books,
        "has_odds": has_odds,
        "has_context": has_context,
        "ready_for_model": bool(out.get("ready_for_model")) or (has_odds and has_context),
        "bookmaker_quorum_contract_ready": b_ready,
        "bookmaker_quorum_a_coverage_ready": a_ready,
        "tier_a_coverage_ready": a_ready,
        "tier_b_coverage_ready": b_ready,
        "tier_b_bookmaker_quorum_ready": b_ready,
        "tier_b_independent_source_quorum_ready": odds_sources_count >= 2,
        "tier_b_confirmation_mode": "bookmaker_quorum" if b_ready else "none",
        "tier_a_publish_ready": a_ready and movement_ok,
        "tier_b_publish_ready": b_ready and movement_ok,
        "ready_for_publish": a_ready and movement_ok,
        "line_movement_confirmed": movement_ok,
        "line_movement_waiting": bool(out.get("line_movement_waiting")) or line_movement_waiting(out),
        "line_movement_declined": movement_declined,
        "need_price_confirmations": max(0, min_books - books),
        "need_context_sources": max(0, min_context - context_count),
        "need_odds_sources": max(0, 1 - odds_sources_count),
        "publication_price_contract": "2plus_bookmakers_same_side",
    })
    missing: list[str] = []
    if books < min_books:
        missing.append("bookmaker_quorum")
    if context_count < min_context:
        missing.append("context_sources")
    if not movement_ok and a_ready:
        missing.append("line_movement")
    out["missing"] = missing
    after = {
        "tier_a_coverage_ready": bool(out.get("tier_a_coverage_ready")),
        "tier_b_coverage_ready": bool(out.get("tier_b_coverage_ready")),
        "ready_for_publish": bool(out.get("ready_for_publish")),
        "price_confirmations": as_int(out.get("price_confirmations")),
        "books_count": as_int(out.get("books_count")),
        "context_sources_count": as_int(out.get("context_sources_count")),
        "odds_sources_count": as_int(out.get("odds_sources_count")),
    }
    return out, {"before": before, "after": after, "changed": before != after}


def counts_from_rows(rows: list[dict[str, Any]], min_books: int, min_context: int) -> dict[str, int]:
    return {
        "matches_total": len(rows),
        "matches_with_odds": sum(1 for r in rows if r.get("has_odds")),
        "matches_with_context": sum(1 for r in rows if r.get("has_context")),
        "matches_with_2plus_price_confirmations": sum(1 for r in rows if as_int(r.get("books_count") or r.get("price_confirmations")) >= min_books),
        "matches_with_2plus_odds_sources": sum(1 for r in rows if as_int(r.get("odds_sources_count")) >= 2),
        "matches_with_2plus_context_sources": sum(1 for r in rows if as_int(r.get("context_sources_count")) >= min_context),
        "matches_ready_for_model": sum(1 for r in rows if r.get("ready_for_model")),
        "matches_ready_for_publish": sum(1 for r in rows if r.get("ready_for_publish")),
        "matches_tier_a_coverage_ready": sum(1 for r in rows if r.get("tier_a_coverage_ready")),
        "matches_tier_b_coverage_ready": sum(1 for r in rows if r.get("tier_b_coverage_ready")),
        "matches_tier_b_bookmaker_quorum_ready": sum(1 for r in rows if r.get("tier_b_bookmaker_quorum_ready")),
        "matches_ready_for_publish_tier_a": sum(1 for r in rows if r.get("tier_a_publish_ready")),
        "matches_ready_for_publish_tier_b": sum(1 for r in rows if r.get("tier_b_publish_ready")),
        "matches_waiting_line_movement": sum(1 for r in rows if r.get("line_movement_waiting")),
        "matches_declined_after_second_snapshot": sum(1 for r in rows if r.get("line_movement_declined")),
    }


def window_key(row: dict[str, Any], now: datetime) -> str | None:
    dt = parse_dt(row.get("kickoff_utc") or row.get("commence_time") or row.get("start_time") or row.get("kickoff"))
    if dt is None:
        return None
    hours = (dt - now).total_seconds() / 3600.0
    if hours < 0:
        return None
    for name, start, end in WINDOWS:
        if hours >= float(start or 0.0) and (end is None or hours < end):
            return name
    return None


def window_counts(rows: list[dict[str, Any]], now: datetime) -> dict[str, dict[str, int]]:
    out = {name: {"matches": 0, "with_odds": 0, "with_context": 0, "bookmaker_2plus": 0, "context_2plus": 0, "a_contract": 0, "b_contract": 0, "waiting_movement": 0, "publish_ready": 0} for name, _, _ in WINDOWS}
    for row in rows:
        name = window_key(row, now)
        if not name:
            continue
        bucket = out[name]
        bucket["matches"] += 1
        bucket["with_odds"] += int(bool(row.get("has_odds")))
        bucket["with_context"] += int(bool(row.get("has_context")))
        bucket["bookmaker_2plus"] += int(as_int(row.get("books_count") or row.get("price_confirmations")) >= 2)
        bucket["context_2plus"] += int(as_int(row.get("context_sources_count")) >= 2)
        bucket["a_contract"] += int(bool(row.get("tier_a_coverage_ready")))
        bucket["b_contract"] += int(bool(row.get("tier_b_coverage_ready")))
        bucket["waiting_movement"] += int(bool(row.get("line_movement_waiting")))
        bucket["publish_ready"] += int(bool(row.get("ready_for_publish")))
    return out


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    fields = [
        "match_key", "kickoff_utc", "league_name", "home_team", "away_team",
        "odds_sources_count", "odds_sources", "price_confirmations", "books_count",
        "context_sources_count", "context_sources", "has_odds", "has_context",
        "ready_for_model", "ready_for_publish", "tier_a_coverage_ready", "tier_b_coverage_ready",
        "tier_b_bookmaker_quorum_ready", "tier_b_confirmation_mode", "tier_a_publish_ready",
        "tier_b_publish_ready", "line_movement_confirmed", "line_movement_waiting",
        "line_movement_declined", "line_movement_status", "need_price_confirmations",
        "need_odds_sources", "need_context_sources", "missing", "publication_price_contract",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            for key in ("odds_sources", "context_sources", "missing"):
                flat[key] = "|".join(str(x) for x in list_from_any(flat.get(key)))
            writer.writerow({key: flat.get(key, "") for key in fields})


def update_inventory_rows(rows_by_key: dict[str, dict[str, Any]]) -> dict[str, Any]:
    updated_files: list[str] = []
    for path in [DAY_INV_DIR / f"{target_date()}.json", DAY_INV_DIR / "latest.json", DAY_INV_DIR / "current.json", DAY_INV_DIR / "today.json"]:
        inv = load_json(path, {})
        if not isinstance(inv, dict) or not isinstance(inv.get("matches"), list):
            continue
        changed = False
        for row in inv["matches"]:
            if not isinstance(row, dict):
                continue
            truth = None
            for key in row_keys(row):
                truth = rows_by_key.get(key)
                if truth:
                    break
            if not truth:
                continue
            row["price_confirmation_sources_count"] = max(as_int(row.get("price_confirmation_sources_count")), as_int(truth.get("price_confirmations")))
            row["books_count"] = max(as_int(row.get("books_count")), as_int(truth.get("books_count")))
            cov = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
            cov.update({
                "books_count": row["books_count"],
                "price_confirmation_sources_count": row["price_confirmation_sources_count"],
                "publication_price_contract": "2plus_bookmakers_same_side",
                "bookmaker_quorum_contract_ready": bool(truth.get("bookmaker_quorum_contract_ready")),
                "tier_a_coverage_ready": bool(truth.get("tier_a_coverage_ready")),
                "tier_b_coverage_ready": bool(truth.get("tier_b_coverage_ready")),
                "ready_for_publish_coverage": bool(truth.get("tier_a_coverage_ready")),
            })
            row["coverage"] = cov
            changed = True
        if changed:
            write_json(path, inv)
            updated_files.append(str(path))
    return {"updated_inventory_files": updated_files}


def extract_raw_books2() -> int:
    # Prefer structured v8 payload, then plain text, then provider stats embedded in latest reports.
    for path in (EXPORT_DIR / "latest-harizon-telegram-run-report.json", EXPORT_DIR / "latest-harizon-telegram-run-report-v5.json"):
        payload = load_json(path, {})
        if isinstance(payload, dict):
            api = payload.get("api") if isinstance(payload.get("api"), dict) else {}
            odds = api.get("odds_api_io") if isinstance(api.get("odds_api_io"), dict) else {}
            val = as_int(odds.get("books_2plus") or odds.get("bookmakers_2plus") or odds.get("matches_with_2plus_books"))
            if val:
                return val
    text_paths = [EXPORT_DIR / "latest-harizon-telegram-run-report.txt", EXPORT_DIR / "latest-run-bot.log"]
    for path in text_paths:
        try:
            text = path.read_text(encoding="utf-8")
        except Exception:
            continue
        for pattern in (r"Raw 2\+ букмекера odds-api\.io:\s*(\d+)", r"2\+ букмекера\s+(\d+)"):
            m = re.search(pattern, text)
            if m:
                return as_int(m.group(1))
    return 0


def main() -> int:
    now = datetime.now(UTC)
    min_books = max(2, as_int(os.getenv("PUBLISH_MIN_BOOKS") or os.getenv("CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS"), 2))
    min_context = max(2, as_int(os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH"), 2))
    payload = load_json(TRUTH_JSON, {})
    rows_raw = payload.get("rows") if isinstance(payload, dict) and isinstance(payload.get("rows"), list) else []
    rows: list[dict[str, Any]] = []
    changed_rows = 0
    for row in rows_raw:
        if not isinstance(row, dict):
            continue
        fixed, diff = normalize_row(row, min_books, min_context)
        rows.append(fixed)
        if diff.get("changed"):
            changed_rows += 1
    rows.sort(key=lambda x: (str(x.get("kickoff_utc") or ""), str(x.get("league_name") or ""), str(x.get("home_team") or "")))
    counts = counts_from_rows(rows, min_books, min_context)
    windows = window_counts(rows, now)
    raw_books2 = extract_raw_books2()
    normalized_books2 = counts.get("matches_with_2plus_price_confirmations", 0)
    report = {
        "status": "ok" if rows else "no_rows",
        "created_at_utc": now.isoformat(),
        "policy": "2plus_bookmakers_same_side_replaces_independent_odds_source_gate",
        "min_books": min_books,
        "min_context_sources": min_context,
        "rows_seen": len(rows_raw),
        "rows_written": len(rows),
        "changed_rows": changed_rows,
        "raw_odds_api_2plus_books": raw_books2,
        "normalized_inventory_2plus_books": normalized_books2,
        "lost_mapping": max(0, raw_books2 - normalized_books2) if raw_books2 else 0,
        "counts": counts,
        "window_counts": windows,
        "notes": [
            "No prices are created here; only existing coverage truth is relabelled to the bookmaker-quorum publication contract.",
            "2 independent odds-sources remain diagnostic only.",
            "If lost_mapping stays high, the next layer is raw odds-api offer to inventory row matching, not guard loosening.",
        ],
    }
    if isinstance(payload, dict) and rows:
        payload["counts"] = counts
        payload["rows"] = rows
        payload["bookmaker_quorum_normalized"] = True
        payload["bookmaker_quorum_normalizer"] = {k: v for k, v in report.items() if k != "window_counts"}
        payload["window_counts"] = windows
        write_json(TRUTH_JSON, payload)
        write_csv(TRUTH_CSV, rows)
        rows_by_key: dict[str, dict[str, Any]] = {}
        for row in rows:
            for key in row_keys(row):
                rows_by_key[key] = row
        report["inventory_update"] = update_inventory_rows(rows_by_key)
        highwater = load_json(HIGHWATER_PATH, {})
        if isinstance(highwater, dict):
            highwater["counts"] = counts
            highwater["bookmaker_quorum_normalized"] = True
            highwater["updated_at_utc"] = now.isoformat()
            write_json(HIGHWATER_PATH, highwater)
        summary = load_json(SUMMARY_JSON, {})
        if isinstance(summary, dict):
            summary["coverage_truth_counts"] = counts
            summary["bookmaker_quorum_normalizer"] = report
            summary["updated_at_utc"] = now.isoformat()
            sources = summary.setdefault("sources", {})
            if isinstance(sources, dict):
                sources["bookmaker_quorum_normalizer"] = {"json": str(REPORT_JSON), "updated_at_utc": now.isoformat(), "counts": counts}
            write_json(SUMMARY_JSON, summary)
    write_json(REPORT_JSON, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
