from __future__ import annotations

"""Build monotonic per-match coverage truth for the day inventory.

This script is intentionally API-free.  It merges all persisted evidence for the
current local day into a strict row-level truth table used by Telegram reports,
controlled fallback diagnostics and day-inventory coverage checks.

Important invariant: for a fixed local day and a stable 300-match inventory,
coverage evidence must not go backwards.  A later run can fail to re-fetch SStats
or Bzzoiro, or process zero matches, but that must not erase lines/context already
seen earlier in the same day.  To enforce this, the script merges:

* current day-inventory row evidence;
* latest-context-source-index runtime evidence;
* progressive_coverage_state evidence;
* a date-scoped high-watermark file written by previous runs.
"""

import csv
import json
import os
import re
from datetime import datetime, timezone
from copy import deepcopy
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
CONTEXT_SOURCE_INDEX_PATH = EXPORT_DIR / "latest-context-source-index.json"
PROGRESSIVE_STATE_PATHS = [
    DAY_INV_DIR / "progressive_coverage_state.json",
    EXPORT_DIR / "latest-progressive-coverage-state.json",
]
HIGHWATER_PATH = DAY_INV_DIR / "coverage_truth_highwater.json"
HIGHWATER_EXPORT_PATH = EXPORT_DIR / "latest-day-inventory-coverage-highwater.json"
FROZEN_ROSTER_EXPORT_PATH = EXPORT_DIR / "latest-day-inventory-frozen-roster.json"

LIVE_ODDS_SOURCES = {"odds_api_io", "bzzoiro", "sportlogic", "allsportsapi", "oddspapi", "bookies_api"}
NON_CONTEXT_SOURCES = {
    "",
    "ensemble",
    "market",
    "market_signal",
    "line_history",
    "odds_api_io",
    "xg_model_context",
    "form_context",
    "candidate_factory",
}


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
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
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or os.getenv("DAY_INVENTORY_CACHE_DATE") or "").strip()
    return explicit or datetime.now(UTC).astimezone(app_tz()).date().isoformat()


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


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


def local_date_from_any(value: Any) -> str:
    dt = parse_dt(value)
    if dt is None:
        return ""
    try:
        return dt.astimezone(app_tz()).date().isoformat()
    except Exception:
        return dt.date().isoformat()


def norm(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    aliases = {
        "oddsapiio": "odds_api_io",
        "odds_api": "odds_api_io",
        "oddsapi": "odds_api_io",
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
        "weatherapi": "weather",
        "weather_api": "weather",
        "open_meteo": "weather",
        "openweather": "weather",
        "openweathermap": "weather",
        "highlightly_fixture": "highlightly",
        "highlightly_context": "highlightly",
        "xg_model": "model_xg",
        "model_xg_context": "model_xg",
    }
    return aliases.get(text, text)


def list_from_any(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k, v in value.items() if str(k).strip() and v not in (None, "", False, [], {})]
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


def row_date(row: dict[str, Any]) -> str:
    for key in ("date_local", "kickoff_utc", "commence_time", "start_time", "kickoff", "kickoff_local"):
        value = row.get(key)
        if not value:
            continue
        if key == "date_local" and re.match(r"^20\d\d-\d\d-\d\d$", str(value)):
            return str(value)
        date = local_date_from_any(value)
        if date:
            return date
    direct = str(row.get("match_key") or row.get("canonical_match_id") or "")
    match = re.search(r"(20\d\d-\d\d-\d\d)", direct)
    return match.group(1) if match else ""


def row_key(row: dict[str, Any]) -> str:
    md = metadata(row)
    for key in (row.get("match_key"), row.get("canonical_match_id"), md.get("match_key"), md.get("canonical_match_id")):
        if str(key or "").strip():
            return str(key).strip()
    home = re.sub(r"[^a-z0-9]+", "_", str(row.get("home_team") or row.get("home") or "").lower()).strip("_")
    away = re.sub(r"[^a-z0-9]+", "_", str(row.get("away_team") or row.get("away") or "").lower()).strip("_")
    d = row_date(row)
    return f"soccer|{home}|{away}|{d}" if home and away and d else ""



def team_key(value: Any) -> str:
    try:
        from app.utils import canonicalize_team_name
        text = canonicalize_team_name(str(value or ""))
    except Exception:
        text = str(value or "").lower()
    text = re.sub(r"[^a-z0-9а-яё]+", " ", text.lower()).strip()
    text = re.sub(r"\s+", " ", text)
    stop = {"fc", "fk", "sc", "cf", "ac", "club", "cd", "de", "da", "del", "if", "bk", "afc", "ii", "b"}
    parts = [part for part in text.split() if part and part not in stop]
    return " ".join(parts).strip()


def generated_match_key_variants(row: dict[str, Any]) -> set[str]:
    d = row_date(row)
    if not d:
        return set()
    home = team_key(row.get("home_team") or row.get("home") or metadata(row).get("home_team"))
    away = team_key(row.get("away_team") or row.get("away") or metadata(row).get("away_team"))
    if not home or not away:
        return set()
    first, second = sorted([home, away])
    return {
        f"{d}|{home}|{away}",
        f"{d}|{away}|{home}",
        f"{d}|{first}|{second}",
        f"soccer|{home}|{away}|{d}",
        f"soccer|{away}|{home}|{d}",
        f"soccer|{first}|{second}|{d}",
    }

def row_key_variants(row: dict[str, Any]) -> set[str]:
    md = metadata(row)
    values = {
        str(row.get("match_key") or "").strip(),
        str(row.get("canonical_match_id") or "").strip(),
        str(md.get("match_key") or "").strip(),
        str(md.get("canonical_match_id") or "").strip(),
    }
    key = row_key(row)
    if key:
        values.add(key)
    # Runtime/progressive state uses app.utils.build_match_key
    # (soccer|sorted_home|sorted_away|YYYY-MM-DD), while day inventory rows often
    # use date|home|away. Include both families every time, even when a direct key
    # exists, otherwise progressive/SStats/Bzzoiro evidence cannot be joined back
    # to the frozen inventory row and coverage appears to drop.
    values |= generated_match_key_variants(row)
    return {v for v in values if v}




def frozen_roster_path(date_local: str) -> Path:
    return DAY_INV_DIR / f"frozen_inventory_roster_{date_local}.json"


def union_lists(*values: Any) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        for item in list_from_any(value):
            key = norm(item) or str(item).strip().lower()
            if not key or key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def merge_dict_evidence(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = deepcopy(base)
    for key, value in incoming.items():
        if key not in merged or merged.get(key) in (None, "", [], {}):
            merged[key] = deepcopy(value)
            continue
        old = merged.get(key)
        if isinstance(old, bool) or isinstance(value, bool):
            merged[key] = bool(old) or bool(value)
        elif isinstance(old, (int, float)) or isinstance(value, (int, float)):
            merged[key] = max(as_int(old), as_int(value))
        elif isinstance(old, (list, tuple, set)) or isinstance(value, (list, tuple, set)):
            merged[key] = union_lists(old, value)
        elif isinstance(old, dict) and isinstance(value, dict):
            merged[key] = merge_dict_evidence(old, value)
        elif str(value or "").strip() and not str(old or "").strip():
            merged[key] = deepcopy(value)
    return merged


def merge_inventory_row_evidence(frozen: dict[str, Any], current: dict[str, Any]) -> dict[str, Any]:
    """Merge evidence from the latest row into the frozen identity row.

    The roster identity (match_key, teams, league, kickoff) remains frozen so the
    denominator and match set are stable. Evidence fields are union/max merged so
    coverage never regresses when a later run sees less data.
    """
    merged = deepcopy(frozen)
    identity_keys = {
        "match_key", "canonical_match_id", "home_team", "away_team", "league_name",
        "competition", "kickoff_utc", "commence_time", "start_time", "kickoff",
        "sport_key", "league_key", "home_team_norm", "away_team_norm", "tier",
    }
    for key, value in current.items():
        if key in identity_keys:
            continue
        if key in {"coverage", "metadata", "source_ids", "refresh", "sources"} and isinstance(value, dict):
            old = merged.get(key) if isinstance(merged.get(key), dict) else {}
            merged[key] = merge_dict_evidence(old, value)
        elif key in {"odds_sources", "line_sources", "context_sources", "context_confirmations", "books", "price_confirmations"}:
            merged[key] = union_lists(merged.get(key), value)
        elif key in {"price_confirmation_sources_count", "price_sources_count", "books_count", "odds_sources_count", "context_sources_count"}:
            merged[key] = max(as_int(merged.get(key)), as_int(value))
        elif key not in merged or merged.get(key) in (None, "", [], {}):
            merged[key] = deepcopy(value)
    return merged


def apply_frozen_roster(inv: dict[str, Any], date_local: str, now_iso: str) -> tuple[dict[str, Any], dict[str, Any]]:
    rows = [row for row in inv.get("matches", []) if isinstance(row, dict)] if isinstance(inv, dict) else []
    max_matches = max(1, as_int(os.getenv("DAY_INVENTORY_TARGET_SIZE") or os.getenv("DAY_INVENTORY_MAX_MATCHES") or "300", 300))
    path = frozen_roster_path(date_local)
    existing = load_json(path, {})
    current_by_key = {row_key(row): row for row in rows if row_key(row)}
    report = {
        "enabled": str(os.getenv("DAY_INVENTORY_FREEZE_ROSTER_ENABLED") or "true").lower() not in {"0", "false", "no", "off"},
        "date_local": date_local,
        "path": str(path),
        "current_rows": len(rows),
        "target_size": max_matches,
        "created": False,
        "restored": False,
        "overlap": 0,
        "lost_from_current": 0,
        "new_in_current": 0,
        "frozen_rows": 0,
    }
    if not report["enabled"] or not rows:
        return inv, report

    min_valid_rows = min(max_matches, max(50, int(max_matches * 0.5)))
    report["min_valid_rows"] = min_valid_rows

    def create_or_repair(reason: str) -> tuple[dict[str, Any], dict[str, Any]]:
        frozen_rows = rows[:max_matches]
        payload = {
            "version": "frozen_day_inventory_roster_v2_min_valid",
            "date_local": date_local,
            "created_at_utc": now_iso,
            "updated_at_utc": now_iso,
            "target_size": max_matches,
            "matches": frozen_rows,
            "repair_reason": reason,
            "notes": [
                "This file freezes the local-day top inventory roster so coverage metrics accumulate over a stable denominator.",
                "Tiny frozen rosters are repaired when a fuller same-day inventory exists; otherwise reports can collapse from 300 matches to only the last few future matches.",
            ],
        }
        write_json(path, payload)
        write_json(FROZEN_ROSTER_EXPORT_PATH, {k: v for k, v in payload.items() if k != "matches"} | {"frozen_rows": len(frozen_rows)})
        inv["matches"] = frozen_rows
        report.update({"created": True, "repaired": reason.startswith("repair_"), "repair_reason": reason, "frozen_rows": len(frozen_rows), "overlap": len(frozen_rows)})
        return inv, report

    if not isinstance(existing, dict) or str(existing.get("date_local") or "") != date_local or not isinstance(existing.get("matches"), list):
        return create_or_repair("create_from_current_inventory")

    frozen_rows = [row for row in existing.get("matches", []) if isinstance(row, dict)]
    # Repair the pathological case introduced by a late-day runtime freeze: the
    # frozen roster can contain only 3 remaining future matches while the real
    # day inventory still has hundreds of same-day rows.  A frozen roster smaller
    # than min_valid_rows is ignored when current rows are clearly fuller.
    if frozen_rows and len(frozen_rows) < min_valid_rows and len(rows) > len(frozen_rows):
        return create_or_repair(f"repair_tiny_existing_roster:{len(frozen_rows)}<{min_valid_rows};current_rows={len(rows)}")
    frozen_by_key = {row_key(row): row for row in frozen_rows if row_key(row)}
    overlap = len(set(frozen_by_key) & set(current_by_key))
    merged_rows: list[dict[str, Any]] = []
    for frozen in frozen_rows[:max_matches]:
        key = row_key(frozen)
        current = current_by_key.get(key)
        merged_rows.append(merge_inventory_row_evidence(frozen, current) if current else deepcopy(frozen))
    # Once a roster exists, do not add new current rows.  Otherwise a later
    # provider/top-ranking reshuffle can silently replace the denominator and
    # make day-level coverage counts go down.  The roster is reset only when the
    # local date changes.
    merged_rows = merged_rows[:max_matches]
    payload = dict(existing)
    payload.update({
        "version": "frozen_day_inventory_roster_v1",
        "date_local": date_local,
        "updated_at_utc": now_iso,
        "target_size": max_matches,
        "matches": merged_rows,
        "last_merge": {
            "current_rows": len(rows),
            "overlap": overlap,
            "lost_from_current": max(0, len(frozen_by_key) - overlap),
            "new_in_current": max(0, len(current_by_key) - overlap),
        },
    })
    write_json(path, payload)
    write_json(FROZEN_ROSTER_EXPORT_PATH, {k: v for k, v in payload.items() if k != "matches"} | {"frozen_rows": len(merged_rows)})
    inv["matches"] = merged_rows
    inv["frozen_roster"] = {
        "date_local": date_local,
        "path": str(path),
        "updated_at_utc": now_iso,
        "rows": len(merged_rows),
        "overlap_with_current_before_restore": overlap,
    }
    report.update({
        "restored": True,
        "frozen_rows": len(merged_rows),
        "overlap": overlap,
        "lost_from_current": max(0, len(frozen_by_key) - overlap),
        "new_in_current": max(0, len(current_by_key) - overlap),
    })
    return inv, report


def count_from_containers(row: dict[str, Any], *keys: str) -> int:
    md = metadata(row)
    cov = coverage(row)
    containers = [row, md, cov]
    for key in ("source_summary", "market_summary", "price_summary", "integrity_report"):
        val = row.get(key)
        if isinstance(val, dict):
            containers.append(val)
    best = 0
    for container in containers:
        for key in keys:
            best = max(best, as_int(container.get(key)))
    return best


def load_context_source_index() -> dict[str, list[str]]:
    payload = load_json(CONTEXT_SOURCE_INDEX_PATH, {})
    if not isinstance(payload, dict):
        return {}
    by_match = payload.get("by_match") if isinstance(payload.get("by_match"), dict) else {}
    out: dict[str, list[str]] = {}
    for key, value in by_match.items():
        if isinstance(key, str) and key.strip():
            out[key] = unique_norm(list_from_any(value))
    return out


def load_progressive_index(date_local: str) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for path in PROGRESSIVE_STATE_PATHS:
        payload = load_json(path, {})
        if not isinstance(payload, dict):
            continue
        state_date = str(payload.get("date_local") or "").strip()
        if state_date and state_date != date_local:
            continue
        matches = payload.get("matches") if isinstance(payload.get("matches"), dict) else {}
        for key, value in matches.items():
            if not isinstance(value, dict):
                continue
            value_with_key = dict(value)
            value_with_key.setdefault("match_key", str(key))
            rd = row_date(value_with_key)
            if rd and rd != date_local:
                continue
            entry = {
                "odds_sources": unique_norm(list_from_any(value.get("odds_sources"))),
                "context_sources": unique_norm(list_from_any(value.get("context_sources"))),
                "price_confirmations": as_int(value.get("price_confirmations") or value.get("price_confirmation_sources_count") or value.get("books_count")),
                "books_count": as_int(value.get("books_count")),
                "has_odds": bool(value.get("odds_sources")) or bool(value.get("has_odds")),
                "has_context": bool(value.get("context_sources")) or bool(value.get("has_context")),
            }
            keys = {str(key)} | row_key_variants(value_with_key)
            if value.get("match_key"):
                keys.add(str(value.get("match_key")))
            if value.get("canonical_match_id"):
                keys.add(str(value.get("canonical_match_id")))
            for k in keys:
                if not k:
                    continue
                cur = out.setdefault(k, {"odds_sources": [], "context_sources": [], "price_confirmations": 0, "books_count": 0, "has_odds": False, "has_context": False})
                cur["odds_sources"] = unique_norm(cur.get("odds_sources", []) + entry["odds_sources"])
                cur["context_sources"] = unique_norm(cur.get("context_sources", []) + entry["context_sources"])
                cur["price_confirmations"] = max(as_int(cur.get("price_confirmations")), entry["price_confirmations"])
                cur["books_count"] = max(as_int(cur.get("books_count")), entry["books_count"])
                cur["has_odds"] = bool(cur.get("has_odds")) or entry["has_odds"]
                cur["has_context"] = bool(cur.get("has_context")) or entry["has_context"]
    return out


def load_highwater(date_local: str) -> dict[str, Any]:
    payload = load_json(HIGHWATER_PATH, {})
    if not isinstance(payload, dict) or str(payload.get("date_local") or "") != date_local:
        return {"version": "coverage_truth_highwater_v1", "date_local": date_local, "rows": {}}
    if not isinstance(payload.get("rows"), dict):
        payload["rows"] = {}
    return payload


def index_sources_for_row(row: dict[str, Any], index: dict[str, list[str]]) -> list[str]:
    found: list[str] = []
    for key in row_key_variants(row):
        found.extend(index.get(key) or [])
    return unique_norm(found)


def progressive_for_row(row: dict[str, Any], index: dict[str, dict[str, Any]]) -> dict[str, Any]:
    merged = {"odds_sources": [], "context_sources": [], "price_confirmations": 0, "books_count": 0, "has_odds": False, "has_context": False}
    for key in row_key_variants(row):
        entry = index.get(key)
        if not isinstance(entry, dict):
            continue
        merged["odds_sources"] = unique_norm(merged["odds_sources"] + list_from_any(entry.get("odds_sources")))
        merged["context_sources"] = unique_norm(merged["context_sources"] + list_from_any(entry.get("context_sources")))
        merged["price_confirmations"] = max(as_int(merged["price_confirmations"]), as_int(entry.get("price_confirmations")))
        merged["books_count"] = max(as_int(merged["books_count"]), as_int(entry.get("books_count")))
        merged["has_odds"] = bool(merged["has_odds"]) or bool(entry.get("has_odds"))
        merged["has_context"] = bool(merged["has_context"]) or bool(entry.get("has_context"))
    return merged


def row_has_bzzoiro_context_hint(row: dict[str, Any]) -> bool:
    md = metadata(row)
    cov = coverage(row)
    if str(row.get("source") or "").lower() == "bzzoiro":
        return True
    if any(bool(md.get(key)) for key in (
        "bzzoiro_context_fields",
        "bzzoiro_has_prediction",
        "bzzoiro_has_context_hint",
        "bzzoiro_context_gap_annotated_at_utc",
        "bzzoiro_line_evidence_context_bridge",
    )):
        return True
    source_ids = row.get("source_ids") if isinstance(row.get("source_ids"), dict) else {}
    provider_ids = md.get("provider_source_ids") if isinstance(md.get("provider_source_ids"), dict) else {}
    has_bzz_id = any(str(k).lower().startswith(("bzzoiro", "bsd")) for k in list(source_ids.keys()) + list(provider_ids.keys()))
    return bool(has_bzz_id and (cov.get("context") or cov.get("xg") or md.get("bzzoiro_raw_source")))


def current_odds_sources(row: dict[str, Any]) -> list[str]:
    cov = coverage(row)
    raw = (
        list_from_any(row.get("odds_sources"))
        + list_from_any(row.get("line_sources"))
        + list_from_any(cov.get("odds_sources"))
        + list_from_any(cov.get("line_sources"))
        + list_from_any(metadata(row).get("odds_sources"))
        + list_from_any(metadata(row).get("line_sources"))
    )
    return sorted({x for x in unique_norm(raw) if x in LIVE_ODDS_SOURCES})


def current_context_sources(row: dict[str, Any], context_index: dict[str, list[str]], progressive: dict[str, Any]) -> list[str]:
    md = metadata(row)
    cov = coverage(row)
    raw = (
        list_from_any(row.get("context_sources"))
        + list_from_any(row.get("context_confirmations"))
        + list_from_any(cov.get("context_sources"))
        + list_from_any(cov.get("all_context_sources"))
        + list_from_any(cov.get("supplemental_context_sources"))
        + list_from_any(md.get("context_sources"))
        + list_from_any(md.get("context_confirmations"))
        + list_from_any(progressive.get("context_sources"))
        + index_sources_for_row(row, context_index)
    )
    if row_has_bzzoiro_context_hint(row):
        raw.append("bzzoiro")
    cleaned: list[str] = []
    for item in unique_norm(raw):
        if item.startswith("provider_"):
            item = item.removeprefix("provider_")
        if item in NON_CONTEXT_SOURCES:
            continue
        if re.match(r"^context_(source|confirmation)_\d+$", item):
            continue
        cleaned.append(item)
    return sorted(set(cleaned))


def price_confirmations(row: dict[str, Any], progressive: dict[str, Any]) -> int:
    return max(
        count_from_containers(row, "price_confirmation_sources_count", "price_sources_count", "books_count", "latest_books_max"),
        len(list_from_any(row.get("price_confirmations"))),
        len(list_from_any(row.get("books"))),
        as_int(progressive.get("price_confirmations")),
    )


def line_movement_status(row: dict[str, Any]) -> str:
    md = metadata(row)
    cov = coverage(row)
    for container in (row, md, cov):
        for key in (
            "line_movement_status",
            "line_movement_lifecycle_status",
            "movement_status",
            "line_guard_status",
            "line_state",
            "movement_lifecycle_status",
        ):
            val = container.get(key) if isinstance(container, dict) else None
            if str(val or "").strip():
                return norm(val)
    return ""


def line_movement_confirmed(row: dict[str, Any]) -> bool:
    status = line_movement_status(row)
    if any(token in status for token in ("confirmed", "passed", "kept", "movement_ok", "publish_now_no_next_cron")):
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


def merge_highwater(truth: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    if not previous:
        return truth
    merged = dict(truth)
    merged["odds_sources"] = sorted(set(list_from_any(truth.get("odds_sources")) + list_from_any(previous.get("odds_sources"))))
    merged["context_sources"] = sorted(set(list_from_any(truth.get("context_sources")) + list_from_any(previous.get("context_sources"))))
    merged["odds_sources_count"] = len(merged["odds_sources"])
    merged["context_sources_count"] = len(merged["context_sources"])
    merged["price_confirmations"] = max(as_int(truth.get("price_confirmations")), as_int(previous.get("price_confirmations")))
    merged["books_count"] = max(as_int(truth.get("books_count")), as_int(previous.get("books_count")))
    merged["has_odds"] = bool(truth.get("has_odds")) or bool(previous.get("has_odds")) or merged["price_confirmations"] > 0 or bool(merged["odds_sources"])
    merged["has_context"] = bool(truth.get("has_context")) or bool(previous.get("has_context")) or bool(merged["context_sources"])
    merged["ready_for_model"] = bool(truth.get("ready_for_model")) or bool(previous.get("ready_for_model")) or (merged["has_odds"] and merged["has_context"])
    return merged


def finalize_truth(row: dict[str, Any], min_odds: int, min_context: int, context_index: dict[str, list[str]], progressive_index: dict[str, dict[str, Any]], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    progressive = progressive_for_row(row, progressive_index)
    osrc = sorted(set(current_odds_sources(row) + [x for x in list_from_any(progressive.get("odds_sources")) if norm(x) in LIVE_ODDS_SOURCES]))
    csrc = current_context_sources(row, context_index, progressive)
    pc = price_confirmations(row, progressive)
    books = max(count_from_containers(row, "books_count"), len(list_from_any(row.get("books"))), as_int(progressive.get("books_count")), pc)
    cov = coverage(row)
    truth: dict[str, Any] = {
        "match_key": row.get("match_key") or row.get("canonical_match_id") or row_key(row),
        "kickoff_utc": row.get("kickoff_utc") or row.get("commence_time") or row.get("kickoff_local") or row.get("start_time") or "",
        "league_name": row.get("league_name") or row.get("competition") or "",
        "home_team": row.get("home_team") or row.get("home") or "",
        "away_team": row.get("away_team") or row.get("away") or "",
        "odds_sources": osrc,
        "odds_sources_count": len(osrc),
        "price_confirmations": pc,
        "books_count": books,
        "context_sources": csrc,
        "context_sources_count": len(csrc),
        "has_odds": bool(cov.get("odds")) or pc > 0 or bool(osrc) or bool(progressive.get("has_odds")),
        "has_context": bool(cov.get("context")) or bool(csrc) or bool(progressive.get("has_context")),
        "ready_for_model": bool(cov.get("ready_for_model")) or ((bool(cov.get("odds")) or pc > 0 or bool(osrc)) and (bool(cov.get("context")) or bool(csrc))),
    }
    if previous:
        truth = merge_highwater(truth, previous)
    missing: list[str] = []
    if truth["price_confirmations"] < min_odds:
        missing.append("price_confirmations")
    if truth["odds_sources_count"] < min_odds:
        missing.append("independent_odds_sources")
    if truth["context_sources_count"] < min_context:
        missing.append("context_sources")
    tier_a_coverage_ready = truth["has_odds"] and truth["has_context"] and truth["price_confirmations"] >= min_odds and truth["odds_sources_count"] >= min_odds and truth["context_sources_count"] >= min_context
    tier_b_coverage_ready = truth["has_odds"] and truth["has_context"] and truth["price_confirmations"] >= 1 and truth["odds_sources_count"] >= 1 and truth["context_sources_count"] >= 1
    movement_ok = line_movement_confirmed(row)
    movement_drop = line_movement_declined(row)
    movement_wait = line_movement_waiting(row)
    if not movement_ok and not movement_drop and (tier_a_coverage_ready or tier_b_coverage_ready):
        movement_wait = True
    truth.update({
        "ready_for_publish": tier_a_coverage_ready and movement_ok,
        "tier_a_coverage_ready": tier_a_coverage_ready,
        "tier_b_coverage_ready": tier_b_coverage_ready,
        "tier_a_publish_ready": tier_a_coverage_ready and movement_ok,
        "tier_b_publish_ready": tier_b_coverage_ready and movement_ok,
        "line_movement_confirmed": movement_ok,
        "line_movement_waiting": movement_wait,
        "line_movement_declined": movement_drop,
        "line_movement_status": line_movement_status(row),
        "need_price_confirmations": max(0, min_odds - truth["price_confirmations"]),
        "need_odds_sources": max(0, min_odds - truth["odds_sources_count"]),
        "need_context_sources": max(0, min_context - truth["context_sources_count"]),
        "missing": missing,
    })
    return truth


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    fields = [
        "match_key", "kickoff_utc", "league_name", "home_team", "away_team",
        "odds_sources_count", "odds_sources", "price_confirmations", "books_count",
        "context_sources_count", "context_sources", "has_odds", "has_context",
        "ready_for_model", "ready_for_publish", "tier_a_coverage_ready", "tier_b_coverage_ready",
        "tier_a_publish_ready", "tier_b_publish_ready", "line_movement_confirmed",
        "line_movement_waiting", "line_movement_declined", "line_movement_status",
        "need_price_confirmations", "need_odds_sources", "need_context_sources", "missing",
    ]
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            flat = dict(row)
            for key in ("odds_sources", "context_sources", "missing"):
                flat[key] = "|".join(str(x) for x in flat.get(key) or [])
            writer.writerow({key: flat.get(key, "") for key in fields})


def update_inventory_rows(inv: dict[str, Any], rows_by_key: dict[str, dict[str, Any]]) -> None:
    matches = inv.get("matches") if isinstance(inv.get("matches"), list) else []
    for row in matches:
        if not isinstance(row, dict):
            continue
        truth = rows_by_key.get(row_key(row))
        if not truth:
            continue
        row["odds_sources"] = truth["odds_sources"]
        row["context_sources"] = truth["context_sources"]
        row["price_confirmation_sources_count"] = truth["price_confirmations"]
        row["books_count"] = max(as_int(row.get("books_count")), truth["books_count"])
        cov = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
        cov.update({
            "odds": bool(truth["has_odds"]),
            "context": bool(truth["has_context"]),
            "ready_for_model": bool(truth["ready_for_model"]),
            "odds_sources": truth["odds_sources"],
            "line_sources": truth["odds_sources"],
            "context_sources": truth["context_sources"],
            "all_context_sources": truth["context_sources"],
            "odds_sources_count": truth["odds_sources_count"],
            "context_sources_count": truth["context_sources_count"],
            "price_confirmation_sources_count": truth["price_confirmations"],
            "books_count": truth["books_count"],
            "ready_for_publish_coverage": bool(truth["tier_a_coverage_ready"]),
            "tier_b_coverage_ready": bool(truth["tier_b_coverage_ready"]),
            "coverage_truth_highwater_applied": True,
        })
        row["coverage"] = cov


def counts_from_rows(rows: list[dict[str, Any]], min_odds: int, min_context: int) -> dict[str, int]:
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
    return counts


def main() -> int:
    now = datetime.now(UTC).isoformat()
    d = target_date()
    min_odds = max(2, as_int(os.getenv("PUBLISH_MIN_ODDS_SOURCES") or os.getenv("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES"), 2))
    min_context = max(2, as_int(os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH"), 2))
    inv_path = DAY_INV_DIR / f"{d}.json"
    inv = load_json(inv_path, {})
    if not isinstance(inv, dict):
        inv = {}
    inv, roster_report = apply_frozen_roster(inv, d, now)
    matches = [row for row in inv.get("matches", []) if isinstance(row, dict)]
    context_index = load_context_source_index()
    progressive_index = load_progressive_index(d)
    highwater = load_highwater(d)
    previous_rows = highwater.get("rows") if isinstance(highwater.get("rows"), dict) else {}

    rows: list[dict[str, Any]] = []
    rows_by_key: dict[str, dict[str, Any]] = {}
    highwater_rows: dict[str, dict[str, Any]] = {}
    for row in matches:
        key = row_key(row)
        previous = previous_rows.get(key) if isinstance(previous_rows.get(key), dict) else None
        truth = finalize_truth(row, min_odds, min_context, context_index, progressive_index, previous)
        rows.append(truth)
        if key:
            rows_by_key[key] = truth
            highwater_rows[key] = {
                "match_key": truth.get("match_key"),
                "kickoff_utc": truth.get("kickoff_utc"),
                "league_name": truth.get("league_name"),
                "home_team": truth.get("home_team"),
                "away_team": truth.get("away_team"),
                "odds_sources": truth.get("odds_sources") or [],
                "context_sources": truth.get("context_sources") or [],
                "price_confirmations": truth.get("price_confirmations") or 0,
                "books_count": truth.get("books_count") or 0,
                "has_odds": bool(truth.get("has_odds")),
                "has_context": bool(truth.get("has_context")),
                "ready_for_model": bool(truth.get("ready_for_model")),
            }
    rows.sort(key=lambda x: (str(x.get("kickoff_utc") or ""), str(x.get("league_name") or ""), str(x.get("home_team") or "")))
    counts = counts_from_rows(rows, min_odds, min_context)
    update_inventory_rows(inv, rows_by_key)
    inv["coverage_truth_highwater_updated_at_utc"] = now
    inv["coverage_truth_highwater_counts"] = counts
    if isinstance(inv.get("counts"), dict):
        inv["counts"]["coverage_truth_highwater_updated_at_utc"] = now
        inv["counts"]["coverage_truth_matches_with_context"] = counts["matches_with_context"]
        inv["counts"]["coverage_truth_matches_with_2plus_context_sources"] = counts["matches_with_2plus_context_sources"]
        inv["counts"]["coverage_truth_matches_ready_for_model"] = counts["matches_ready_for_model"]
    for path in [inv_path, DAY_INV_DIR / "latest.json", DAY_INV_DIR / "current.json", DAY_INV_DIR / "today.json"]:
        write_json(path, inv)

    highwater_payload = {
        "version": "coverage_truth_highwater_v1",
        "date_local": d,
        "updated_at_utc": now,
        "inventory_path": str(inv_path),
        "rows": highwater_rows,
        "counts": counts,
        "sources": {
            "frozen_roster": roster_report,
            "day_inventory_rows": len(matches),
            "context_source_index_rows": len(context_index),
            "progressive_index_rows": len(progressive_index),
            "previous_highwater_rows": len(previous_rows),
        },
    }
    write_json(HIGHWATER_PATH, highwater_payload)
    write_json(HIGHWATER_EXPORT_PATH, highwater_payload)

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
        "frozen_roster": roster_report,
        "highwater": {
            "path": str(HIGHWATER_PATH),
            "export_path": str(HIGHWATER_EXPORT_PATH),
            "sources": highwater_payload["sources"],
        },
        "notes": [
            "Coverage truth is monotonic within one local day: row sources are unioned with progressive_coverage_state and coverage_truth_highwater.json.",
            "odds_sources_count is independent live provider count; price confirmations are bookmaker/line depth and tracked separately.",
            "matches_with_context and matches_with_2plus_context_sources must not decrease during the same local day unless the inventory date changes.",
            "current_by_kickoff_window can shrink when matches start, but day-level coverage truth uses high-watermark evidence.",
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
                "highwater": str(HIGHWATER_PATH),
                "counts": counts,
            }
        summary["coverage_truth_counts"] = counts
        summary["coverage_truth_highwater"] = highwater_payload["sources"]
        summary["frozen_roster"] = roster_report
        summary["updated_at_utc"] = now
        write_json(SUMMARY_PATH, summary)
    print(json.dumps({k: payload[k] for k in ("status", "date_local", "updated_at_utc", "counts", "highwater")}, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
