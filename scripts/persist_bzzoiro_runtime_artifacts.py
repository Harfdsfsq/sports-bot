from __future__ import annotations

"""Persist usable Bzzoiro runtime artifacts for targeted A-tier matching.

Some production reports already contain Bzzoiro event counts, but the targeted
executor could not see a normalized events artifact. This helper scans known
runtime/export artifacts and the Telegram report payload/text, normalizes any
Bzzoiro events/offers it can find, and writes stable artifacts for downstream
matching.

No publication side effects. It never invents offers; if only aggregate counts
exist, it writes diagnostics only and keeps events empty.
"""

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

EXPORT = Path(".data/exports")
EVENTS_OUT = EXPORT / "latest-bzzoiro-events.json"
ODDS_OUT = EXPORT / "latest-bzzoiro-odds.json"
REPORT_OUT = EXPORT / "latest-bzzoiro-runtime-artifact-persistence.json"


def _load(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except Exception:
        return {} if default is None else default


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [r for r in payload if isinstance(r, dict)]
    if isinstance(payload, dict):
        out: list[dict[str, Any]] = []
        for key in ("events", "matches", "rows", "items", "data", "odds", "offers"):
            val = payload.get(key)
            if isinstance(val, list):
                out.extend([r for r in val if isinstance(r, dict)])
            elif isinstance(val, dict):
                out.extend(_rows(val))
        return out
    return []


def _home(row: dict[str, Any]) -> Any:
    return row.get("home_team") or row.get("home") or row.get("homeName") or row.get("team_home") or row.get("homeTeam")


def _away(row: dict[str, Any]) -> Any:
    return row.get("away_team") or row.get("away") or row.get("awayName") or row.get("team_away") or row.get("awayTeam")


def _offers(row: dict[str, Any]) -> Any:
    return row.get("offers") or row.get("odds") or row.get("markets") or row.get("prices") or row.get("bookmakers") or []


def _offer_count(value: Any) -> int:
    if isinstance(value, list):
        return len(value)
    if isinstance(value, dict):
        return sum(_offer_count(v) for v in value.values()) or len(value)
    return 0


def _extract_from_artifacts() -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    names = [
        "latest-bzzoiro-runtime.json",
        "latest-bzzoiro-events-raw.json",
        "latest-bzzoiro-odds-raw.json",
        "latest-sstats-bzzoiro-odds-merge.json",
        "latest-secondary-provider-matching.json",
        "latest-signal-stack-runtime.json",
        "latest-harizon-telegram-run-report.json",
    ]
    events: list[dict[str, Any]] = []
    odds: list[dict[str, Any]] = []
    scanned: list[str] = []
    for name in names:
        path = EXPORT / name
        if not path.exists():
            continue
        scanned.append(name)
        payload = _load(path, {})
        roots = [payload]
        if isinstance(payload, dict):
            for key in ("bzzoiro", "api", "runtime", "data"):
                if isinstance(payload.get(key), dict):
                    roots.append(payload[key])
        for root in roots:
            for row in _rows(root):
                home, away = _home(row), _away(row)
                if not (home and away):
                    continue
                offer_obj = _offers(row)
                item = {
                    "source": "bzzoiro",
                    "artifact": name,
                    "home_team": home,
                    "away_team": away,
                    "kickoff": row.get("kickoff") or row.get("commence_time") or row.get("start_time") or row.get("date"),
                    "offers": offer_obj,
                    "offer_count": _offer_count(offer_obj),
                    "raw": row,
                }
                events.append(item)
                if item["offer_count"] > 0:
                    odds.append(item)
    return events, odds, scanned


def _aggregate_counts_from_report() -> dict[str, int]:
    text = _read(EXPORT / "latest-harizon-telegram-run-report.txt")
    out = {"events": 0, "secondary_offers": 0, "overlap": 0, "requests": 0, "errors": 0}
    m = re.search(r"bzzoiro: req (\d+), ctx (\d+), events (\d+), secondary offers (\d+), overlap odds-api\.io (\d+), err (\d+)", text, re.I)
    if m:
        out.update({"requests": int(m.group(1)), "events": int(m.group(3)), "secondary_offers": int(m.group(4)), "overlap": int(m.group(5)), "errors": int(m.group(6))})
    return out


def main() -> int:
    events, odds, scanned = _extract_from_artifacts()
    counts = _aggregate_counts_from_report()
    now = datetime.now(UTC).isoformat()
    events_payload = {"created_at_utc": now, "source": "bzzoiro", "events": events, "event_count": len(events), "aggregate_event_count_from_report": counts.get("events", 0), "diagnosis": "normalized_events_persisted" if events else "only_aggregate_report_count_available"}
    odds_payload = {"created_at_utc": now, "source": "bzzoiro", "rows": odds, "offer_rows": len(odds), "offer_count": sum(int(r.get("offer_count") or 0) for r in odds), "aggregate_secondary_offers_from_report": counts.get("secondary_offers", 0), "diagnosis": "normalized_odds_persisted" if odds else "no_usable_offer_rows_found"}
    report = {"status": "ok", "created_at_utc": now, "scanned_artifacts": scanned, "normalized_events": len(events), "normalized_offer_rows": len(odds), "normalized_offer_count": odds_payload["offer_count"], "aggregate_report_counts": counts, "publication_contract_relaxed": False}
    EXPORT.mkdir(parents=True, exist_ok=True)
    EVENTS_OUT.write_text(json.dumps(events_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ODDS_OUT.write_text(json.dumps(odds_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    REPORT_OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
