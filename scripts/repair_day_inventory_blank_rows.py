"""Remove identity-less rows and rebuild final coverage from exact provider evidence."""

from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(".").resolve()
DAY_DIR = ROOT / ".data" / "day_inventory"
CACHE_DIR = ROOT / ".data" / "cache" / "day_inventory"
EXPORT = ROOT / ".data" / "exports"
OUT = EXPORT / "latest-day-inventory-blank-row-repair.json"
VERIFIED_ODDS = {"odds_api_io", "sstats_pari", "bzzoiro", "allsportsapi", "sportlogic", "bookies_api", "sharpapi", "rapidapi_odds"}


def _tz() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


def _day() -> str:
    raw = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or os.getenv("DAY_INVENTORY_CACHE_DATE") or "").strip()
    return raw[:10] if raw else datetime.now(UTC).astimezone(_tz()).date().isoformat()


def _load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return default


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _has_date(row: dict[str, Any]) -> bool:
    for key in ("kickoff_utc", "commence_time", "start_time", "kickoff", "event_date", "date"):
        if _text(row.get(key)):
            return True
    return any(re.search(r"20\d{2}-\d{2}-\d{2}", _text(row.get(key))) for key in ("match_key", "canonical_match_id", "canonical_match_key", "event_key"))


def _team(row: dict[str, Any], side: str) -> str:
    keys = ("home_team", "home", "home_name", "team_home", "match_home") if side == "home" else ("away_team", "away", "away_name", "team_away", "match_away")
    return next((_text(row.get(key)) for key in keys if _text(row.get(key))), "")


def _valid(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    explicit = _text(row.get("match_key") or row.get("canonical_match_id") or row.get("canonical_match_key") or row.get("event_key"))
    home, away = _team(row, "home"), _team(row, "away")
    return bool((explicit and (home or away or _has_date(row))) or (home and away and _has_date(row)))


def _repair(path: Path) -> dict[str, Any]:
    payload = _load(path, None)
    if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
        return {"path": str(path), "status": "missing_or_no_matches"}
    before = len(payload["matches"])
    payload["matches"] = [row for row in payload["matches"] if _valid(row)]
    removed = before - len(payload["matches"])
    if removed:
        payload["blank_rows_repaired_at_utc"] = datetime.now(UTC).isoformat()
        _write(path, payload)
    return {"path": str(path), "status": "ok", "before": before, "after": len(payload["matches"]), "removed": removed}


def _verified_list(row: dict[str, Any], key: str) -> list[str]:
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    value = metadata.get(key)
    if isinstance(value, dict):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(item) for item in value if str(item).strip()]
    return []


def _patch_truth_functions(truth: Any, cumulative: Any, bridge: Any) -> None:
    bridge.LIVE_ODDS_SOURCES.update(VERIFIED_ODDS)
    truth.LIVE_ODDS_SOURCES.update(VERIFIED_ODDS)
    cumulative.LIVE_ODDS_SOURCES.update(VERIFIED_ODDS)

    original_odds = truth.odds_sources
    original_context = truth.context_sources
    original_price = truth.price_confirmations

    def odds_sources(row: dict[str, Any]) -> list[str]:
        verified = truth.unique_norm(_verified_list(row, "verified_odds_sources"))
        if verified or bool((row.get("coverage") or {}).get("daily_coverage_evidence_synced")):
            return sorted(item for item in verified if item in truth.LIVE_ODDS_SOURCES)
        return original_odds(row)

    def context_sources(row: dict[str, Any]) -> list[str]:
        verified = truth.unique_norm(_verified_list(row, "verified_context_sources"))
        if verified or bool((row.get("coverage") or {}).get("daily_coverage_evidence_synced")):
            return sorted(set(verified))
        return original_context(row)

    def price_confirmations(row: dict[str, Any]) -> int:
        verified = truth.unique_norm(_verified_list(row, "verified_bookmakers"))
        if verified or bool((row.get("coverage") or {}).get("daily_coverage_evidence_synced")):
            return len(verified)
        return original_price(row)

    truth.odds_sources = odds_sources
    truth.context_sources = context_sources
    truth.price_confirmations = price_confirmations

    original_c_odds = cumulative.odds_source_count
    original_c_context = cumulative.context_source_count
    original_c_price = cumulative.price_confirmation_count

    def cumulative_odds(row: dict[str, Any]) -> int:
        verified = {cumulative.norm_source(item) for item in _verified_list(row, "verified_odds_sources")}
        if verified or bool((row.get("coverage") or {}).get("daily_coverage_evidence_synced")):
            return len(verified & cumulative.LIVE_ODDS_SOURCES)
        return original_c_odds(row)

    def cumulative_context(row: dict[str, Any]) -> int:
        verified = {cumulative.norm_source(item) for item in _verified_list(row, "verified_context_sources") if cumulative.norm_source(item)}
        if verified or bool((row.get("coverage") or {}).get("daily_coverage_evidence_synced")):
            return len(verified)
        return original_c_context(row)

    def cumulative_price(row: dict[str, Any]) -> int:
        verified = {str(item).strip().lower() for item in _verified_list(row, "verified_bookmakers") if str(item).strip()}
        if verified or bool((row.get("coverage") or {}).get("daily_coverage_evidence_synced")):
            return len(verified)
        return original_c_price(row)

    cumulative.odds_source_count = cumulative_odds
    cumulative.context_source_count = cumulative_context
    cumulative.price_confirmation_count = cumulative_price


def _install_final_truth_hooks() -> dict[str, Any]:
    result: dict[str, Any] = {}
    try:
        from app.services.strict_inventory_horizon_activation import install as install_horizon

        result["strict_inventory_horizon_activation"] = install_horizon()
    except Exception as exc:
        result["strict_inventory_horizon_activation"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    try:
        from app.services.strict_coverage_inventory_sync import sync

        result["strict_inventory_sync"] = sync()
    except Exception as exc:
        result["strict_inventory_sync"] = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}
    try:
        from scripts import bridge_runtime_context_coverage as bridge
        from scripts import build_day_inventory_coverage_truth as truth
        from scripts import day_inventory_cumulative_coverage as cumulative
        from app.services.strict_coverage_inventory_sync import sync

        _patch_truth_functions(truth, cumulative, bridge)
        result["truth_live_odds_sources"] = sorted(truth.LIVE_ODDS_SOURCES)

        current = cumulative.ensure_latest_run_coverage_merged
        if not getattr(current, "_harizon_verified_strict_truth", False):
            def ensure_latest_run_coverage_merged():
                steps = []
                for name in ("match_data_coverage_report.py", "merge_run_coverage_into_day_inventory.py", "repair_inventory_source_counts.py"):
                    steps.append(cumulative.run_python_script(ROOT / "scripts" / name))
                started = datetime.now(UTC).isoformat()
                try:
                    strict_result = sync()
                    steps.append({"path": "app.services.strict_coverage_inventory_sync.sync", "status": "ok", "started_at_utc": started, "finished_at_utc": datetime.now(UTC).isoformat(), "counts": strict_result.get("counts")})
                except Exception as exc:
                    steps.append({"path": "app.services.strict_coverage_inventory_sync.sync", "status": "error", "error": f"{type(exc).__name__}: {exc}"})
                try:
                    code = truth.main()
                    steps.append({"path": str(ROOT / "scripts" / "build_day_inventory_coverage_truth.py"), "status": "ok" if code in (0, None) else "error", "code": code, "strict_live_odds_sources": sorted(truth.LIVE_ODDS_SOURCES)})
                except Exception as exc:
                    steps.append({"path": str(ROOT / "scripts" / "build_day_inventory_coverage_truth.py"), "status": "error", "error": f"{type(exc).__name__}: {exc}"})
                return steps

            ensure_latest_run_coverage_merged._harizon_verified_strict_truth = True
            cumulative.ensure_latest_run_coverage_merged = ensure_latest_run_coverage_merged
    except Exception as exc:
        result["truth_hook_error"] = f"{type(exc).__name__}: {exc}"
    result["publication_contract_relaxed"] = False
    return result


def main() -> int:
    day = _day()
    results = []
    seen: set[Path] = set()
    for root in (DAY_DIR, CACHE_DIR):
        for path in (root / f"{day}.json", root / "today.json", root / "current.json", root / "latest.json"):
            resolved = path.resolve()
            if resolved not in seen:
                seen.add(resolved)
                results.append(_repair(path))
    hooks = _install_final_truth_hooks()
    payload = {"status": "ok", "created_at_utc": datetime.now(UTC).isoformat(), "date_local": day, "files": results, "total_removed": sum(int(row.get("removed") or 0) for row in results), "verified_coverage_hooks": hooks, "publication_contract_relaxed": False}
    _write(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
