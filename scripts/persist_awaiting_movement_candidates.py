from __future__ import annotations

"""Persist controlled-fallback candidates that must wait for the next line check.

According to the HARIZON lifecycle, a value candidate found before the next
regular run should not disappear as a plain reject.  It should be stored as
awaiting_next_run_movement_check, refreshed on the next run and then either
published or rejected after the line/value check.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
REPORT = ROOT / ".data" / "exports" / "latest-controlled-fallback-report.json"
STATE = ROOT / ".data" / "candidate-lifecycle-state.json"
OUT = ROOT / ".data" / "exports" / "latest-awaiting-movement-candidates.json"
UTC = timezone.utc

AWAIT_REASONS = {
    "controlled_fallback_next_regular_run_before_kickoff",
    "controlled_fallback_missing_line_recheck",
    "needs_next_cron_line_movement_recheck",
}


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


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


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = re.sub(r"[^a-z0-9а-я]+", " ", text)
    return " ".join(text.split())


def _point(value: Any) -> str:
    if value in (None, "", "null"):
        return ""
    try:
        f = float(str(value).replace(",", "."))
        return str(int(f)) if f.is_integer() else f"{f:g}"
    except Exception:
        return _norm(value)


def _selection_key(row: dict[str, Any]) -> str:
    explicit = _norm(row.get("selection_key"))
    if explicit in {"under", "over", "home", "away", "draw"}:
        return explicit
    text = _norm(row.get("selection"))
    if any(t in text for t in ("under", "menshe", "меньше", "tm", "тм")):
        return "under"
    if any(t in text for t in ("over", "bolshe", "больше", "tb", "тб")):
        return "over"
    return explicit or text


def _sig(row: dict[str, Any]) -> str:
    match = _norm(row.get("match_key") or row.get("canonical_match_id") or row.get("event_key"))
    if not match:
        match = f"{_norm(row.get('home_team') or row.get('home'))}|{_norm(row.get('away_team') or row.get('away'))}|{str(row.get('commence_time') or row.get('kickoff') or '')[:10]}"
    fam = _norm(row.get("family") or row.get("market_family"))
    return "|".join([match, fam, _selection_key(row), _point(row.get("point") or row.get("line") or row.get("handicap"))])


def _candidate_from_evaluated(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    candidate = dict(row)
    candidate.pop("ok", None)
    candidate.pop("tier", None)
    candidate["_candidate_source"] = "awaiting_movement_lifecycle"
    candidate["publication_lifecycle_status"] = "awaiting_next_run_movement_check"
    candidate["awaiting_reason"] = "controlled_fallback_next_regular_run_before_kickoff"
    candidate["awaiting_created_at_utc"] = datetime.now(UTC).isoformat()
    candidate["odds"] = metrics.get("odds", candidate.get("odds"))
    candidate["selected_odds"] = metrics.get("odds", candidate.get("selected_odds") or candidate.get("odds"))
    candidate["adjusted_probability"] = metrics.get("adjusted_probability", candidate.get("adjusted_probability"))
    candidate["model_probability"] = metrics.get("model_probability", candidate.get("model_probability"))
    candidate["market_probability"] = metrics.get("market_probability", candidate.get("market_probability"))
    candidate["edge_pct"] = metrics.get("canonical_edge_pp", candidate.get("edge_pct"))
    candidate["ev_pct"] = metrics.get("canonical_ev_pct", candidate.get("ev_pct"))
    candidate["quality_score"] = metrics.get("quality_score", candidate.get("quality_score"))
    candidate["confidence"] = metrics.get("confidence", candidate.get("confidence"))
    candidate["books_count"] = metrics.get("books_count", candidate.get("books_count"))
    candidate["confirmation_sources_count"] = metrics.get("confirmation_sources_count", candidate.get("confirmation_sources_count"))
    if metrics.get("confirmation_sources"):
        candidate["confirmation_sources"] = metrics.get("confirmation_sources")
    candidate["lifecycle_signature"] = _sig(candidate)
    return candidate


def main() -> int:
    now = datetime.now(UTC)
    report = _load_json(REPORT, {})
    evaluated = report.get("evaluated") if isinstance(report, dict) else []
    if not isinstance(evaluated, list):
        evaluated = []

    state = _load_json(STATE, {})
    if not isinstance(state, dict):
        state = {}
    current = state.get("awaiting_movement_candidates")
    if not isinstance(current, list):
        current = []

    # Drop expired items, keep upcoming/unknown only for a short safety window.
    kept: dict[str, dict[str, Any]] = {}
    for row in current:
        if not isinstance(row, dict):
            continue
        kickoff = _parse_dt(row.get("commence_time") or row.get("kickoff") or row.get("start_time"))
        created = _parse_dt(row.get("awaiting_created_at_utc") or row.get("created_at") or row.get("created_at_utc"))
        if kickoff is not None and kickoff < now - timedelta(minutes=10):
            continue
        if kickoff is None and created is not None and now - created > timedelta(hours=18):
            continue
        kept[_sig(row)] = row

    added = 0
    for row in evaluated:
        if not isinstance(row, dict):
            continue
        reasons = [str(x) for x in (row.get("reject_reasons") or [])]
        if not any(r in AWAIT_REASONS for r in reasons):
            continue
        kickoff = _parse_dt(row.get("commence_time") or row.get("kickoff") or row.get("start_time"))
        if kickoff is not None and kickoff < now:
            continue
        candidate = _candidate_from_evaluated(row)
        sig = candidate["lifecycle_signature"]
        if sig not in kept:
            added += 1
        kept[sig] = candidate

    awaiting = sorted(kept.values(), key=lambda r: str(r.get("commence_time") or r.get("kickoff") or ""))
    state["awaiting_movement_candidates"] = awaiting
    state["awaiting_movement_updated_at_utc"] = now.isoformat()
    _write_json(STATE, state)

    payload = {
        "status": "ok",
        "created_at_utc": now.isoformat(),
        "source_report": str(REPORT),
        "evaluated_seen": len(evaluated),
        "added": added,
        "awaiting_total": len(awaiting),
        "sample": awaiting[:10],
    }
    _write_json(OUT, payload)
    print(json.dumps({"status": "ok", "added": added, "awaiting_total": len(awaiting)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
