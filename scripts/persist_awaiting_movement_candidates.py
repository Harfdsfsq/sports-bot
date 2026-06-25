from __future__ import annotations

"""Persist candidates that must wait for the next line-movement check.

A value candidate found before the next regular run should not disappear as a
plain reject. It should be stored as awaiting_next_run_movement_check, restored
on the next run and then either published or rejected after the line/value check.

The guarded fallback evaluates candidates after the line guard has already
removed candidates whose first snapshot only needs the next cron recheck.  For
that path, latest-controlled-fallback-report can have evaluated=0 even though
latest-line-movement-guard-report dropped valid lifecycle candidates.  This
script therefore also reconstructs awaiting rows from the line-guard dropped
samples plus the full a-cover/b-cover promotion snapshots.
"""

import json
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(".").resolve()
REPORT = ROOT / ".data" / "exports" / "latest-controlled-fallback-report.json"
LINE_GUARD_REPORT = ROOT / ".data" / "exports" / "latest-line-movement-guard-report.json"
A_COVER_PROMOTION = ROOT / ".data" / "exports" / "latest-a-cover-value-promotion.json"
B_COVER_PROMOTION = ROOT / ".data" / "exports" / "latest-b-cover-value-promotion.json"
RESCUE = ROOT / ".data" / "exports" / "latest-rescue-candidates.json"
STATE = ROOT / ".data" / "candidate-lifecycle-state.json"
OUT = ROOT / ".data" / "exports" / "latest-awaiting-movement-candidates.json"

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


def _num(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


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


def _match_aliases(row: dict[str, Any]) -> set[str]:
    raw = str(row.get("match_key") or row.get("canonical_match_id") or row.get("event_key") or "").strip()
    date = ""
    for value in (row.get("commence_time"), row.get("kickoff"), row.get("kickoff_utc"), row.get("start_time"), raw):
        m = re.search(r"20\d{2}-\d{2}-\d{2}", str(value or ""))
        if m:
            date = m.group(0)
            break
    home = _norm(row.get("home_team") or row.get("home"))
    away = _norm(row.get("away_team") or row.get("away"))
    out = {raw, _norm(raw)}
    if date and home and away:
        out.update({f"{date}|{home}|{away}", f"{date}|{away}|{home}", f"soccer|{home}|{away}|{date}", f"soccer|{away}|{home}|{date}"})
    return {x for x in out if x and x.strip("|")}


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


def _candidate_from_full_row(row: dict[str, Any], guard: dict[str, Any] | None = None) -> dict[str, Any]:
    candidate = dict(row)
    candidate["_candidate_source"] = "awaiting_movement_lifecycle"
    candidate["publication_lifecycle_status"] = "awaiting_next_run_movement_check"
    candidate["awaiting_reason"] = "needs_next_cron_line_movement_recheck"
    candidate["awaiting_created_at_utc"] = datetime.now(UTC).isoformat()
    if guard:
        candidate["line_movement_guard"] = guard
        diagnostics = candidate.setdefault("diagnostics", {})
        if isinstance(diagnostics, dict):
            diagnostics["line_movement_guard"] = guard
            diagnostics["line_movement_lifecycle_status"] = guard.get("line_movement_lifecycle_status")
    candidate["lifecycle_signature"] = _sig(candidate)
    return candidate


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        rows: list[dict[str, Any]] = []
        for key in ("candidates", "items", "rows", "selected_all", "sample", "top_candidates"):
            value = payload.get(key)
            if isinstance(value, list):
                rows.extend([x for x in value if isinstance(x, dict)])
        return rows
    return []


def _index_full_candidates() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in (A_COVER_PROMOTION, B_COVER_PROMOTION, RESCUE):
        rows.extend(_extract_rows(_load_json(path, [])))
    return rows


def _line_guard_awaiting_rows() -> tuple[list[dict[str, Any]], dict[str, int]]:
    report = _load_json(LINE_GUARD_REPORT, {})
    files = report.get("files") if isinstance(report, dict) else []
    full_rows = _index_full_candidates()
    by_alias: dict[str, list[dict[str, Any]]] = {}
    for row in full_rows:
        for alias in _match_aliases(row):
            by_alias.setdefault(alias, []).append(row)
    out: list[dict[str, Any]] = []
    stats = {"dropped_seen": 0, "awaiting_dropped_seen": 0, "matched_full_candidate": 0, "unmatched": 0}
    if not isinstance(files, list):
        return out, stats
    for item in files:
        if not isinstance(item, dict):
            continue
        for dropped in item.get("dropped_sample") or []:
            if not isinstance(dropped, dict):
                continue
            stats["dropped_seen"] += 1
            guard = dropped.get("guard") if isinstance(dropped.get("guard"), dict) else {}
            reasons = [str(x) for x in guard.get("reasons") or []]
            if not any(reason in AWAIT_REASONS for reason in reasons):
                continue
            stats["awaiting_dropped_seen"] += 1
            aliases = _match_aliases(dropped)
            candidates: list[dict[str, Any]] = []
            for alias in aliases:
                candidates.extend(by_alias.get(alias, []))
            # Prefer same selection and close price if there are several candidates on this match.
            sel = _selection_key(dropped)
            price = _num(dropped.get("odds"), 0.0)
            best: dict[str, Any] | None = None
            best_score = -999.0
            for cand in candidates:
                score = 0.0
                if _selection_key(cand) == sel:
                    score += 10.0
                diff = abs(_num(cand.get("odds") or cand.get("selected_odds"), 0.0) - price)
                score -= min(5.0, diff * 10.0)
                if score > best_score:
                    best_score = score
                    best = cand
            if best is None:
                stats["unmatched"] += 1
                continue
            stats["matched_full_candidate"] += 1
            out.append(_candidate_from_full_row(best, guard))
    return out, stats


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
    from_evaluated = 0
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
            from_evaluated += 1
        kept[sig] = candidate

    line_rows, line_stats = _line_guard_awaiting_rows()
    from_line_guard = 0
    for candidate in line_rows:
        kickoff = _parse_dt(candidate.get("commence_time") or candidate.get("kickoff") or candidate.get("start_time"))
        if kickoff is not None and kickoff < now:
            continue
        sig = candidate["lifecycle_signature"]
        if sig not in kept:
            added += 1
            from_line_guard += 1
        kept[sig] = candidate

    awaiting = sorted(kept.values(), key=lambda r: str(r.get("commence_time") or r.get("kickoff") or ""))
    state["awaiting_movement_candidates"] = awaiting
    state["awaiting_movement_updated_at_utc"] = now.isoformat()
    _write_json(STATE, state)

    payload = {
        "status": "ok",
        "created_at_utc": now.isoformat(),
        "source_report": str(REPORT),
        "line_guard_report": str(LINE_GUARD_REPORT),
        "evaluated_seen": len(evaluated),
        "added": added,
        "added_from_evaluated": from_evaluated,
        "added_from_line_guard": from_line_guard,
        "line_guard_stats": line_stats,
        "awaiting_total": len(awaiting),
        "sample": awaiting[:10],
    }
    _write_json(OUT, payload)
    print(json.dumps({"status": "ok", "added": added, "from_line_guard": from_line_guard, "awaiting_total": len(awaiting)}, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
