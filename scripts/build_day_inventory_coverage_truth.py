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

from app.services.publication_thresholds import publish_min_context_sources, publish_min_odds_sources
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


def context_sources(row: dict[str, Any], extra_sources: list[str] | None = None) -> list[str]:
    md = metadata(row)
    sources = unique_norm(
        list_from_any(row.get("context_sources"))
        + list_from_any(row.get("context_confirmations"))
        + list_from_any(extra_sources)
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


def row_truth(
    row: dict[str, Any],
    min_odds: int,
    min_context: int,
    context_source_index: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    cov = coverage(row)
    osrc = odds_sources(row)
    match_key = str(row.get("match_key") or row.get("canonical_match_id") or "")
    extra_context = list((context_source_index or {}).get(match_key) or [])
    csrc = context_sources(row, extra_context)
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
    ready_publish = has_odds and has_context and pc >= min_odds and len(osrc) >= min_odds and cc >= min_context
    strict_ready = ready_publish
    return {
        "match_key": match_key,
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
        "tier_a_coverage_ready": strict_ready,
        "tier_b_coverage_ready": strict_ready,
        "tier_b_bookmaker_quorum_ready": strict_ready,
        "tier_b_confirmation_mode": "strict_independent_sources" if strict_ready else "none",
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
    min_odds = publish_min_odds_sources()
    min_context = publish_min_context_sources()
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
            "ready_for_publish requires 2+ price confirmations, 2+ independent odds sources, and 2+ context sources.",
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
