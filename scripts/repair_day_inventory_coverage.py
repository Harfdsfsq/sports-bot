from __future__ import annotations

"""Repair per-match day inventory coverage from run artifacts.

The accumulator is intentionally fixture-first. This script runs immediately after
accumulation and before priority planning. It projects actual run evidence back
onto `.data/day_inventory/<date>.json` so the inventory does not say every match
needs odds refresh when the current run already had lines/context.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from scripts.day_inventory_aliases import should_update_current_aliases, write_current_aliases

UTC = timezone.utc
ROOT = Path(".").resolve()
EXPORT_DIR = ROOT / ".data" / "exports"
DAY_INV_DIR = ROOT / ".data" / "day_inventory"
LINE_HISTORY_DIR = ROOT / ".data" / "line_history"
OUT_PATH = EXPORT_DIR / "latest-day-inventory-coverage-repair.json"
SUMMARY_PATH = EXPORT_DIR / "latest-day-inventory-summary.json"


def app_tz() -> ZoneInfo:
    name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"
    try:
        return ZoneInfo(name)
    except Exception:
        return ZoneInfo("Europe/Moscow")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def target_date(now: datetime) -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or "").strip()
    if explicit:
        return explicit
    return now.astimezone(app_tz()).date().isoformat()


def compact_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9а-яё]+", "_", str(value or "").lower()).strip("_")


def match_key_from_row(row: dict[str, Any]) -> str:
    direct = str(row.get("match_key") or row.get("canonical_match_id") or "").strip()
    if direct:
        return direct
    home = compact_key(row.get("home_team") or row.get("home") or row.get("homeTeam"))
    away = compact_key(row.get("away_team") or row.get("away") or row.get("awayTeam"))
    dt = parse_dt(row.get("commence_time") or row.get("kickoff_utc") or row.get("start_time") or row.get("date"))
    if not home or not away or dt is None:
        return ""
    teams = sorted([home, away])
    return f"soccer|{teams[0]}|{teams[1]}|{dt.date().isoformat()}"


def truthy_field(row: dict[str, Any], *keys: str) -> bool:
    for key in keys:
        value = row.get(key)
        if value not in (None, "", [], {}):
            return True
    return False


def latest_rows() -> list[dict[str, Any]]:
    for path in (EXPORT_DIR / "latest-matches.json", ROOT / ".data" / "latest-matches.json"):
        payload = load_json(path, None)
        if isinstance(payload, list):
            return [dict(row) for row in payload if isinstance(row, dict)]
        if isinstance(payload, dict):
            for key in ("matches", "rows", "data"):
                rows = payload.get(key)
                if isinstance(rows, list):
                    return [dict(row) for row in rows if isinstance(row, dict)]
    return []


def candidates_from(path: Path) -> list[dict[str, Any]]:
    payload = load_json(path, None)
    if isinstance(payload, list):
        return [dict(row) for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ("candidates", "rows", "data", "top_candidates", "selected_candidates", "published_candidates", "evaluated"):
            rows = payload.get(key)
            if isinstance(rows, list):
                return [dict(row) for row in rows if isinstance(row, dict)]
    return []


def candidate_metrics(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics")
    return metrics if isinstance(metrics, dict) else {}


def candidate_has_odds(row: dict[str, Any]) -> bool:
    metrics = candidate_metrics(row)
    for key in ("odds", "price", "selected_odds"):
        try:
            if float(str(row.get(key) or metrics.get(key) or 0).replace(",", ".")) > 1.0:
                return True
        except Exception:
            pass
    bucket = row.get("raw_bucket_offers")
    if isinstance(bucket, list) and bucket:
        return True
    return False


def candidate_has_context(row: dict[str, Any]) -> bool:
    metrics = candidate_metrics(row)
    if row.get("confirmation_sources") or metrics.get("confirmation_sources"):
        return True
    if metrics.get("xg_sanity") or row.get("source_summary") or row.get("expected_home") or row.get("expected_away"):
        return True
    return False


def evidence_from_latest_matches(now_iso: str) -> dict[str, dict[str, Any]]:
    evidence: dict[str, dict[str, Any]] = {}
    for row in latest_rows():
        key = match_key_from_row(row)
        if not key:
            continue
        item = evidence.setdefault(key, {"odds": False, "context": False, "xg": False, "form": False, "weather": False, "sources": set(), "odds_at": None, "context_at": None})
        item["sources"].add(str(row.get("source") or "latest_matches"))
        has_market_candidate = truthy_field(
            row,
            "forecast_family",
            "forecast_selection",
            "forecast_selection_key",
            "forecast_bookmaker",
            "forecast_odds_source",
            "forecast_model_probability_pct",
            "forecast_adjusted_probability_pct",
            "forecast_market_probability_pct",
            "forecast_publication_score",
        )
        has_context = truthy_field(
            row,
            "forecast_context_source",
            "forecast_expected_home",
            "forecast_expected_away",
            "forecast_total_xg",
            "forecast_context_confidence",
        )
        if has_market_candidate:
            item["odds"] = True
            item["odds_at"] = now_iso
        if has_context:
            item["context"] = True
            item["context_at"] = now_iso
        if truthy_field(row, "forecast_expected_home", "forecast_expected_away", "forecast_total_xg"):
            item["xg"] = True
        if str(row.get("forecast_context_source") or "").strip():
            item["form"] = True
    return evidence


def merge_candidate_evidence(evidence: dict[str, dict[str, Any]], now_iso: str) -> None:
    paths = [
        EXPORT_DIR / "latest-rescue-candidates.json",
        EXPORT_DIR / "latest-candidates-before-quality.json",
        EXPORT_DIR / "latest-candidates-after-quality.json",
        EXPORT_DIR / "latest-candidates.json",
        EXPORT_DIR / "latest-controlled-fallback-report.json",
    ]
    for path in paths:
        for row in candidates_from(path):
            key = str(row.get("match_key") or row.get("canonical_match_id") or "").strip()
            if not key:
                continue
            item = evidence.setdefault(key, {"odds": False, "context": False, "xg": False, "form": False, "weather": False, "sources": set(), "odds_at": None, "context_at": None})
            item["sources"].add(path.name)
            if candidate_has_odds(row):
                item["odds"] = True
                item["odds_at"] = now_iso
            if candidate_has_context(row):
                item["context"] = True
                item["context_at"] = now_iso
            metrics = candidate_metrics(row)
            if metrics.get("xg_sanity") or row.get("expected_home") or row.get("expected_away"):
                item["xg"] = True
            if row.get("confirmation_sources") or metrics.get("confirmation_sources"):
                item["form"] = True


def merge_line_history_evidence(evidence: dict[str, dict[str, Any]], local_date: str) -> None:
    for path in (LINE_HISTORY_DIR / f"{local_date}.json", LINE_HISTORY_DIR / "latest.json"):
        payload = load_json(path, {})
        lines = payload.get("lines") if isinstance(payload, dict) else None
        if not isinstance(lines, dict):
            continue
        for entry in lines.values():
            if not isinstance(entry, dict):
                continue
            snap = entry.get("last_snapshot") if isinstance(entry.get("last_snapshot"), dict) else None
            if not snap:
                continue
            key = str(snap.get("match_key") or "").strip()
            if not key:
                continue
            item = evidence.setdefault(key, {"odds": False, "context": False, "xg": False, "form": False, "weather": False, "sources": set(), "odds_at": None, "context_at": None})
            item["sources"].add("line_history")
            item["odds"] = True
            item["odds_at"] = snap.get("captured_at_utc") or payload.get("updated_at_utc")


def recompute_counts(rows: list[dict[str, Any]], previous: dict[str, Any]) -> dict[str, Any]:
    now = datetime.now(UTC)
    counts = dict(previous or {})
    base = {
        "matches_total": len(rows),
        "matches_with_odds": 0,
        "matches_with_context": 0,
        "matches_with_weather": 0,
        "matches_with_news": 0,
        "matches_with_xg": 0,
        "matches_with_form": 0,
        "matches_ready_for_model": 0,
        "matches_ready_for_publish": 0,
        "matches_next_6h": 0,
        "matches_next_6h_ready": 0,
        "matches_next_12h": 0,
        "matches_next_12h_ready": 0,
    }
    for row in rows:
        coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
        has_odds = bool(coverage.get("odds"))
        has_context = bool(coverage.get("context"))
        ready = bool(coverage.get("ready_for_model")) or (has_odds and has_context)
        base["matches_with_odds"] += int(has_odds)
        base["matches_with_context"] += int(has_context)
        base["matches_with_weather"] += int(bool(coverage.get("weather")))
        base["matches_with_news"] += int(bool(coverage.get("news")))
        base["matches_with_xg"] += int(bool(coverage.get("xg")))
        base["matches_with_form"] += int(bool(coverage.get("form")))
        base["matches_ready_for_model"] += int(ready)
        base["matches_ready_for_publish"] += int(bool(coverage.get("ready_for_publish")))
        kickoff = parse_dt(row.get("kickoff_utc") or row.get("commence_time"))
        if kickoff is None:
            continue
        hours = (kickoff - now).total_seconds() / 3600.0
        if 0 <= hours <= 6:
            base["matches_next_6h"] += 1
            base["matches_next_6h_ready"] += int(ready)
        if 0 <= hours <= 12:
            base["matches_next_12h"] += 1
            base["matches_next_12h_ready"] += int(ready)
    counts.update(base)
    counts["matches_total_high_watermark"] = max(int(counts.get("matches_total_high_watermark") or 0), len(rows), int(base.get("matches_total") or 0))
    return counts


def main() -> int:
    now = datetime.now(UTC)
    now_iso = now.isoformat()
    local_date = target_date(now)
    inventory_path = DAY_INV_DIR / f"{local_date}.json"
    inventory = load_json(inventory_path, {})
    if not isinstance(inventory, dict):
        inventory = {"date_local": local_date, "matches": []}
    rows = [dict(row) for row in inventory.get("matches", []) if isinstance(row, dict)]
    evidence = evidence_from_latest_matches(now_iso)
    merge_candidate_evidence(evidence, now_iso)
    merge_line_history_evidence(evidence, local_date)

    repaired = 0
    for row in rows:
        key = str(row.get("match_key") or row.get("canonical_match_id") or "").strip()
        item = evidence.get(key)
        if not item:
            continue
        coverage = dict(row.get("coverage") or {})
        refresh = dict(row.get("refresh") or {})
        before = json.dumps({"coverage": coverage, "refresh": refresh}, sort_keys=True, ensure_ascii=False)
        if item.get("odds"):
            coverage["odds"] = True
            refresh["last_odds_refresh_utc"] = item.get("odds_at") or now_iso
        if item.get("context"):
            coverage["context"] = True
            refresh["last_context_refresh_utc"] = item.get("context_at") or now_iso
        if item.get("xg"):
            coverage["xg"] = True
        if item.get("form"):
            coverage["form"] = True
        if item.get("weather"):
            coverage["weather"] = True
        coverage["ready_for_model"] = bool(coverage.get("odds")) and bool(coverage.get("context"))
        row["coverage"] = coverage
        row["refresh"] = refresh
        metadata = dict(row.get("metadata") or {})
        metadata["coverage_repair_sources"] = sorted(str(x) for x in item.get("sources", set()))
        row["metadata"] = metadata
        after = json.dumps({"coverage": coverage, "refresh": refresh}, sort_keys=True, ensure_ascii=False)
        repaired += int(before != after)

    counts = recompute_counts(rows, inventory.get("counts") if isinstance(inventory.get("counts"), dict) else {})
    inventory["matches"] = rows
    inventory["counts"] = counts
    inventory["updated_at_utc"] = now_iso
    sources = inventory.setdefault("sources", {})
    if isinstance(sources, dict):
        sources["coverage_repair"] = {
            "updated_at_utc": now_iso,
            "evidence_matches": len(evidence),
            "rows_repaired": repaired,
        }
    write_json(inventory_path, inventory)
    alias_update = write_current_aliases(ROOT, local_date, inventory, write_json)
    summary = {
        "date_local": local_date,
        "updated_at_utc": now_iso,
        "timezone": str(app_tz()),
        "build_status": inventory.get("build_status") or "ok",
        "counts": counts,
        "source_match_counts": dict(inventory.get("source_match_counts") or {}),
        "league_match_counts": dict(inventory.get("league_match_counts") or {}),
        "sources": dict(inventory.get("sources") or {}),
        "alias_update": alias_update,
    }
    if should_update_current_aliases(local_date):
        write_json(SUMMARY_PATH, summary)
    report = {
        "status": "ok",
        "date_local": local_date,
        "updated_at_utc": now_iso,
        "inventory_path": str(inventory_path),
        "summary_path": str(SUMMARY_PATH) if should_update_current_aliases(local_date) else None,
        "alias_update": alias_update,
        "evidence_matches": len(evidence),
        "rows_repaired": repaired,
        "counts": counts,
        "notes": [
            "Coverage is repaired from latest-matches forecast fields, rescue/evaluated candidates, and line_history snapshots.",
            "Priority planner now sees real last_odds_refresh_utc / last_context_refresh_utc instead of treating every row as stale.",
        ],
    }
    write_json(OUT_PATH, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
