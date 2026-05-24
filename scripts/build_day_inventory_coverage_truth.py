from __future__ import annotations

"""Build auditable per-match coverage truth from the unified day inventory contract.

This version treats persisted metadata/harizon_contract numeric evidence as the
source of truth when explicit source names are unavailable. It fixes the case
where runtime evidence was merged into metadata but the report still showed
`2+ context/price = 0`.
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
CONTEXT_EXCLUDE = {"", "ensemble", "market", "market_signal", "line_history", "odds_api_io", "xg_model_context", "form_context"}


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
        if isinstance(value, bool):
            return int(value)
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value).replace(",", ".")))
    except Exception:
        return default


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def run_now() -> datetime:
    for key in ("HARIZON_RUN_NOW_UTC", "RUN_NOW_UTC", "CURRENT_TIME_UTC"):
        dt = parse_dt(os.getenv(key))
        if dt is not None:
            return dt
    for path in (EXPORT_DIR / "latest-run-summary.json", ROOT / ".logs" / "debug-last-run.json"):
        payload = load_json(path, {})
        summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else payload
        if isinstance(summary, dict):
            for key in ("current_time_utc", "started_time_utc", "created_at_utc", "updated_at_utc"):
                dt = parse_dt(summary.get(key))
                if dt is not None:
                    return dt
    return datetime.now(UTC)


def canonical_match_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    return re.sub(r"[^a-z0-9|]+", " ", text).strip()


def sent_match_keys() -> set[str]:
    keys: set[str] = set()
    for path in (ROOT / ".data" / "fallback-sent-index.json", ROOT / ".data" / "published-candidate-index.json"):
        payload = load_json(path, {})
        rows: list[Any] = []
        if isinstance(payload, dict):
            rows.extend(payload.values())
            for key in ("sent", "published", "rows"):
                if isinstance(payload.get(key), list):
                    rows.extend(payload[key])
        elif isinstance(payload, list):
            rows = payload
        for row in rows:
            if isinstance(row, dict) and row.get("telegram_sent") is True:
                key = canonical_match_key(row.get("match_key"))
                if key:
                    keys.add(key)
    return keys


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
        "soccerstats": "sstats",
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
    out: list[str] = []
    seen: set[str] = set()
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


def contract(row: dict[str, Any]) -> dict[str, Any]:
    return row.get("harizon_contract") if isinstance(row.get("harizon_contract"), dict) else {}


def count_from_containers(row: dict[str, Any], *keys: str) -> int:
    best = 0
    for container in (row, metadata(row), coverage(row), contract(row)):
        if not isinstance(container, dict):
            continue
        for key in keys:
            best = max(best, as_int(container.get(key)))
    return best


def odds_sources(row: dict[str, Any]) -> list[str]:
    values = (
        list_from_any(row.get("odds_sources"))
        + list_from_any(row.get("line_sources"))
        + list_from_any(metadata(row).get("odds_sources"))
        + list_from_any(contract(row).get("odds_sources"))
    )
    return sorted({x for x in unique_norm(values) if x in LIVE_ODDS_SOURCES})


def context_sources(row: dict[str, Any]) -> list[str]:
    values = (
        list_from_any(row.get("context_sources"))
        + list_from_any(row.get("context_confirmations"))
        + list_from_any(metadata(row).get("context_sources"))
        + list_from_any(metadata(row).get("context_confirmations"))
        + list_from_any(contract(row).get("context_sources"))
    )
    cleaned: list[str] = []
    for item in unique_norm(values):
        if item.startswith("provider_"):
            item = item.removeprefix("provider_")
        if item in CONTEXT_EXCLUDE:
            continue
        if re.match(r"^context_(source|confirmation)_\d+$", item):
            continue
        cleaned.append(item)
    return sorted(set(cleaned))


def price_confirmation_count(row: dict[str, Any]) -> int:
    return max(
        count_from_containers(row, "price_confirmation_sources_count", "price_sources_count", "books_count", "latest_books_max", "bookmaker_count"),
        len(list_from_any(row.get("price_confirmations"))),
        len(list_from_any(metadata(row).get("price_confirmations"))),
        len(list_from_any(row.get("books"))),
        len(list_from_any(metadata(row).get("books"))),
    )


def row_truth(row: dict[str, Any], min_odds: int, min_context: int, now_dt: datetime, sent_keys: set[str]) -> dict[str, Any]:
    cov = coverage(row)
    osrc = odds_sources(row)
    csrc = context_sources(row)

    odds_sources_count = max(len(osrc), count_from_containers(row, "independent_odds_sources_count", "odds_sources_count", "odds_source_count"))
    context_sources_count = max(len(csrc), count_from_containers(row, "context_sources_count", "confirmation_sources_count", "context_source_count"))
    price_confirmations = price_confirmation_count(row)

    has_odds = bool(cov.get("odds")) or odds_sources_count > 0 or price_confirmations > 0
    has_context = bool(cov.get("context")) or context_sources_count > 0

    missing: list[str] = []
    if price_confirmations < min_odds:
        missing.append("price_confirmations")
    if odds_sources_count < min_odds:
        missing.append("independent_odds_sources")
    if context_sources_count < min_context:
        missing.append("context_sources")

    strict_ready_publish = (
        has_odds
        and has_context
        and price_confirmations >= min_odds
        and odds_sources_count >= min_odds
        and context_sources_count >= min_context
    )
    match_key = row.get("match_key") or row.get("canonical_match_id") or ""
    kickoff_utc = row.get("kickoff_utc") or row.get("commence_time") or row.get("kickoff_local") or ""
    kickoff_dt = parse_dt(kickoff_utc)
    minutes_to_kickoff = round((kickoff_dt - now_dt).total_seconds() / 60.0, 2) if kickoff_dt is not None else None
    already_published = canonical_match_key(match_key) in sent_keys
    ready_publish = bool(strict_ready_publish and not already_published)
    missing_new = list(missing)
    if already_published:
        missing_new.append("already_published")
    return {
        "match_key": match_key,
        "kickoff_utc": kickoff_utc,
        "minutes_to_kickoff": minutes_to_kickoff,
        "league_name": row.get("league_name") or "",
        "home_team": row.get("home_team") or "",
        "away_team": row.get("away_team") or "",
        "odds_sources": osrc,
        "odds_sources_count": odds_sources_count,
        "price_confirmations": price_confirmations,
        "books_count": max(count_from_containers(row, "books_count"), len(list_from_any(row.get("books"))), len(list_from_any(metadata(row).get("books")))),
        "context_sources": csrc,
        "context_sources_count": context_sources_count,
        "has_odds": has_odds,
        "has_context": has_context,
        "ready_for_model": bool(cov.get("ready_for_model")) or (has_odds and has_context),
        "strict_ready_for_publish": strict_ready_publish,
        "already_published": already_published,
        "ready_for_publish": ready_publish,
        "need_price_confirmations": max(0, min_odds - price_confirmations),
        "need_odds_sources": max(0, min_odds - odds_sources_count),
        "need_context_sources": max(0, min_context - context_sources_count),
        "missing": missing_new,
        "evidence_basis": "metadata+harizon_contract+explicit_sources",
    }


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "match_key", "kickoff_utc", "minutes_to_kickoff", "league_name", "home_team", "away_team",
        "odds_sources_count", "odds_sources", "price_confirmations", "books_count",
        "context_sources_count", "context_sources", "has_odds", "has_context", "ready_for_model",
        "strict_ready_for_publish", "already_published", "ready_for_publish",
        "need_price_confirmations", "need_odds_sources", "need_context_sources", "missing", "evidence_basis",
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
    now_dt = run_now()
    d = target_date()
    min_odds = max(2, as_int(os.getenv("PUBLISH_MIN_ODDS_SOURCES") or os.getenv("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES"), 2))
    min_context = max(2, as_int(os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH"), 2))
    inv_path = DAY_INV_DIR / f"{d}.json"
    inv = load_json(inv_path, {})
    matches = [row for row in inv.get("matches", []) if isinstance(row, dict)] if isinstance(inv, dict) else []
    rows = [row_truth(row, min_odds, min_context, now_dt, sent_match_keys()) for row in matches]
    rows.sort(key=lambda x: (str(x.get("kickoff_utc") or ""), str(x.get("league_name") or ""), str(x.get("home_team") or "")))

    counts = {
        "matches_total": len(rows),
        "matches_with_odds": sum(1 for r in rows if r["has_odds"]),
        "matches_with_context": sum(1 for r in rows if r["has_context"]),
        "matches_with_2plus_price_confirmations": sum(1 for r in rows if r["price_confirmations"] >= min_odds),
        "matches_with_2plus_odds_sources": sum(1 for r in rows if r["odds_sources_count"] >= min_odds),
        "matches_with_2plus_context_sources": sum(1 for r in rows if r["context_sources_count"] >= min_context),
        "matches_ready_for_model": sum(1 for r in rows if r["ready_for_model"]),
        "matches_ready_for_publish_strict": sum(1 for r in rows if r.get("strict_ready_for_publish")),
        "matches_strict_ready_already_published": sum(1 for r in rows if r.get("strict_ready_for_publish") and r.get("already_published")),
        "matches_ready_for_publish": sum(1 for r in rows if r["ready_for_publish"]),
    }
    counts["matches_missing_price_2plus"] = max(0, len(rows) - counts["matches_with_2plus_price_confirmations"])
    counts["matches_missing_odds_source_2plus"] = max(0, len(rows) - counts["matches_with_2plus_odds_sources"])
    counts["matches_missing_context_2plus"] = max(0, len(rows) - counts["matches_with_2plus_context_sources"])

    payload = {
        "status": "ok",
        "date_local": d,
        "updated_at_utc": datetime.now(UTC).isoformat(),
        "inventory_path": str(inv_path),
        "min_odds_sources": min_odds,
        "min_context_sources": min_context,
        "counts": counts,
        "gap_examples": [r for r in rows if r["missing"]][:25],
        "rows": rows,
        "notes": [
            "odds_sources_count is independent live provider count only: odds_api_io, bzzoiro, sportlogic.",
            "price_confirmations is bookmaker/line depth and is tracked separately from provider independence.",
            "metadata/harizon_contract numeric counts are used when explicit source names are unavailable.",
            "strict_ready_for_publish requires 2+ price confirmations, 2+ independent odds sources, and 2+ context sources.",
            "ready_for_publish additionally excludes matches already sent to Telegram.",
        ],
    }
    write_json(OUT_JSON, payload)
    write_csv(OUT_CSV, rows)

    summary = load_json(SUMMARY_PATH, {})
    if isinstance(summary, dict):
        sources = summary.setdefault("sources", {})
        if isinstance(sources, dict):
            sources["coverage_truth"] = {
                "updated_at_utc": payload["updated_at_utc"],
                "json": str(OUT_JSON),
                "csv": str(OUT_CSV),
                "counts": counts,
                "evidence_basis": "metadata+harizon_contract+explicit_sources",
            }
        summary["coverage_truth_counts"] = counts
        summary["updated_at_utc"] = payload["updated_at_utc"]
        write_json(SUMMARY_PATH, summary)
    print(json.dumps({k: payload[k] for k in ("status", "date_local", "updated_at_utc", "counts", "gap_examples")}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
