from __future__ import annotations

"""Daily operations report wrapper with durable HARIZON ledger support.

The legacy daily report mostly reads archived .logs/state. In GitHub Actions the
real Telegram fallback publications are now persisted in .data/bets/* and export
ledgers. This wrapper patches the legacy report so daily reports, settlement and
auto-learning do not show zeros after real publications.
"""

import importlib.util
import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "scripts" / "build_daily_ops_report.py"
BET_DIR = ROOT / ".data" / "bets"
EXPORT_DIR = ROOT / ".data" / "exports"
STATUS_PATH = EXPORT_DIR / "latest-daily-ops-ledger-wrapper.json"


def _load_original() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_original_daily_ops_report", ORIGINAL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ORIGINAL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _json(path: Path, default: Any) -> Any:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        if not path.exists():
            return rows
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


def _write_status(payload: dict[str, Any]) -> None:
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _key(row: dict[str, Any]) -> str:
    import hashlib
    raw = str(row.get("dedupe_key") or row.get("prediction_id") or row.get("fingerprint") or row.get("id") or "")
    if not raw:
        raw = "|".join(str(row.get(k) or "") for k in ("match_key", "home_team", "away_team", "selection", "point", "published_at_utc", "sent_at"))
    if not raw.strip("|"):
        raw = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _ledger_rows() -> dict[str, list[dict[str, Any]]]:
    paths = {
        "published_bets_jsonl": BET_DIR / "published_bets.jsonl",
        "settled_bets_jsonl": BET_DIR / "settled_bets.jsonl",
        "pending_bets_json": BET_DIR / "pending_bets.json",
        "published_picks_ledger": Path(".data/exports/published-picks-ledger.json"),
        "controlled_fallback_published_ledger": Path(".data/exports/controlled-fallback-published-ledger.json"),
        "published_bets_ledger": Path(".data/exports/published-bets-ledger.json"),
        "latest_controlled_fallback_published": Path(".data/exports/latest-controlled-fallback-published-picks.json"),
        "latest_picks": Path(".data/exports/latest-picks.json"),
        "latest_bets": Path(".data/exports/latest-bets.json"),
    }
    out: dict[str, list[dict[str, Any]]] = {}
    for name, path in paths.items():
        payload = _jsonl(path) if path.suffix == ".jsonl" else _json(path, [])
        if isinstance(payload, dict):
            payload = payload.get("rows") or payload.get("bets") or payload.get("items") or []
        out[name] = [dict(x) for x in payload if isinstance(x, dict)] if isinstance(payload, list) else []
    return out


def _run_rows() -> list[dict[str, Any]]:
    rows = _jsonl(BET_DIR / "run_report_ledger.jsonl")
    # Fallback: if the run ledger is not populated yet, synthesize one run from
    # the latest telegram/run-report payload so daily report never says 0 when a
    # current-day run clearly existed.
    if not rows:
        for path in (EXPORT_DIR / "latest-harizon-telegram-run-report.json", EXPORT_DIR / "latest-run-summary.json"):
            payload = _json(path, {})
            if isinstance(payload, dict) and payload:
                rows.append({
                    "created_at_utc": payload.get("created_at_utc") or payload.get("created_at") or payload.get("updated_at_utc"),
                    "summary": payload.get("summary") if isinstance(payload.get("summary"), dict) else payload.get("coverage") if isinstance(payload.get("coverage"), dict) else {},
                    "source": str(path),
                })
                break
    return rows


def _patch(module: Any) -> dict[str, Any]:
    original_tracked_bets = module.tracked_bets
    original_collect_runs = module.collect_runs
    original_bet_date = module.bet_date
    original_settlement_date = module.settlement_date
    ledger_by_source = _ledger_rows()
    run_ledger = _run_rows()

    def local_date_from_keys(row: dict[str, Any], keys: tuple[str, ...], tz: Any) -> str:
        for key in keys:
            value = row.get(key)
            if value in (None, ""):
                continue
            try:
                date = module.local_date(value, tz)
                if date:
                    return date
            except Exception:
                pass
        return ""

    def bet_date_with_ledger(bet: dict[str, Any], tz: Any) -> str:
        date = local_date_from_keys(bet, (
            "published_at_utc", "published_at", "sent_at", "telegram_sent_at_utc",
            "created_at_utc", "created_at", "publication_created_at_utc",
            "commence_time", "start_time", "kickoff", "kickoff_utc",
        ), tz)
        if date:
            return date
        payload = bet.get("bet_payload") if isinstance(bet.get("bet_payload"), dict) else {}
        if payload:
            date = local_date_from_keys(payload, ("published_at_utc", "sent_at", "created_at_utc", "created_at", "commence_time", "kickoff"), tz)
            if date:
                return date
        try:
            return original_bet_date(bet, tz)
        except Exception:
            return ""

    def settlement_date_with_ledger(bet: dict[str, Any], tz: Any) -> str:
        settlement = bet.get("settlement") if isinstance(bet.get("settlement"), dict) else {}
        date = local_date_from_keys(settlement, ("settled_at", "checked_at", "updated_at", "completed_at"), tz)
        if date:
            return date
        date = local_date_from_keys(bet, ("settled_at", "checked_at", "settlement_checked_at", "completed_at", "updated_at"), tz)
        if date:
            return date
        try:
            return original_settlement_date(bet, tz)
        except Exception:
            return ""

    def normalize_bet_row(row: dict[str, Any], source: str) -> dict[str, Any]:
        out = dict(row)
        out.setdefault("ledger_source", source)
        if out.get("telegram_sent") is True or str(out.get("publication_lifecycle_status") or "").lower() in {"telegram_sent", "sent", "published"}:
            out.setdefault("published", True)
        # Keep already-sent but unsettled rows visible as open/pending exposure for
        # reports. Settlement code can later replace status with won/lost/push/void.
        status = str(out.get("status") or "").strip().lower()
        if status in {"published", "telegram_sent", "sent"} and not isinstance(out.get("settlement"), dict):
            out["status"] = "pending"
        if out.get("stake") is not None and out.get("stake_amount") in (None, ""):
            out["stake_amount"] = out.get("stake")
        if out.get("odds") is None and out.get("selected_odds") is not None:
            out["odds"] = out.get("selected_odds")
        return out

    def tracked_bets_with_ledger() -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            rows.extend([dict(x) for x in original_tracked_bets() if isinstance(x, dict)])
        except Exception:
            pass
        for source, source_rows in ledger_by_source.items():
            rows.extend(normalize_bet_row(row, source) for row in source_rows)
        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            out[_key(row)] = row
        return list(out.values())

    def collect_runs_with_ledger(report_date: str) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        try:
            rows.extend([dict(x) for x in original_collect_runs(report_date) if isinstance(x, dict)])
        except Exception:
            pass
        tz = module.app_tz()
        for item in run_ledger:
            created = item.get("created_at_utc") or item.get("created_at") or item.get("updated_at_utc")
            try:
                if module.local_date(created, tz) != report_date:
                    continue
            except Exception:
                continue
            summary = item.get("summary") if isinstance(item.get("summary"), dict) else {}
            rows.append({"created_at": created, "summary": dict(summary), "_archive_path": "ledger:run_report_ledger", "ledger_source": item.get("source")})
        rows.sort(key=lambda item: str(item.get("created_at") or ""))
        return rows

    module.tracked_bets = tracked_bets_with_ledger
    module.collect_runs = collect_runs_with_ledger
    module.bet_date = bet_date_with_ledger
    module.settlement_date = settlement_date_with_ledger

    return {
        "status": "installed",
        "ledger_sources": {k: len(v) for k, v in ledger_by_source.items()},
        "run_ledger_rows": len(run_ledger),
        "patches": ["tracked_bets", "collect_runs", "bet_date", "settlement_date"],
    }


def main() -> int:
    module = _load_original()
    status = _patch(module)
    _write_status(status)
    return int(module.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
