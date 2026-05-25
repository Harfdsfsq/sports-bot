from __future__ import annotations

"""Build an auditable per-match coverage truth table for the day inventory.

The Telegram summary is intentionally compact, but debugging the 300-match
contract needs a row-level artifact.  This script does not call external APIs;
it only normalizes the persisted inventory evidence into explicit columns:
independent live odds providers, price confirmations, context sources, and the
remaining gaps before a match can be considered publish-ready.
"""

import csv
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path(".").resolve()
DAY_INV_DIR = ROOT / ".data" / "day_inventory"
EXPORT_DIR = ROOT / ".data" / "exports"
OUT_JSON = EXPORT_DIR / "latest-day-inventory-coverage-truth.json"
OUT_CSV = EXPORT_DIR / "latest-day-inventory-coverage-truth.csv"
SUMMARY_PATH = EXPORT_DIR / "latest-day-inventory-summary.json"
LINE_HISTORY_DIR = ROOT / ".data" / "line_history"
CANDIDATE_LIFECYCLE_PATH = ROOT / ".data" / "candidate-lifecycle-state.json"

LIVE_ODDS_SOURCES = {"odds_api_io", "bzzoiro", "sportlogic"}
CONTEXT_ONLY = {"sstats", "bzzoiro", "sportlogic", "clubelo", "football_data", "thesportsdb", "openfootball", "openligadb", "espn", "weatherapi", "open_meteo"}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def app_tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def target_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or "").strip()
    return explicit or datetime.now(UTC).astimezone(app_tz()).date().isoformat()


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def norm(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    aliases = {
        "oddsapiio": "odds_api_io",
        "odds_api": "odds_api_io",
        "odds_api_io_account1": "odds_api_io",
        "odds_api_io_account2": "odds_api_io",
        "bzzoiro_predictions": "bzzoiro",
        "bzzoiro_current_odds": "bzzoiro",
        "bzzoiro_v2": "bzzoiro",
        "sport_logic": "sportlogic",
        "sportlogic_io": "sportlogic",
        "sstats_form": "sstats",
        "sstats_net": "sstats",
        "football_data_org": "football_data",
        "sportsdb": "thesportsdb",
        "the_sports_db": "thesportsdb",
    }
    return aliases.get(text, text)


def list_from_any(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value.keys() if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [v.strip() for v in re.split(r"[,|;/]+", value) if v.strip()]
    return []


def unique_norm(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        item = norm(value)
        if not item or item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def metadata(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("metadata") if isinstance(row.get("metadata"), dict) else {}


def coverage(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("coverage") if isinstance(row.get("coverage"), dict) else {}


def count_from_metadata(row: dict[str, Any], *keys: str) -> int:
    md = metadata(row)
    best = 0
    for container in (row, md):
        for key in keys:
            best = max(best, as_int(container.get(key)))
    return best

def load_line_state(day: str) -> dict[str, Any]:
    state = load_json(LINE_HISTORY_DIR / f"{day}.json", {})
    if isinstance(state, dict):
        return state
    return {}


def load_candidate_lifecycle() -> dict[str, Any]:
    state = load_json(CANDIDATE_LIFECYCLE_PATH, {})
    return state if isinstance(state, dict) else {}


def row_match_key(row: dict[str, Any]) -> str:
    key = row.get("match_key") or row.get("canonical_match_id") or ""
    if key:
        return str(key)
    parts = [row.get("league_name"), row.get("home_team"), row.get("away_team"), row.get("kickoff_utc") or row.get("commence_time")]
    return "|".join(norm(x) for x in parts if str(x or "").strip())


def line_status_for_row(row: dict[str, Any], line_state: dict[str, Any]) -> dict[str, Any]:
    lines = line_state.get("lines") if isinstance(line_state, dict) else {}
    if not isinstance(lines, dict) or not lines:
        return {"status": "missing", "snapshot_count": 0, "matched_lines": 0, "confirmed_lines": 0, "waiting_lines": 0, "declined_lines": 0}
    match_key = str(row_match_key(row) or "")
    norm_home = norm(row.get("home_team"))
    norm_away = norm(row.get("away_team"))
    matched: list[dict[str, Any]] = []
    for key, entry in lines.items():
        key_s = str(key)
        if match_key and match_key in key_s:
            matched.append(entry if isinstance(entry, dict) else {})
        elif norm_home and norm_away and norm_home in key_s and norm_away in key_s:
            matched.append(entry if isinstance(entry, dict) else {})
    if not matched:
        return {"status": "missing", "snapshot_count": 0, "matched_lines": 0, "confirmed_lines": 0, "waiting_lines": 0, "declined_lines": 0}
    statuses = [str(x.get("last_status") or "") for x in matched]
    snapshot_count = max(as_int(len(x.get("snapshots") or [])) for x in matched)
    confirmed = sum(1 for x in statuses if x == "movement_confirmed")
    waiting = sum(1 for x in statuses if x == "awaiting_next_run")
    declined = sum(1 for x in statuses if x in {"movement_failed", "value_failed"})
    if confirmed:
        status = "movement_confirmed"
    elif waiting:
        status = "awaiting_next_run"
    elif declined:
        status = "declined_after_second_snapshot"
    else:
        status = statuses[-1] or "seen"
    return {"status": status, "snapshot_count": snapshot_count, "matched_lines": len(matched), "confirmed_lines": confirmed, "waiting_lines": waiting, "declined_lines": declined}


def odds_sources(row: dict[str, Any]) -> list[str]:
    sources = unique_norm(list_from_any(row.get("odds_sources")) + list_from_any(row.get("line_sources")))
    return sorted(x for x in sources if x in LIVE_ODDS_SOURCES)


def context_sources(row: dict[str, Any]) -> list[str]:
    md = metadata(row)
    sources = unique_norm(
        list_from_any(row.get("context_sources"))
        + list_from_any(row.get("context_confirmations"))
        + list_from_any(md.get("context_sources"))
        + list_from_any(md.get("context_confirmations"))
    )
    cleaned = []
    for item in sources:
        if item.startswith("provider_"):
            item = item.removeprefix("provider_")
        if item in {"ensemble", "market", "market_signal", "line_history", "odds_api_io", "xg_model_context", "form_context"}:
            continue
        if re.match(r"^context_(source|confirmation)_\d+$", item):
            continue
        cleaned.append(item)
    return sorted(set(cleaned))


def price_confirmations(row: dict[str, Any]) -> int:
    return max(
        count_from_metadata(row, "price_confirmation_sources_count", "price_sources_count", "books_count", "latest_books_max"),
        len(list_from_any(row.get("price_confirmations"))),
        len(list_from_any(row.get("books"))),
    )


def row_truth(row: dict[str, Any], min_odds: int, min_context: int, line_state: dict[str, Any]) -> dict[str, Any]:
    cov = coverage(row)
    osrc = odds_sources(row)
    csrc = context_sources(row)
    pc = price_confirmations(row)
    cc = len(csrc)
    has_odds = bool(cov.get("odds")) or pc > 0
    has_context = bool(cov.get("context")) or cc > 0
    missing: list[str] = []
    if pc < min_odds:
        missing.append("price_confirmations")
    if len(osrc) < min_odds:
        missing.append("independent_odds_sources")
    if cc < min_context:
        missing.append("context_sources")
    ready_tier_a_coverage = has_odds and has_context and pc >= min_odds and len(osrc) >= min_odds and cc >= min_context
    ready_tier_b_coverage = has_odds and has_context and pc >= 1 and len(osrc) >= 1 and cc >= 1
    movement = line_status_for_row(row, line_state)
    movement_status = str(movement.get("status") or "")
    ready_tier_a = ready_tier_a_coverage and movement_status in {"movement_confirmed", "publish_now_no_next_cron"}
    ready_tier_b = ready_tier_b_coverage and movement_status == "movement_confirmed"
    waiting_movement = ready_tier_b_coverage and movement_status in {"missing", "awaiting_next_run", "seen"}
    declined_after_second_snapshot = ready_tier_b_coverage and movement_status == "declined_after_second_snapshot"
    ready_publish = ready_tier_a or ready_tier_b
    return {
        "match_key": row.get("match_key") or row.get("canonical_match_id") or "",
        "kickoff_utc": row.get("kickoff_utc") or row.get("commence_time") or row.get("kickoff_local") or "",
        "league_name": row.get("league_name") or "",
        "home_team": row.get("home_team") or "",
        "away_team": row.get("away_team") or "",
        "odds_sources": osrc,
        "odds_sources_count": len(osrc),
        "price_confirmations": pc,
        "books_count": max(count_from_metadata(row, "books_count"), len(list_from_any(row.get("books")))),
        "context_sources": csrc,
        "context_sources_count": cc,
        "has_odds": has_odds,
        "has_context": has_context,
        "ready_for_model": bool(cov.get("ready_for_model")) or (has_odds and has_context),
        "ready_for_publish": ready_publish,
        "ready_for_publish_tier_a": ready_tier_a,
        "ready_for_publish_tier_b": ready_tier_b and not ready_tier_a,
        "tier_a_coverage_ready": ready_tier_a_coverage,
        "tier_b_coverage_ready": ready_tier_b_coverage,
        "line_movement_status": movement_status,
        "line_snapshot_count": movement.get("snapshot_count", 0),
        "line_waiting_next_run": waiting_movement,
        "line_declined_after_second_snapshot": declined_after_second_snapshot,
        "need_price_confirmations": max(0, min_odds - pc),
        "need_odds_sources": max(0, min_odds - len(osrc)),
        "need_context_sources": max(0, min_context - cc),
        "missing": missing,
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "match_key",
        "kickoff_utc",
        "league_name",
        "home_team",
        "away_team",
        "odds_sources_count",
        "odds_sources",
        "price_confirmations",
        "books_count",
        "context_sources_count",
        "context_sources",
        "has_odds",
        "has_context",
        "ready_for_model",
        "ready_for_publish",
        "ready_for_publish_tier_a",
        "ready_for_publish_tier_b",
        "tier_a_coverage_ready",
        "tier_b_coverage_ready",
        "line_movement_status",
        "line_snapshot_count",
        "line_waiting_next_run",
        "line_declined_after_second_snapshot",
        "need_price_confirmations",
        "need_odds_sources",
        "need_context_sources",
        "missing",
    ]
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            for key in ("odds_sources", "context_sources", "missing"):
                flat[key] = "|".join(str(x) for x in flat.get(key) or [])
            writer.writerow({key: flat.get(key, "") for key in fields})


def main() -> int:
    now = datetime.now(UTC).isoformat()
    d = target_date()
    min_odds = max(2, as_int(os.getenv("PUBLISH_MIN_ODDS_SOURCES") or os.getenv("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES"), 2))
    min_context = max(2, as_int(os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH"), 2))
    inv_path = DAY_INV_DIR / f"{d}.json"
    inv = load_json(inv_path, {})
    matches = [row for row in inv.get("matches", []) if isinstance(row, dict)] if isinstance(inv, dict) else []
    line_state = load_line_state(d)
    lifecycle = load_candidate_lifecycle()
    rows = [row_truth(row, min_odds, min_context, line_state) for row in matches]
    rows.sort(key=lambda x: (str(x.get("kickoff_utc") or ""), str(x.get("league_name") or ""), str(x.get("home_team") or "")))

    counts = {
        "matches_total": len(rows),
        "matches_with_odds": sum(1 for r in rows if r["has_odds"]),
        "matches_with_context": sum(1 for r in rows if r["has_context"]),
        "matches_with_2plus_price_confirmations": sum(1 for r in rows if r["price_confirmations"] >= min_odds),
        "matches_with_2plus_odds_sources": sum(1 for r in rows if r["odds_sources_count"] >= min_odds),
        "matches_with_2plus_context_sources": sum(1 for r in rows if r["context_sources_count"] >= min_context),
        "matches_ready_for_model": sum(1 for r in rows if r["ready_for_model"]),
        "matches_tier_a_coverage_ready": sum(1 for r in rows if r["tier_a_coverage_ready"]),
        "matches_tier_b_coverage_ready": sum(1 for r in rows if r["tier_b_coverage_ready"]),
        "matches_waiting_line_movement": sum(1 for r in rows if r["line_waiting_next_run"]),
        "matches_declined_after_second_snapshot": sum(1 for r in rows if r["line_declined_after_second_snapshot"]),
        "matches_ready_for_publish_tier_a": sum(1 for r in rows if r["ready_for_publish_tier_a"]),
        "matches_ready_for_publish_tier_b": sum(1 for r in rows if r["ready_for_publish_tier_b"]),
        "matches_ready_for_publish": sum(1 for r in rows if r["ready_for_publish"]),
        "candidate_lifecycle_seen": as_int((lifecycle.get("counts") or {}).get("total_candidates_seen")) if isinstance(lifecycle, dict) else 0,
        "candidate_lifecycle_waiting_line_movement": as_int((lifecycle.get("counts") or {}).get("waiting_line_movement")) if isinstance(lifecycle, dict) else 0,
        "candidate_lifecycle_tier_a_publishable": as_int((lifecycle.get("counts") or {}).get("tier_a_publishable")) if isinstance(lifecycle, dict) else 0,
        "candidate_lifecycle_tier_b_publishable": as_int((lifecycle.get("counts") or {}).get("tier_b_publishable")) if isinstance(lifecycle, dict) else 0,
    }
    counts["matches_missing_price_2plus"] = max(0, len(rows) - counts["matches_with_2plus_price_confirmations"])
    counts["matches_missing_odds_source_2plus"] = max(0, len(rows) - counts["matches_with_2plus_odds_sources"])
    counts["matches_missing_context_2plus"] = max(0, len(rows) - counts["matches_with_2plus_context_sources"])

    gap_examples = [r for r in rows if r["missing"]][:25]
    payload = {
        "status": "ok",
        "date_local": d,
        "updated_at_utc": now,
        "inventory_path": str(inv_path),
        "min_odds_sources": min_odds,
        "min_context_sources": min_context,
        "counts": counts,
        "gap_examples": gap_examples,
        "rows": rows,
        "notes": [
            "odds_sources_count is independent live provider count only: odds_api_io, bzzoiro, sportlogic.",
            "price_confirmations is bookmaker/line depth and is tracked separately from provider independence.",
            "A-tier requires 2+ price confirmations, 2+ independent odds sources, 2+ context sources, and confirmed line movement or no next cron window.",
            "B-tier requires 1+ independent odds source, 1+ context source, confirmed second line movement snapshot, and value still alive at candidate time.",
            "Inventory truth can prove coverage and line-state; final candidate lifecycle proves value-vs-publication decisions.",
        ],
    }
    write_json(OUT_JSON, payload)
    write_csv(OUT_CSV, rows)

    summary = load_json(SUMMARY_PATH, {})
    if isinstance(summary, dict):
        sources = summary.setdefault("sources", {})
        if isinstance(sources, dict):
            sources["coverage_truth"] = {
                "updated_at_utc": now,
                "json": str(OUT_JSON),
                "csv": str(OUT_CSV),
                "counts": counts,
            }
        summary["coverage_truth_counts"] = counts
        summary["updated_at_utc"] = now
        write_json(SUMMARY_PATH, summary)
    print(json.dumps({k: payload[k] for k in ("status", "date_local", "updated_at_utc", "counts", "gap_examples")}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
