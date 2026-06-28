from __future__ import annotations

"""Daily operations report wrapper with durable HARIZON ledger support.

This wrapper keeps the legacy report renderer, but patches its data readers so
it sees durable .data/bets ledgers and counts unique semantic bets rather than
raw runtime rows.  It also runs publication-ledger sync immediately before the
report, so state settlements from the report-only app.cli run are mirrored back
to .data/bets and daily reports no longer show zero published picks when the
Telegram fallback actually sent forecasts.
"""

import hashlib
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "scripts" / "build_daily_ops_report.py"
BET_DIR = ROOT / ".data" / "bets"
EXPORT_DIR = ROOT / ".data" / "exports"
STATUS_PATH = EXPORT_DIR / "latest-daily-ops-ledger-wrapper.json"
UTC = timezone.utc


def _load_original() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_original_daily_ops_report", ORIGINAL)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {ORIGINAL}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sync_publication_ledger() -> dict[str, Any]:
    try:
        from scripts import sync_publication_ledger

        return sync_publication_ledger.sync_bets()
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


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


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е").replace("—", "-").replace("–", "-")
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return " ".join(text.split())


def _point(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        f = float(str(value).replace(",", "."))
        return str(int(f)) if f.is_integer() else f"{f:.2f}".rstrip("0").rstrip(".")
    except Exception:
        return _norm(value)


def _parse_dt(value: Any) -> datetime | None:
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


def _nested(row: dict[str, Any], name: str) -> dict[str, Any]:
    value = row.get(name)
    return value if isinstance(value, dict) else {}


def _first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def _kickoff(row: dict[str, Any]) -> str:
    payload = _nested(row, "bet_payload")
    value = _first(
        row.get("commence_time"), row.get("kickoff"), row.get("kickoff_utc"), row.get("start_time"),
        payload.get("commence_time"), payload.get("kickoff"), payload.get("start_time"),
    )
    dt = _parse_dt(value)
    if dt:
        return dt.replace(second=0, microsecond=0).isoformat()
    return str(value or "")[:16]


def _selection_key(value: Any) -> str:
    text = _norm(value)
    if any(token in text for token in ("меньше", "under", "тм", "tm")):
        return "under"
    if any(token in text for token in ("больше", "over", "тб", "tb")):
        return "over"
    return text


def _key(row: dict[str, Any]) -> str:
    payload = _nested(row, "bet_payload")
    raw = "|".join([
        _norm(_first(row.get("match_key"), row.get("canonical_match_id"), payload.get("match_key")) or ""),
        _norm(_first(row.get("home_team"), row.get("home"), payload.get("home_team"), payload.get("home")) or ""),
        _norm(_first(row.get("away_team"), row.get("away"), payload.get("away_team"), payload.get("away")) or ""),
        _kickoff(row),
        _norm(_first(row.get("family"), row.get("market_family"), payload.get("family"), payload.get("market_family")) or ""),
        _selection_key(_first(row.get("selection_key"), row.get("selection"), payload.get("selection_key"), payload.get("selection")) or ""),
        _point(_first(row.get("point"), row.get("line"), row.get("handicap"), payload.get("point"), payload.get("line"), payload.get("handicap"))),
    ])
    if raw.strip("|"):
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return hashlib.sha1(json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()


def _as_float(value: Any) -> float:
    try:
        if value in (None, ""):
            return 0.0
        return float(str(value).replace(",", "."))
    except Exception:
        return 0.0


def _row_score(row: dict[str, Any]) -> tuple[int, int, int, float, int]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    stake = max(_as_float(row.get("stake")), _as_float(row.get("stake_amount")), _as_float(_nested(row, "bet_payload").get("stake")), _as_float(_nested(row, "bet_payload").get("stake_amount")))
    sent = 1 if (row.get("telegram_sent") is True or str(row.get("publication_lifecycle_status") or row.get("status") or "").lower() in {"telegram_sent", "published", "sent", "pending", "open", "active", "won", "lost", "push", "void", "half_won", "half_lost"}) else 0
    confirmations = row.get("confirmation_sources") or metrics.get("confirmation_sources") or []
    conf_count = len(confirmations) if isinstance(confirmations, list) else 0
    metric_size = len(json.dumps(metrics, ensure_ascii=False, sort_keys=True)) if metrics else 0
    return (sent, 1 if stake > 0 else 0, conf_count, stake, metric_size)


def _normalize_bet_row(row: dict[str, Any], source: str) -> dict[str, Any]:
    out = dict(row)
    out.setdefault("ledger_source", source)
    status = str(out.get("status") or "").strip().lower()
    if out.get("telegram_sent") is True or str(out.get("publication_lifecycle_status") or "").lower() in {"telegram_sent", "sent", "published"}:
        out.setdefault("published", True)
    if status in {"published", "telegram_sent", "sent"} and not isinstance(out.get("settlement"), dict):
        out["status"] = "pending"
    stake = max(_as_float(out.get("stake")), _as_float(out.get("stake_amount")), _as_float(_nested(out, "bet_payload").get("stake")), _as_float(_nested(out, "bet_payload").get("stake_amount")))
    if stake > 0:
        out["stake"] = stake
        out["stake_amount"] = stake
    if out.get("odds") is None and out.get("selected_odds") is not None:
        out["odds"] = out.get("selected_odds")
    out["ledger_semantic_key"] = _key(out)
    return out


def _dedupe_rows(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    by_key: dict[str, dict[str, Any]] = {}
    duplicates = 0
    for row in rows:
        key = _key(row)
        if key in by_key:
            duplicates += 1
            old = by_key[key]
            base, extra = (row, old) if _row_score(row) >= _row_score(old) else (old, row)
            merged = dict(base)
            for k, v in extra.items():
                if merged.get(k) in (None, "", [], {}, 0) and v not in (None, "", [], {}):
                    merged[k] = v
            stake = max(_as_float(old.get("stake")), _as_float(old.get("stake_amount")), _as_float(row.get("stake")), _as_float(row.get("stake_amount")))
            if stake > 0:
                merged["stake"] = stake
                merged["stake_amount"] = stake
            merged["ledger_semantic_key"] = key
            by_key[key] = merged
        else:
            row = dict(row)
            row["ledger_semantic_key"] = key
            by_key[key] = row
    return list(by_key.values()), duplicates


def _ledger_rows() -> dict[str, list[dict[str, Any]]]:
    paths = {
        "published_bets_jsonl": BET_DIR / "published_bets.jsonl",
        "settled_bets_jsonl": BET_DIR / "settled_bets.jsonl",
        "pending_bets_json": BET_DIR / "pending_bets.json",
        "published_picks_ledger": EXPORT_DIR / "published-picks-ledger.json",
        "controlled_fallback_published_ledger": EXPORT_DIR / "controlled-fallback-published-ledger.json",
        "published_bets_ledger": EXPORT_DIR / "published-bets-ledger.json",
        "latest_controlled_fallback_published": EXPORT_DIR / "latest-controlled-fallback-published-picks.json",
        "latest_picks": EXPORT_DIR / "latest-picks.json",
        "latest_bets": EXPORT_DIR / "latest-bets.json",
    }
    out: dict[str, list[dict[str, Any]]] = {}
    for name, path in paths.items():
        payload = _jsonl(path) if path.suffix == ".jsonl" else _json(path, [])
        if isinstance(payload, dict):
            payload = payload.get("rows") or payload.get("bets") or payload.get("items") or payload.get("pending") or []
        rows = [_normalize_bet_row(dict(x), name) for x in payload if isinstance(x, dict)] if isinstance(payload, list) else []
        out[name] = rows
    return out


def _run_rows() -> list[dict[str, Any]]:
    rows = _jsonl(BET_DIR / "run_report_ledger.jsonl")
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
    duplicate_count = 0

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

    def tracked_bets_with_ledger() -> list[dict[str, Any]]:
        nonlocal duplicate_count
        rows: list[dict[str, Any]] = []
        try:
            rows.extend([dict(x) for x in original_tracked_bets() if isinstance(x, dict)])
        except Exception:
            pass
        for source, source_rows in ledger_by_source.items():
            rows.extend(_normalize_bet_row(row, source) for row in source_rows)
        unique, duplicates = _dedupe_rows(rows)
        duplicate_count = duplicates
        return unique

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

    try:
        unique_preview = tracked_bets_with_ledger()
    except Exception:
        unique_preview = []
    return {
        "status": "installed",
        "ledger_sources": {k: len(v) for k, v in ledger_by_source.items()},
        "run_ledger_rows": len(run_ledger),
        "unique_bets_preview": len(unique_preview),
        "duplicate_bet_rows_ignored": duplicate_count,
        "dedupe_policy": "semantic_match_market_selection_point_kickoff",
        "patches": ["tracked_bets", "collect_runs", "bet_date", "settlement_date"],
    }


def main() -> int:
    ledger_sync = _sync_publication_ledger()
    module = _load_original()
    status = _patch(module)
    status["publication_ledger_sync"] = ledger_sync
    _write_status(status)
    return int(module.main() or 0)


if __name__ == "__main__":
    raise SystemExit(main())
