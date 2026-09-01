"""Focused Alpha decision board v2 with strict freshness and evidence truth.

The legacy scorer remains the single implementation of conservative utility. This
wrapper restricts inputs to actionable fixtures and repairs cumulative counters into
exact provider/bookmaker identities before scoring.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from app.services.focused_alpha_evidence_truth import repair_candidate_evidence
from scripts import build_focused_alpha_decisions as base

ROOT = base.ROOT
EXPORT = base.EXPORT
OUT = base.OUT
CANDIDATE_PATHS = base.CANDIDATE_PATHS


def _write(payload: Any) -> None:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _parse_dt(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _candidate_kickoff(row: dict[str, Any]) -> datetime | None:
    for key in (
        "commence_time",
        "commence_time_utc",
        "kickoff_utc",
        "kickoff",
        "start_time",
        "start_at",
        "event_time",
    ):
        parsed = _parse_dt(row.get(key))
        if parsed is not None:
            return parsed
    for container_key in ("metadata", "source_summary", "diagnostics"):
        container = row.get(container_key)
        if not isinstance(container, dict):
            continue
        for key in ("commence_time", "kickoff_utc", "kickoff", "start_time"):
            parsed = _parse_dt(container.get(key))
            if parsed is not None:
                return parsed
    return None


def _window_limits() -> tuple[timedelta, timedelta]:
    minimum_minutes = max(
        0.0,
        base._float(os.getenv("MIN_KICKOFF_LEAD_MINUTES"), 20.0),
    )
    horizon_hours = max(
        minimum_minutes / 60.0,
        base._float(
            os.getenv("FOCUSED_ALPHA_CANDIDATE_MAX_HOURS")
            or os.getenv("HARIZON_DATA_COLLECTION_WINDOW_HOURS"),
            36.0,
        ),
    )
    return timedelta(minutes=minimum_minutes), timedelta(hours=horizon_hours)


def _present(value: Any) -> bool:
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple, set, dict)):
        return bool(value)
    return True


def _candidate_completeness(row: dict[str, Any]) -> int:
    repaired = repair_candidate_evidence(row)
    return sum(
        _present(repaired.get(field))
        for field in (
            "odds",
            "adjusted_probability",
            "model_probability",
            "market_probability",
            "confidence",
            "quality_score",
            "ev_pct",
            "edge_pct",
            "confirmation_sources",
            "odds_sources",
            "commence_time",
        )
    )


def collect_candidates(
    *,
    now: datetime | None = None,
    stats: dict[str, int] | None = None,
) -> list[dict[str, Any]]:
    current = now or datetime.now(UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    current = current.astimezone(UTC)
    minimum_lead, maximum_horizon = _window_limits()
    counts = {
        "raw_rows": 0,
        "missing_kickoff": 0,
        "inside_minimum_lead": 0,
        "stale_or_started": 0,
        "outside_data_horizon": 0,
        "eligible_rows": 0,
        "unique_rows": 0,
        "duplicates_collapsed": 0,
        "evidence_truth_repaired": 0,
    }
    best: dict[str, dict[str, Any]] = {}
    for candidate_path in CANDIDATE_PATHS:
        for source_row in base._rows(base._load(candidate_path, {})):
            counts["raw_rows"] += 1
            row = dict(source_row)
            kickoff = _candidate_kickoff(row)
            if kickoff is None:
                counts["missing_kickoff"] += 1
                continue
            delta = kickoff - current
            if delta < timedelta(0):
                counts["stale_or_started"] += 1
                continue
            if delta < minimum_lead:
                counts["inside_minimum_lead"] += 1
                continue
            if delta > maximum_horizon:
                counts["outside_data_horizon"] += 1
                continue
            counts["eligible_rows"] += 1
            row["commence_time"] = kickoff.isoformat()
            row["hours_to_kickoff"] = round(delta.total_seconds() / 3600.0, 6)
            row["_focused_alpha_source_path"] = (
                str(candidate_path.relative_to(ROOT))
                if candidate_path.is_relative_to(ROOT)
                else str(candidate_path)
            )
            row = repair_candidate_evidence(row)
            counts["evidence_truth_repaired"] += 1
            key = base._key(row)
            if not key.strip("|"):
                continue
            current_best = best.get(key)
            # Preserve the earlier, more authoritative candidate path on an exact tie.
            if current_best is None or _candidate_completeness(row) > _candidate_completeness(current_best):
                best[key] = row
    counts["unique_rows"] = len(best)
    counts["duplicates_collapsed"] = max(
        0,
        counts["eligible_rows"] - counts["unique_rows"],
    )
    if stats is not None:
        stats.clear()
        stats.update(counts)
    return list(best.values())


def build_decisions(*, now: datetime | None = None) -> dict[str, Any]:
    history = base.build_history_audit()
    pool_stats: dict[str, int] = {}
    candidates = collect_candidates(now=now, stats=pool_stats)
    scored = [base.score_candidate(row, history) for row in candidates]
    scored.sort(
        key=lambda row: (
            -base._float(row.get("risk_adjusted_utility")),
            -base._float(row.get("conservative_ev_pct")),
            str(row.get("decision_key")),
        )
    )
    maximum = max(
        0,
        min(
            3,
            int(base._float(os.getenv("FOCUSED_ALPHA_DAILY_MAX_DECISIONS"), 2.0)),
        ),
    )
    selected: list[dict[str, Any]] = []
    matches: set[str] = set()
    leagues: set[str] = set()
    for row in scored:
        if not row.get("passes_shadow_contract"):
            continue
        match = base._norm(
            row.get("match_key")
            or f"{row.get('home_team')}|{row.get('away_team')}"
        )
        league = base._norm(row.get("league_name"))
        if match in matches or (league and league in leagues):
            continue
        selected.append(row)
        matches.add(match)
        if league:
            leagues.add(league)
        if len(selected) >= maximum:
            break
    live_enabled = str(
        os.getenv("FOCUSED_ALPHA_LIVE_ENABLED") or "false"
    ).strip().lower() in {"1", "true", "yes", "on"}
    payload = {
        "status": "ok",
        "version": "focused_alpha_decisions_v2_fresh_evidence_truth",
        "created_at_utc": (now or datetime.now(UTC)).astimezone(UTC).isoformat(),
        "mode": (
            "shadow"
            if not live_enabled
            else "live_eligible_but_existing_guards_still_required"
        ),
        "candidate_pool": pool_stats,
        "candidates_seen": len(scored),
        "passes_shadow_contract": sum(
            bool(row.get("passes_shadow_contract")) for row in scored
        ),
        "selected_count": len(selected),
        "daily_max_decisions": maximum,
        "publication_minimum_count": 0,
        "no_bet_is_valid": True,
        "history_live_learning_ready": bool(history.get("live_learning_ready")),
        "thresholds_auto_tuned": False,
        "focused_alpha_live_enabled": live_enabled,
        "selected_shadow": selected,
        "ranked": scored[:100],
        "publication_contract_relaxed": False,
        "evidence_truth_basis": "explicit_provider_and_exact_offer_identities",
    }
    _write(payload)
    return payload


def main() -> int:
    payload = build_decisions()
    print(
        json.dumps(
            {
                "status": payload.get("status"),
                "version": payload.get("version"),
                "mode": payload.get("mode"),
                "candidate_pool": payload.get("candidate_pool"),
                "candidates_seen": payload.get("candidates_seen"),
                "passes_shadow_contract": payload.get("passes_shadow_contract"),
                "selected_count": payload.get("selected_count"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
