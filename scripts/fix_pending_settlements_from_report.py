from __future__ import annotations

"""Try to close old pending predictions before the manual performance report.

The all-time report can recover historical Telegram publications from sent-index
and Actions artifacts, but many of those rows arrive as status=pending.  This
script runs the normal SettlementService directly over the deduped rows, writes
settled outcomes back to the durable ledger/state/export files, and saves a
clear diagnostic for every still-unsettled pick.
"""

import asyncio
import hashlib
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.services.settlement import SettlementService
from scripts import sync_publication_ledger as ledger

DATA = ROOT / ".data"
EXPORT = DATA / "exports"
PAST_REPORT = EXPORT / "latest-past-predictions-report.json"
OUT = EXPORT / "latest-pending-settlement-fixer.json"

CLOSED = {"won", "half_won", "lost", "half_lost", "push", "void", "cancelled", "canceled", "refunded"}


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
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


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def nested(row: dict[str, Any], key: str) -> dict[str, Any]:
    value = row.get(key)
    return value if isinstance(value, dict) else {}


def first(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def status_of(row: dict[str, Any]) -> str:
    settlement = nested(row, "settlement")
    raw = str(first(settlement.get("outcome"), settlement.get("result"), row.get("status"), row.get("settlement_status"), "pending") or "pending").strip().lower()
    if raw in {"settled", "closed"}:
        outcome = str(first(settlement.get("outcome"), settlement.get("result"), row.get("outcome"), "") or "").strip().lower()
        return outcome if outcome in CLOSED else raw
    return raw if raw in CLOSED else "pending"


def stake_of(row: dict[str, Any]) -> float:
    payload = nested(row, "bet_payload")
    return max(as_float(row.get("stake_amount")), as_float(row.get("stake")), as_float(payload.get("stake_amount")), as_float(payload.get("stake")), 5.0)


def odds_of(row: dict[str, Any]) -> float:
    payload = nested(row, "bet_payload")
    return max(as_float(row.get("odds")), as_float(row.get("selected_odds")), as_float(payload.get("odds")))


def kickoff_of(row: dict[str, Any]) -> datetime | None:
    payload = nested(row, "bet_payload")
    for key in ("commence_time", "kickoff_utc", "kickoff", "start_time"):
        dt = parse_dt(row.get(key)) or parse_dt(payload.get(key))
        if dt is not None:
            return dt
    return None


def row_key(row: dict[str, Any]) -> str:
    for key in ("fingerprint", "prediction_id", "business_dedupe_key", "ledger_semantic_key", "canonical_publication_key"):
        value = str(row.get(key) or "").strip()
        if value:
            return value
    raw = "|".join([
        str(row.get("home_team") or "").lower(),
        str(row.get("away_team") or "").lower(),
        str(row.get("commence_time") or "")[:16],
        str(row.get("family") or row.get("market_family") or "").lower(),
        str(row.get("selection_key") or row.get("selection") or "").lower(),
        str(row.get("point") or ""),
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def normalize_for_settlement(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    payload = nested(out, "bet_payload")
    for key in ("home_team", "away_team", "league_name", "commence_time", "family", "selection", "selection_key", "point", "market_family"):
        if out.get(key) in (None, "") and payload.get(key) not in (None, ""):
            out[key] = payload.get(key)
    if out.get("match_name") and (not out.get("home_team") or not out.get("away_team")):
        parts = str(out.get("match_name") or "").replace("—", "-").split("-", 1)
        if len(parts) == 2:
            out.setdefault("home_team", parts[0].strip())
            out.setdefault("away_team", parts[1].strip())
    out["status"] = "pending"
    out.setdefault("fingerprint", row_key(out))
    out.setdefault("prediction_id", row_key(out))
    out.setdefault("stake_amount", stake_of(out))
    out.setdefault("stake", stake_of(out))
    out.setdefault("odds", odds_of(out))
    out.setdefault("family", out.get("market_family") or "totals")
    if out.get("market_family") in (None, ""):
        out["market_family"] = out.get("family") or "totals"
    if out.get("commence_time") in (None, ""):
        # SettlementService eligibility requires commence_time.  Rows without it
        # stay in diagnostics as missing_commence_time.
        out["commence_time"] = ""
    return out


def compact_pick(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "home_team": row.get("home_team"),
        "away_team": row.get("away_team"),
        "league_name": row.get("league_name"),
        "commence_time": row.get("commence_time"),
        "selection_key": row.get("selection_key"),
        "selection": row.get("selection"),
        "point": row.get("point"),
        "odds": row.get("odds"),
        "status": row.get("status"),
        "fingerprint": row.get("fingerprint"),
    }


def apply_items(rows: list[dict[str, Any]], items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    by_fp = {str(item.get("fingerprint") or ""): item for item in items if str(item.get("fingerprint") or "")}
    updated: list[dict[str, Any]] = []
    applied: list[dict[str, Any]] = []
    for row in rows:
        fp = str(row.get("fingerprint") or row_key(row))
        item = by_fp.get(fp)
        if item is None:
            updated.append(row)
            continue
        out = dict(row)
        outcome = str(item.get("outcome") or "pending").lower()
        out["status"] = outcome
        out["settlement_status"] = "closed" if outcome in CLOSED else "pending"
        out["pnl"] = round(as_float(item.get("pnl")), 2)
        out["settlement"] = {
            "outcome": outcome,
            "pnl": round(as_float(item.get("pnl")), 2),
            "final_home_goals": item.get("final_home_goals"),
            "final_away_goals": item.get("final_away_goals"),
            "score": f"{int(as_float(item.get('final_home_goals')))}:{int(as_float(item.get('final_away_goals')))}" if item.get("final_home_goals") is not None and item.get("final_away_goals") is not None else None,
            "settled_at": item.get("settled_at"),
            "source": item.get("source"),
            "note": item.get("note"),
            "result_orientation": item.get("result_orientation"),
        }
        updated.append(out)
        applied.append({"fingerprint": fp, "outcome": outcome, "pnl": out["pnl"], "source": item.get("source"), "note": item.get("note")})
    return updated, applied


def write_ledger(rows: list[dict[str, Any]]) -> dict[str, Any]:
    existing = ledger.iter_jsonl(ledger.PUBLISHED_JSONL) + ledger.iter_jsonl(ledger.SETTLED_JSONL)
    merged, merge_stats = ledger.merge_by_key(existing, rows)
    pending, pending_stats = ledger.merge_by_key([], [r for r in merged if ledger.is_pending(r)])
    settled, settled_stats = ledger.merge_by_key([], [r for r in merged if not ledger.is_pending(r)])
    ledger.write_jsonl(ledger.PUBLISHED_JSONL, merged)
    ledger.write_json(ledger.PENDING_JSON, pending)
    ledger.write_jsonl(ledger.SETTLED_JSONL, settled)
    ledger.write_json(ledger.EXPORT_DIR / "latest-picks.json", merged)
    ledger.write_json(ledger.EXPORT_DIR / "latest-pending-bets.json", pending)
    ledger.write_json(ledger.EXPORT_DIR / "latest-settled-bets.json", settled)
    state_stats = ledger.mirror_to_state(merged)
    return {
        "published_ledger_rows": len(merged),
        "pending_rows": len(pending),
        "settled_rows": len(settled),
        "duplicates_removed": merge_stats.get("duplicates_removed", 0),
        "pending_duplicates_removed": pending_stats.get("duplicates_removed", 0),
        "settled_duplicates_removed": settled_stats.get("duplicates_removed", 0),
        "state_mirror": state_stats,
    }


async def run_fix() -> dict[str, Any]:
    report = load_json(PAST_REPORT, {})
    report_rows = [dict(r) for r in (report.get("rows") if isinstance(report, dict) else []) or [] if isinstance(r, dict)]
    if not report_rows:
        # Fallback to durable ledger if the all-time report has not been created yet.
        report_rows = [dict(r) for r in (ledger.iter_jsonl(ledger.PUBLISHED_JSONL) + ledger.iter_jsonl(ledger.SETTLED_JSONL)) if isinstance(r, dict)]
    now = datetime.now(UTC)
    normalized = [normalize_for_settlement(r) for r in report_rows]
    pending = []
    skipped: list[dict[str, Any]] = []
    for row in normalized:
        if status_of(row) != "pending":
            continue
        ko = kickoff_of(row)
        if ko is None:
            skipped.append({"reason": "missing_commence_time", **compact_pick(row)})
            continue
        if ko > now - timedelta(minutes=30):
            skipped.append({"reason": "not_old_enough", **compact_pick(row)})
            continue
        pending.append(row)
    settings = Settings()
    service = SettlementService(settings)
    result = await service.settle_pending_bets(pending, now)
    updated_rows, applied = apply_items(normalized, list(result.get("items") or []))
    ledger_stats = write_ledger(updated_rows) if applied else write_ledger(normalized)
    payload = {
        "status": "ok",
        "created_at_utc": now.isoformat(),
        "report_rows_seen": len(report_rows),
        "pending_candidates": len(pending),
        "skipped": skipped[:100],
        "settlement_probe": result,
        "settled_applied": len(applied),
        "applied": applied[:100],
        "ledger": ledger_stats,
    }
    write_json(OUT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return payload


def main() -> int:
    asyncio.run(run_fix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
