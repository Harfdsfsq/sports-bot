from __future__ import annotations

import json, os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

TIME_BUCKETS = (("0-4h", 0, 4), ("4-8h", 4, 8), ("8-12h", 8, 12), ("12-16h", 12, 16), ("16-20h", 16, 20), ("20-24h", 20, 24), (">24h", 24, None))

@dataclass(frozen=True)
class DailySlaThresholds:
    target_matches: int = 300
    min_odds_sources: int = 2
    min_context_sources: int = 2
    min_books: int = 2
    min_line_snapshots: int = 2
    offer_coverage_warn_pct: float = 0.95
    context_coverage_warn_pct: float = 0.85


def _int(v: Any, d: int = 0) -> int:
    try: return d if v in (None, "") else int(float(str(v).strip()))
    except Exception: return d


def _dt(v: Any) -> datetime | None:
    if isinstance(v, datetime): dt = v
    else:
        s = str(v or "").strip()
        if not s: return None
        try: dt = datetime.fromisoformat(s[:-1] + "+00:00" if s.endswith("Z") else s)
        except ValueError: return None
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


def _src(v: Any) -> set[str]:
    if v in (None, ""): return set()
    if isinstance(v, str): return {p.strip().lower() for p in v.replace(";", ",").replace("|", ",").replace("/", ",").split(",") if p.strip()}
    if isinstance(v, dict):
        out: set[str] = set()
        for k, x in v.items(): out.update({str(k).lower()} if isinstance(x, bool) and x else _src(x))
        return out
    if isinstance(v, (list, tuple, set)):
        out: set[str] = set()
        for x in v: out.update(_src(x))
        return out
    return set()


def _views(r: dict[str, Any]) -> list[dict[str, Any]]:
    return [x for x in (r, r.get("coverage"), r.get("source_summary"), r.get("diagnostics")) if isinstance(x, dict)]


def _count(r: dict[str, Any], nums: tuple[str, ...], names: tuple[str, ...] = ()) -> int:
    return max([0] + [_int(v.get(k), 0) for v in _views(r) for k in nums] + [len(_src(v.get(k))) for v in _views(r) for k in names])


def kickoff_bucket(kickoff: datetime | None, now: datetime | None = None) -> str:
    if kickoff is None: return "unknown"
    now = now or datetime.now(UTC); now = now.replace(tzinfo=UTC) if now.tzinfo is None else now.astimezone(UTC)
    h = (kickoff.astimezone(UTC) - now).total_seconds() / 3600
    if h < 0: return "started"
    return next(label for label, a, b in TIME_BUCKETS if h >= a and (b is None or h < b))


def normalize_inventory_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list): rows = payload
    elif isinstance(payload, dict): rows = next((payload[k] for k in ("matches", "items", "rows", "inventory", "day_inventory", "data", "results") if isinstance(payload.get(k), list)), [])
    else: rows = []
    return [dict(x) for x in rows if isinstance(x, dict)]


def match_coverage_status(row: dict[str, Any], thresholds: DailySlaThresholds | None = None) -> dict[str, Any]:
    t = thresholds or DailySlaThresholds()
    odds = _count(row, ("odds_source_count", "odds_sources_count", "independent_odds_sources_count", "price_sources_count"), ("odds_sources", "price_sources", "sources"))
    ctx = _count(row, ("context_source_count", "context_sources_count", "confirmation_sources_count"), ("context_sources", "confirmation_sources"))
    books = _count(row, ("books_count", "bookmaker_count", "bookmakers_count"), ("books", "bookmakers", "selected_bookmakers"))
    line = _count(row, ("line_snapshots_count", "odds_movement_snapshots_count", "movement_snapshots_count"))
    line_ok = line >= t.min_line_snapshots or any(str(v.get(k, "")).lower() in {"1", "true", "yes", "ok"} for v in _views(row) for k in ("has_line_movement", "line_movement_checked"))
    ko = _dt(row.get("kickoff_utc") or row.get("commence_time") or row.get("start_time") or row.get("event_date") or row.get("date"))
    reasons = []
    if odds < t.min_odds_sources: reasons.append(f"odds_sources:{odds}/{t.min_odds_sources}")
    if ctx < t.min_context_sources: reasons.append(f"context_sources:{ctx}/{t.min_context_sources}")
    if books < t.min_books: reasons.append(f"books:{books}/{t.min_books}")
    if not line_ok: reasons.append(f"line_snapshots:{line}/{t.min_line_snapshots}")
    return {"match_key": row.get("match_key") or row.get("canonical_match_id") or row.get("id"), "kickoff_utc": ko.isoformat() if ko else None, "bucket": kickoff_bucket(ko), "odds_sources_count": odds, "context_sources_count": ctx, "books_count": books, "line_snapshots_count": line, "line_movement_checked": line_ok, "coverage_ready": not reasons, "reasons": reasons}


def build_daily_sla_report(payload: Any, *, thresholds: DailySlaThresholds | None = None, generated_at: datetime | None = None) -> dict[str, Any]:
    t = thresholds or DailySlaThresholds(); rows = [match_coverage_status(r, t) for r in normalize_inventory_rows(payload)]; n = len(rows); pct = lambda x: round(x * 100 / n, 2) if n else 0.0
    odds = sum(r["odds_sources_count"] >= t.min_odds_sources for r in rows); ctx = sum(r["context_sources_count"] >= t.min_context_sources for r in rows); books = sum(r["books_count"] >= t.min_books for r in rows); line = sum(bool(r["line_movement_checked"]) for r in rows); ready = sum(bool(r["coverage_ready"]) for r in rows)
    breaches = ([f"inventory_count:{n}/{t.target_matches}"] if n < t.target_matches else []) + ([f"odds_2plus_pct:{pct(odds):.2f}/{t.offer_coverage_warn_pct * 100:.2f}"] if pct(odds) < t.offer_coverage_warn_pct * 100 else []) + ([f"context_2plus_pct:{pct(ctx):.2f}/{t.context_coverage_warn_pct * 100:.2f}"] if pct(ctx) < t.context_coverage_warn_pct * 100 else [])
    buckets: dict[str, dict[str, int]] = {}
    for r in rows:
        b = buckets.setdefault(str(r["bucket"]), {"matches": 0, "coverage_ready": 0, "odds_ready": 0, "context_ready": 0, "line_movement_ready": 0}); b["matches"] += 1; b["coverage_ready"] += int(r["coverage_ready"]); b["odds_ready"] += int(r["odds_sources_count"] >= t.min_odds_sources); b["context_ready"] += int(r["context_sources_count"] >= t.min_context_sources); b["line_movement_ready"] += int(r["line_movement_checked"])
    return {"generated_at": (generated_at or datetime.now(UTC)).isoformat(), "thresholds": t.__dict__, "summary": {"inventory_count": n, "inventory_target": t.target_matches, "coverage_ready_count": ready, "coverage_ready_pct": pct(ready), "odds_2plus_count": odds, "odds_2plus_pct": pct(odds), "context_2plus_count": ctx, "context_2plus_pct": pct(ctx), "books_2plus_count": books, "books_2plus_pct": pct(books), "line_movement_ready_count": line, "line_movement_ready_pct": pct(line)}, "buckets": {k: buckets[k] for k in sorted(buckets)}, "breaches": breaches, "not_ready_sample": [r for r in rows if not r["coverage_ready"]][:25]}


def thresholds_from_env() -> DailySlaThresholds:
    return DailySlaThresholds(target_matches=max(1, _int(os.getenv("DAY_SLA_TARGET_MATCHES") or os.getenv("DAY_INVENTORY_TARGET_SIZE"), 300)), min_odds_sources=max(1, _int(os.getenv("DAY_SLA_MIN_ODDS_SOURCES") or os.getenv("PUBLISH_MIN_ODDS_SOURCES"), 2)), min_context_sources=max(1, _int(os.getenv("DAY_SLA_MIN_CONTEXT_SOURCES") or os.getenv("PUBLISH_MIN_CONTEXT_SOURCES"), 2)), min_books=max(1, _int(os.getenv("DAY_SLA_MIN_BOOKS") or os.getenv("PUBLISH_MIN_BOOKS"), 2)), min_line_snapshots=max(1, _int(os.getenv("DAY_SLA_MIN_LINE_SNAPSHOTS"), 2)))


def load_json_file(path: str | Path) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))
