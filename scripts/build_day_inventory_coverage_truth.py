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


def odds_sources(row: dict[str, Any]) -> list[str]:
    sources = unique_norm(list_from_any(row.get("odds_sources")) + list_from_any(row.get("line_sources")))
    return sorted(x for x in sources if x in LIVE_ODDS_SOURCES)


def row_has_bzzoiro_context_hint(row: dict[str, Any]) -> bool:
    md = metadata(row)
    cov = coverage(row)
    if str(row.get("source") or "").lower() == "bzzoiro":
        return True
    if any(bool(md.get(key)) for key in (
        "bzzoiro_context_fields", "bzzoiro_has_prediction", "bzzoiro_has_context_hint",
        "bzzoiro_context_gap_annotated_at_utc", "bzzoiro_line_evidence_context_bridge",
    )):
        return True
    source_ids = row.get("source_ids") if isinstance(row.get("source_ids"), dict) else {}
    provider_ids = md.get("provider_source_ids") if isinstance(md.get("provider_source_ids"), dict) else {}
    has_bzz_id = any(str(k).lower().startswith(("bzzoiro", "bsd")) for k in list(source_ids.keys()) + list(provider_ids.keys()))
    # Count provider-day Bzzoiro prediction/event rows as context when the row
    # also carries a context/xG/form flag. This fixes the frozen inventory losing
    # the provider even though Bzzoiro supplied prediction/event metadata.
    return bool(has_bzz_id and (cov.get("context") or cov.get("xg") or md.get("bzzoiro_raw_source")))


def context_sources(row: dict[str, Any]) -> list[str]:
    md = metadata(row)
    raw_sources = (
        list_from_any(row.get("context_sources"))
        + list_from_any(row.get("context_confirmations"))
        + list_from_any(md.get("context_sources"))
        + list_from_any(md.get("context_confirmations"))
    )
    if row_has_bzzoiro_context_hint(row):
        raw_sources.append("bzzoiro")
    sources = unique_norm(raw_sources)
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



def line_movement_status(row: dict[str, Any]) -> str:
    md = metadata(row)
    cov = coverage(row)
    for container in (row, md, cov):
        for key in (
            "line_movement_status", "line_movement_lifecycle_status", "movement_status",
            "line_guard_status", "line_state", "movement_lifecycle_status",
        ):
            val = container.get(key) if isinstance(container, dict) else None
            if str(val or "").strip():
                return norm(val)
    return ""

def line_movement_confirmed(row: dict[str, Any]) -> bool:
    status = line_movement_status(row)
    if any(token in status for token in ("confirmed", "passed", "kept", "movement_ok")):
        return True
    md = metadata(row)
    cov = coverage(row)
    for container in (row, md, cov):
        for key in ("line_movement_confirmed", "movement_confirmed", "line_guard_kept", "has_second_line_snapshot"):
            val = container.get(key) if isinstance(container, dict) else None
            if str(val).strip().lower() in {"1", "true", "yes", "on"}:
                return True
    return False

def line_movement_waiting(row: dict[str, Any]) -> bool:
    status = line_movement_status(row)
    if any(token in status for token in ("awaiting", "wait", "needs_next", "needs_later", "next_cron")):
        return True
    md = metadata(row)
    cov = coverage(row)
    for container in (row, md, cov):
        for key in ("awaiting_line_movement", "waiting_line_movement", "needs_line_movement_recheck"):
            val = container.get(key) if isinstance(container, dict) else None
            if str(val).strip().lower() in {"1", "true", "yes", "on"}:
                return True
    return False

def line_movement_declined(row: dict[str, Any]) -> bool:
    status = line_movement_status(row)
    return any(token in status for token in ("declined", "rejected", "dropped", "failed"))

def row_truth(row: dict[str, Any], min_odds: int, min_context: int) -> dict[str, Any]:
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
    tier_a_coverage_ready = has_odds and has_context and pc >= min_odds and len(osrc) >= min_odds and cc >= min_context
    tier_b_coverage_ready = has_odds and has_context and pc >= 1 and len(osrc) >= 1 and cc >= 1
    movement_ok = line_movement_confirmed(row)
    movement_drop = line_movement_declined(row)
    movement_wait = line_movement_waiting(row)
    # Row-level artifacts often do not persist an explicit "awaiting" flag for
    # every covered match.  If A/B coverage is present but no confirmed second
    # snapshot exists, the row is still waiting for line-movement confirmation.
    # This keeps the Telegram/coverage truth from saying B-tier coverage exists
    # while "waiting line movement" is zero.
    if not movement_ok and not movement_drop and (tier_a_coverage_ready or tier_b_coverage_ready):
        movement_wait = True
    ready_publish = tier_a_coverage_ready and movement_ok
    tier_b_publish_ready = tier_b_coverage_ready and movement_ok
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
        "tier_a_coverage_ready": tier_a_coverage_ready,
        "tier_b_coverage_ready": tier_b_coverage_ready,
        "tier_a_publish_ready": ready_publish,
        "tier_b_publish_ready": tier_b_publish_ready,
        "line_movement_confirmed": movement_ok,
        "line_movement_waiting": movement_wait,
        "line_movement_declined": movement_drop,
        "line_movement_status": line_movement_status(row),
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
        "tier_a_coverage_ready",
        "tier_b_coverage_ready",
        "tier_a_publish_ready",
        "tier_b_publish_ready",
        "line_movement_confirmed",
        "line_movement_waiting",
        "line_movement_declined",
        "line_movement_status",
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
    rows = [row_truth(row, min_odds, min_context) for row in matches]
    rows.sort(key=lambda x: (str(x.get("kickoff_utc") or ""), str(x.get("league_name") or ""), str(x.get("home_team") or "")))

    counts = {
        "matches_total": len(rows),
        "matches_with_odds": sum(1 for r in rows if r["has_odds"]),
        "matches_with_context": sum(1 for r in rows if r["has_context"]),
        "matches_with_2plus_price_confirmations": sum(1 for r in rows if r["price_confirmations"] >= min_odds),
        "matches_with_2plus_odds_sources": sum(1 for r in rows if r["odds_sources_count"] >= min_odds),
        "matches_with_2plus_context_sources": sum(1 for r in rows if r["context_sources_count"] >= min_context),
        "matches_ready_for_model": sum(1 for r in rows if r["ready_for_model"]),
        "matches_ready_for_publish": sum(1 for r in rows if r["ready_for_publish"]),
        "matches_tier_a_coverage_ready": sum(1 for r in rows if r["tier_a_coverage_ready"]),
        "matches_tier_b_coverage_ready": sum(1 for r in rows if r["tier_b_coverage_ready"]),
        "matches_ready_for_publish_tier_a": sum(1 for r in rows if r["tier_a_publish_ready"]),
        "matches_ready_for_publish_tier_b": sum(1 for r in rows if r["tier_b_publish_ready"]),
        "matches_waiting_line_movement": sum(1 for r in rows if r["line_movement_waiting"]),
        "matches_declined_after_second_snapshot": sum(1 for r in rows if r["line_movement_declined"]),
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
            "ready_for_publish strict truth requires tier A coverage plus confirmed line movement.",
            "tier_b_coverage_ready means 1+ line evidence, 1+ context source, and later still needs confirmed line movement/value.",
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
