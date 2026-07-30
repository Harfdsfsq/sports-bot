"""Promote truthful active A-cover rows into guarded fallback review.

This script does not publish. It builds at most a small set of exact-market totals
candidates and keeps the final value, hard-xG, movement, price-integrity, duplicate,
daily-cap and Telegram guards untouched.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.services.focused_alpha_evidence_truth import (
    evidence_truth,
    repair_candidate_evidence,
)
from scripts import build_b_cover_candidate_gap_report as bcover

ROOT = Path(".").resolve()
EXPORT = ROOT / ".data" / "exports"
OUT = EXPORT / "latest-a-cover-value-promotion.json"
RESCUE_PATH = EXPORT / "latest-rescue-candidates.json"
ARTIFACT_RESCUE_PATH = ROOT / "artifacts" / "run-bot" / "latest-rescue-candidates.json"


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _kickoff(row: dict[str, Any]) -> datetime | None:
    for key in ("commence_time", "kickoff_utc", "start_time", "kickoff"):
        parsed = bcover.parse_dt(row.get(key))
        if parsed is not None:
            return parsed
    return None


def _in_active_window(row: dict[str, Any], now: datetime) -> bool:
    kickoff = _kickoff(row)
    if kickoff is None:
        return False
    min_lead = _as_int(
        os.getenv("LINE_MOVEMENT_MIN_LEAD_MINUTES")
        or os.getenv("MIN_KICKOFF_LEAD_MINUTES"),
        15,
    )
    if kickoff < now + timedelta(minutes=max(0, min_lead)):
        return False
    if not _env_bool("PROMOTE_A_COVER_ONLY_PUBLISH_WINDOW", True):
        return True
    hours = max(
        0.25,
        _as_float(
            os.getenv("CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS")
            or os.getenv("PUBLISH_WINDOW_HOURS"),
            2.0,
        ),
    )
    return kickoff <= now + timedelta(hours=hours)


def _row_in_fallback_window(row: dict[str, Any], now: datetime) -> bool:
    kickoff = _kickoff(row)
    if kickoff is None:
        return _env_bool("CONTROLLED_FALLBACK_ALLOW_UNKNOWN_TIME", False)
    min_lead = _as_int(
        os.getenv("LINE_MOVEMENT_MIN_LEAD_MINUTES")
        or os.getenv("MIN_KICKOFF_LEAD_MINUTES"),
        15,
    )
    hours = max(
        0.25,
        _as_float(
            os.getenv("CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS")
            or os.getenv("PUBLISH_WINDOW_HOURS"),
            2.0,
        ),
    )
    return now + timedelta(minutes=max(0, min_lead)) <= kickoff <= now + timedelta(hours=hours)


def _source_count(row: dict[str, Any]) -> int:
    """Return strict provider identities, never bookmakers or numeric counters."""

    return int(evidence_truth(row)["odds_sources_count"])


def _is_a_cover(row: dict[str, Any]) -> bool:
    truth = evidence_truth(row)
    return bool(truth["a_cover"])


def _existing_signatures(rows: list[dict[str, Any]]) -> set[str]:
    return {bcover.candidate_signature(row) for row in rows if isinstance(row, dict)}


def _load_existing_rescue(now: datetime) -> tuple[list[dict[str, Any]], dict[str, int]]:
    rows = bcover.rescue_rows_payload()
    stats = {"loaded": len(rows), "kept": 0, "dropped_outside_window": 0}
    if not _env_bool("PROMOTE_A_COVER_PRUNE_RESCUE_TO_PUBLISH_WINDOW", True):
        stats["kept"] = len(rows)
        return rows, stats
    kept: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _row_in_fallback_window(row, now):
            kept.append(row)
        else:
            stats["dropped_outside_window"] += 1
    stats["kept"] = len(kept)
    return kept, stats


def _clear_stale_artifact_rescue() -> bool:
    try:
        if ARTIFACT_RESCUE_PATH.exists():
            ARTIFACT_RESCUE_PATH.unlink()
            return True
    except Exception:
        return False
    return False


def _tune_candidate(candidate: dict[str, Any], inv_row: dict[str, Any]) -> dict[str, Any]:
    """Replace optimistic coverage counters with exact identities."""

    cand = repair_candidate_evidence(candidate, inventory_row=inv_row)
    truth = evidence_truth(cand, inventory_row=inv_row)
    cand["_candidate_source"] = "a_cover_market_promotion"
    reasons = list(cand.get("reasons") or [])
    reasons.extend(
        (
            "mode=a_cover_market_promotion",
            "evidence_basis=explicit_provider_and_exact_offer_identities",
            (
                "a_cover_sources="
                f"{truth['odds_sources_count']}/books={truth['books_count']}"
                f"/contexts={truth['context_sources_count']}"
            ),
        )
    )
    cand["reasons"] = reasons

    source_summary = cand.get("source_summary") if isinstance(cand.get("source_summary"), dict) else {}
    source_summary = dict(source_summary)
    source_summary["selected_source"] = "a_cover_market_promotion"
    source_summary["publish_coverage_contract"] = {
        "tier": "A-cover-promoted-to-fallback-review" if truth["a_cover"] else "below_A",
        "odds_sources_count": truth["odds_sources_count"],
        "bookmakers_count": truth["books_count"],
        "context_sources_count": truth["context_sources_count"],
        "hard_xg": truth["hard_xg"],
        "basis": "explicit_provider_and_exact_offer_identities",
        "note": "candidate still must pass guarded fallback final checks before Telegram publication",
    }
    cand["source_summary"] = source_summary

    diagnostics = cand.get("diagnostics") if isinstance(cand.get("diagnostics"), dict) else {}
    diagnostics = dict(diagnostics)
    diagnostics["a_cover_promotion"] = {
        "created_by": "promote_a_cover_value_candidates.py",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "inventory_match_key": inv_row.get("match_key"),
        "odds_sources": truth["odds_sources"],
        "odds_sources_count": truth["odds_sources_count"],
        "bookmakers": truth["bookmakers"],
        "books_count": truth["books_count"],
        "context_sources": truth["context_sources"],
        "context_sources_count": truth["context_sources_count"],
        "hard_xg": truth["hard_xg"],
        "a_cover": truth["a_cover"],
    }
    cand["diagnostics"] = diagnostics
    return cand


def _candidate_is_exact_a(candidate: dict[str, Any], inv_row: dict[str, Any]) -> bool:
    return bool(evidence_truth(candidate, inventory_row=inv_row)["a_cover"])


def run() -> dict[str, Any]:
    if not _env_bool("PROMOTE_A_COVER_VALUE_CANDIDATES_ENABLED", True):
        return {"enabled": False, "reason": "disabled"}

    stale_artifact_rescue_removed = _clear_stale_artifact_rescue()
    day = bcover.target_date()
    prebuild = bcover.prebuild_coverage_truth_for_promotion()
    inventory, inventory_load = bcover.load_inventory_with_meta(day)
    now = datetime.now(UTC)
    offer_buckets, offer_diag = bcover.collect_offer_buckets(day)

    existing, existing_stats = _load_existing_rescue(now)
    initial_candidates = bcover.candidate_rows()
    signatures = _existing_signatures(existing + initial_candidates)
    limit = _as_int(os.getenv("PROMOTE_A_COVER_VALUE_CANDIDATE_LIMIT"), 18)
    reasons: Counter[str] = Counter()
    promoted: list[dict[str, Any]] = []
    considered = 0
    active_a_rows = 0
    in_window_rows: list[dict[str, Any]] = []

    for row in inventory:
        if not isinstance(row, dict) or not _is_a_cover(row):
            continue
        active_a_rows += 1
        if not _in_active_window(row, now):
            reasons["promotion_skip_outside_active_publish_window"] += 1
            continue
        in_window_rows.append(row)

    in_window_rows.sort(
        key=lambda row: (
            evidence_truth(row)["context_sources_count"],
            evidence_truth(row)["books_count"],
            evidence_truth(row)["odds_sources_count"],
        ),
        reverse=True,
    )

    for row in in_window_rows:
        considered += 1
        match_buckets: dict[str, dict[str, Any]] = {}
        for key in bcover.fallback_match_keys(row, day):
            match_buckets.update(offer_buckets.get(key, {}))
        if not match_buckets:
            reasons["promotion_skip_no_offer_bucket"] += 1
            continue
        candidates_for_row: list[dict[str, Any]] = []
        for bucket_key, bucket in match_buckets.items():
            candidate, reason = bcover.build_candidate_from_bucket(row, bucket_key, bucket)
            if candidate is None:
                reasons[reason] += 1
                continue
            candidate = _tune_candidate(candidate, row)
            if not _candidate_is_exact_a(candidate, row):
                reasons["promotion_skip_exact_evidence_below_a"] += 1
                continue
            signature = bcover.candidate_signature(candidate)
            if signature in signatures:
                reasons["promotion_skip_duplicate_candidate"] += 1
                continue
            signatures.add(signature)
            candidates_for_row.append(candidate)
        candidates_for_row.sort(
            key=lambda candidate: (
                float(candidate.get("ev_pct") or 0.0),
                float(candidate.get("edge_pct") or 0.0),
                float(candidate.get("confidence") or 0.0),
            ),
            reverse=True,
        )
        for candidate in candidates_for_row[:1]:
            promoted.append(candidate)
            reasons["promoted"] += 1
            if limit and len(promoted) >= limit:
                break
        if limit and len(promoted) >= limit:
            break

    merged = promoted + existing
    _write_json(RESCUE_PATH, merged)
    return {
        "enabled": True,
        "status": "ok",
        "created_at_utc": now.isoformat(),
        "target_date": day,
        "inventory_rows_seen": len(inventory),
        "active_a_cover_rows": active_a_rows,
        "in_publish_window_a_cover_rows": len(in_window_rows),
        "considered_a_cover_rows": considered,
        "promoted_count": len(promoted),
        "reason_counts": dict(reasons.most_common()),
        "sample": promoted[:12],
        "rescue_path": str(RESCUE_PATH),
        "existing_rescue_stats": existing_stats,
        "stale_artifact_rescue_removed": stale_artifact_rescue_removed,
        "offer_diagnostics": offer_diag,
        "inventory_load": inventory_load,
        "prebuild_coverage_truth": prebuild,
        "evidence_truth_basis": "explicit_provider_and_exact_offer_identities",
        "safety_note": (
            "promotion only appends exact A-evidence candidates to fallback review; "
            "guarded publisher still enforces value, hard xG, line recheck, duplicate, "
            "daily cap and price-integrity guards"
        ),
    }


def main() -> int:
    try:
        payload = run()
    except Exception as exc:
        payload = {
            "enabled": True,
            "status": "error",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "error": f"{type(exc).__name__}: {exc}",
            "safety_note": "promotion failed before fallback; no Telegram publication guard was relaxed",
        }
    _write_json(OUT, payload)
    print(
        json.dumps(
            {
                "status": payload.get("status"),
                "active_a_cover_rows": payload.get("active_a_cover_rows"),
                "in_publish_window_a_cover_rows": payload.get("in_publish_window_a_cover_rows"),
                "promoted_count": payload.get("promoted_count"),
                "top_reasons": payload.get("reason_counts") or {},
                "error": payload.get("error"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
