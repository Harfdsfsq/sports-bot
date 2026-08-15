from __future__ import annotations

import atexit
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
EXPORT_DIR = ROOT / ".data" / "exports"
DAY_DIR = ROOT / ".data" / "day_inventory"
LIVE_ODDS = {"odds_api_io", "bzzoiro", "sportlogic"}


def _enabled(name: str, default: str = "true") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "on", "force"}


def _is_run_once() -> bool:
    argv = " ".join(str(x) for x in sys.argv).lower()
    return "run-once" in argv and ("app.cli" in argv or "cli.py" in argv or "-m" in argv)


def _load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        pass
    return default


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _items(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k).strip() for k in value.keys() if str(k).strip()]
    if isinstance(value, (list, tuple, set)):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in re.split(r"[,|;/]+", value) if x.strip()]
    return []


def _norm_source(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")
    return {
        "oddsapiio": "odds_api_io",
        "odds_api": "odds_api_io",
        "odds_api_io_account1": "odds_api_io",
        "odds_api_io_account2": "odds_api_io",
        "bzzoiro_v2": "bzzoiro",
        "bzzoiro_current_odds": "bzzoiro",
        "sport_logic": "sportlogic",
    }.get(text, text)


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _parse_dt(value: Any):
    try:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        if "T" in text and "+" not in text:
            text += "+00:00"
        dt = datetime.fromisoformat(text)
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc).astimezone(timezone.utc)
    except Exception:
        return None


def _future(row: dict[str, Any]) -> bool:
    dt = _parse_dt(row.get("kickoff_utc") or row.get("commence_time") or row.get("start_time") or row.get("event_date"))
    return True if dt is None else (dt - datetime.now(timezone.utc)).total_seconds() >= -240


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return []
    out: list[dict[str, Any]] = []
    for key in ("matches", "rows", "gap_examples", "gap_sample", "core_gap_sample"):
        value = payload.get(key)
        if isinstance(value, list):
            out.extend(x for x in value if isinstance(x, dict))
        elif isinstance(value, dict):
            out.extend(x for x in value.values() if isinstance(x, dict))
    return out


def _key(row: dict[str, Any]) -> str:
    return str(row.get("match_key") or row.get("canonical_match_id") or "").strip()


def _title_part(value: Any) -> str:
    return " ".join(part.capitalize() for part in str(value or "").replace("_", " ").split())


def _identity_from_key(match_key: str) -> dict[str, str]:
    parts = [p for p in str(match_key or "").split("|") if p]
    date = ""
    teams: list[str] = []
    for part in parts:
        if re.fullmatch(r"20\d{2}-\d{2}-\d{2}", part):
            date = part
        elif part.lower() not in {"soccer", "football", "teams"}:
            teams.append(part)
    return {
        "home_team": _title_part(teams[0]) if len(teams) > 0 else "",
        "away_team": _title_part(teams[1]) if len(teams) > 1 else "",
        "date_local": date,
        "kickoff_utc": f"{date}T12:00:00+00:00" if date else "",
        "sport_key": "soccer",
    }


def _source_values(row: dict[str, Any], *keys: str) -> list[str]:
    out: list[str] = []
    for container in (
        row,
        row.get("coverage") if isinstance(row.get("coverage"), dict) else {},
        row.get("metadata") if isinstance(row.get("metadata"), dict) else {},
        row.get("source_summary") if isinstance(row.get("source_summary"), dict) else {},
    ):
        if not isinstance(container, dict):
            continue
        for key in keys:
            out.extend(_items(container.get(key)))
    return out


def _odds_sources(row: dict[str, Any]) -> set[str]:
    values = _source_values(row, "odds_sources", "line_sources", "core_odds_sources", "independent_odds_sources")
    sources = {_norm_source(x) for x in values if _norm_source(x) in LIVE_ODDS}
    if not sources and (row.get("odds") or row.get("price") or row.get("bookmaker") or row.get("source")):
        sources.add(_norm_source(row.get("source") or row.get("provider") or "odds_api_io"))
    return {x for x in sources if x}


def _context_sources(row: dict[str, Any]) -> set[str]:
    values = _source_values(row, "context_sources", "context_confirmations", "core_context_sources", "confirmation_sources")
    out = set()
    for value in values:
        item = _norm_source(value)
        if item in {"", "market", "ensemble", "odds_api_io", "line_history"}:
            continue
        if re.match(r"^context_(source|confirmation)_\d+$", item):
            continue
        out.add(item)
    return out


def _odds_count(row: dict[str, Any]) -> int:
    return max(len(_odds_sources(row)), _as_int(row.get("odds_source_count") or row.get("odds_sources_count")))


def _context_count(row: dict[str, Any]) -> int:
    return max(len(_context_sources(row)), _as_int(row.get("context_source_count") or row.get("context_sources_count")))


def _needs_bzzoiro(row: dict[str, Any]) -> bool:
    if not isinstance(row, dict) or not _future(row):
        return False
    missing = {str(x).lower() for x in _items(row.get("missing")) + _items(row.get("tier_a_missing"))}
    return (
        _as_int(row.get("core_odds_needed") or row.get("odds_needed")) > 0
        or _as_int(row.get("core_context_needed") or row.get("context_needed")) > 0
        or _odds_count(row) < 2
        or _context_count(row) < 2
        or "independent_odds_sources" in missing
        or "context_sources" in missing
    )


def _identity_index() -> dict[str, dict[str, Any]]:
    idx: dict[str, dict[str, Any]] = {}
    for path in (EXPORT_DIR / "latest-coverage-planner.json", EXPORT_DIR / "latest-matches.json", EXPORT_DIR / "latest-day-inventory-coverage-truth.json", EXPORT_DIR / "latest-match-data-coverage-matches.json"):
        for row in _rows(_load_json(path, {})):
            key = _key(row)
            if not key:
                continue
            dst = idx.setdefault(key, {})
            for field in ("home_team", "away_team", "league_name", "kickoff_utc", "commence_time", "kickoff_local", "sport_key"):
                value = row.get(field)
                if value not in (None, "", [], {}) and not dst.get(field):
                    dst[field] = value
    return idx


def _repair_day_inventory_identity() -> None:
    if not _enabled("HARIZON_DAY_INVENTORY_IDENTITY_REPAIR_ENABLED"):
        return
    idx = _identity_index()
    changed_total = 0
    rows_seen = 0
    for path in (DAY_DIR / "today.json", DAY_DIR / "current.json", DAY_DIR / "latest.json"):
        payload = _load_json(path, {})
        if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
            continue
        changed = 0
        for row in payload.get("matches") or []:
            if not isinstance(row, dict):
                continue
            key = _key(row)
            if not key:
                continue
            rows_seen += 1
            fallback = _identity_from_key(key)
            src = idx.get(key, {})
            before = json.dumps(row, ensure_ascii=False, sort_keys=True)
            row.setdefault("match_key", key)
            row.setdefault("canonical_match_id", key)
            for field in ("home_team", "away_team", "league_name", "kickoff_utc", "commence_time", "kickoff_local", "sport_key"):
                value = src.get(field) or fallback.get(field)
                if value not in (None, "") and row.get(field) in (None, ""):
                    row[field] = value
            if row.get("date_local") in (None, "") and fallback.get("date_local"):
                row["date_local"] = fallback["date_local"]
            if row.get("sport_key") in (None, ""):
                row["sport_key"] = "soccer"
            changed += int(json.dumps(row, ensure_ascii=False, sort_keys=True) != before)
        if changed:
            payload["updated_at_utc"] = datetime.now(timezone.utc).isoformat()
            _write_json(path, payload)
        changed_total += changed
    _write_json(EXPORT_DIR / "latest-day-inventory-bootstrap-identity-repair.json", {"status": "ok", "identity_index_rows": len(idx), "rows_seen": rows_seen, "rows_changed": changed_total, "updated_at_utc": datetime.now(timezone.utc).isoformat()})


def _prime_bzzoiro_source_matrix_plan() -> None:
    if not _enabled("HARIZON_BZZOIRO_V2_SOURCE_MATRIX_BOOTSTRAP_ENABLED"):
        return
    limit = max(1, _as_int(os.getenv("BZZOIRO_SCOPE_TARGET_LIMIT") or os.getenv("BZZOIRO_V2_SOURCE_MATRIX_TARGET_LIMIT") or 300, 300))
    sources = [
        ("progressive_existing", _rows(_load_json(EXPORT_DIR / "latest-progressive-coverage-plan.json", {}))),
        ("coverage_planner", _rows(_load_json(EXPORT_DIR / "latest-coverage-planner.json", {}))),
        ("coverage_truth", _rows(_load_json(EXPORT_DIR / "latest-day-inventory-coverage-truth.json", {}))),
        ("today", _rows(_load_json(DAY_DIR / "today.json", {}))),
        ("current", _rows(_load_json(DAY_DIR / "current.json", {}))),
        ("latest", _rows(_load_json(DAY_DIR / "latest.json", {}))),
    ]
    selected: dict[str, dict[str, Any]] = {}
    counts: dict[str, int] = {}
    for name, rows in sources:
        added = 0
        for row in rows:
            if not _needs_bzzoiro(row):
                continue
            key = _key(row)
            if not key or key in selected:
                continue
            prepared = dict(row)
            fallback = _identity_from_key(key)
            for field in ("home_team", "away_team", "kickoff_utc", "sport_key"):
                if prepared.get(field) in (None, "") and fallback.get(field):
                    prepared[field] = fallback[field]
            odds_sources = _odds_sources(prepared)
            ctx_sources = _context_sources(prepared)
            prepared["core_odds_sources"] = sorted(odds_sources)
            prepared["core_context_sources"] = sorted(ctx_sources)
            prepared["core_odds_needed"] = max(0, 2 - len(odds_sources))
            prepared["core_context_needed"] = max(0, 2 - len(ctx_sources))
            prepared["odds_needed"] = prepared["core_odds_needed"]
            prepared["context_needed"] = prepared["core_context_needed"]
            selected[key] = prepared
            added += 1
            if len(selected) >= limit:
                break
        counts[name] = added
        if len(selected) >= limit:
            break
    rows = list(selected.values())
    payload = {"created_at_utc": datetime.now(timezone.utc).isoformat(), "status": "ok" if rows else "no_gap_rows", "limit": limit, "core_gap_sample": rows, "gap_sample": rows, "source_counts": counts, "sample_keys": list(selected)[:25], "note": "Rows include selector-readable core_*_needed/core_*_sources so Bzzoiro v2 source-matrix can target live inventory gaps."}
    _write_json(EXPORT_DIR / "latest-progressive-coverage-plan.json", payload)
    _write_json(EXPORT_DIR / "latest-bzzoiro-source-matrix-plan-prime.json", payload)


def _sync_publication_ledger_before_cli() -> None:
    if not _enabled("HARIZON_PUBLICATION_LEDGER_BOOTSTRAP_SYNC_ENABLED"):
        return
    try:
        from scripts.sync_publication_ledger import sync_bets
        sync_bets()
    except Exception:
        pass


def _sync_publication_ledger_after_cli() -> None:
    if not _enabled("HARIZON_PUBLICATION_LEDGER_BOOTSTRAP_SYNC_ENABLED"):
        return
    try:
        from scripts.sync_publication_ledger import main as sync_main
        sync_main()
    except Exception:
        pass


def _send_past_predictions_report_after_cli() -> None:
    if not _enabled("PAST_PREDICTIONS_REPORT_AUTOSEND_ENABLED", "false"):
        return
    if not (_enabled("DAILY_REPORT_ENABLED", "false") and not _enabled("PREDICTION_PUBLICATION_ENABLED", "false") and not _enabled("CONTROLLED_FALLBACK_ENABLED", "false")):
        return
    try:
        from scripts import send_past_predictions_report
        old_argv = list(sys.argv)
        sys.argv = ["send_past_predictions_report.py", "--all", "--send-telegram", "--force"]
        try:
            send_past_predictions_report.main()
        finally:
            sys.argv = old_argv
    except Exception:
        pass


def _install_bzzoiro_v2_source_matrix() -> None:
    if not _enabled("HARIZON_BZZOIRO_V2_SOURCE_MATRIX_BOOTSTRAP_ENABLED"):
        return
    _repair_day_inventory_identity()
    _prime_bzzoiro_source_matrix_plan()
    try:
        from app.services.bzzoiro_v2_source_matrix_runtime_patch import install
        install()
    except Exception:
        pass
    try:
        from app.services.bzzoiro_v2_gap_plan_expander_patch import install as install_gap_plan_expander
        install_gap_plan_expander()
    except Exception:
        pass


def _install_odds_api_io_account2_diagnostics() -> None:
    if not _enabled("HARIZON_ODDS_API_IO_ACCOUNT2_DIAGNOSTICS_PATCH_ENABLED"):
        return
    try:
        from scripts.patch_odds_api_io_account2_diagnostics import install
        import app.providers.odds_api_io as odds_api_io
        install(odds_api_io)
    except Exception:
        pass


def _install_zero_raw_candidate_recovery() -> None:
    if not _enabled("HARIZON_ZERO_RAW_CANDIDATE_RECOVERY_ENABLED"):
        return
    try:
        from scripts.patch_zero_raw_candidate_recovery import install
        import app.services.model as model_module
        install(model_module)
    except Exception:
        pass


def _run_bzzoiro_offer_bridge_after_cli() -> None:
    if not _enabled("HARIZON_BZZOIRO_OFFER_OVERLAP_BRIDGE_ENABLED"):
        return
    try:
        from scripts.bridge_bzzoiro_offer_overlap import main as bridge_main
        bridge_main()
    except Exception:
        pass
    try:
        from scripts.repair_bzzoiro_overlap_inventory_sources import main as repair_main
        repair_main()
    except Exception:
        pass


if _is_run_once():
    _sync_publication_ledger_before_cli()
    _install_odds_api_io_account2_diagnostics()
    _install_zero_raw_candidate_recovery()
    _install_bzzoiro_v2_source_matrix()
    atexit.register(_run_bzzoiro_offer_bridge_after_cli)
    atexit.register(_sync_publication_ledger_after_cli)
    atexit.register(_send_past_predictions_report_after_cli)
