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

# Strict-evidence inventory first: only these rows carry explicit provider and
# context identities, which is what evidence_truth needs to see an A-cover row.
A_COVER_INVENTORY_PATHS = (
    EXPORT / "latest-day-inventory-coverage-truth.json",
    ROOT / "artifacts" / "run-bot" / "latest-day-inventory-coverage-truth.json",
    EXPORT / "latest-day-inventory-cumulative-coverage.json",
    ROOT / "artifacts" / "run-bot" / "latest-day-inventory-cumulative-coverage.json",
)

_RETRYABLE_PRICE_SKIPS = {
    "promotion_skip_odds_above_max",
    "promotion_skip_odds_below_min",
    "promotion_skip_price_outlier",
}


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


def _window_hours() -> float:
    """Promotion window must match the publisher window, not a 2h default.

    The promotion step runs in its own process, so it does not see the env layer
    applied by publish_controlled_fallback_guarded_v20.py. Falling back to 2.0
    silently threw away every row kicking off later today.
    """
    return max(
        0.25,
        _as_float(
            os.getenv("PROMOTE_A_COVER_WINDOW_HOURS")
            or os.getenv("CONTROLLED_FALLBACK_PUBLISH_WINDOW_HOURS")
            or os.getenv("PUBLISH_WINDOW_HOURS"),
            24.0,
        ),
    )


def _min_lead_minutes() -> int:
    return _as_int(
        os.getenv("LINE_MOVEMENT_MIN_LEAD_MINUTES")
        or os.getenv("MIN_KICKOFF_LEAD_MINUTES"),
        15,
    )


def _in_active_window(row: dict[str, Any], now: datetime) -> bool:
    kickoff = _kickoff(row)
    if kickoff is None:
        return False
    if kickoff < now + timedelta(minutes=max(0, _min_lead_minutes())):
        return False
    if not _env_bool("PROMOTE_A_COVER_ONLY_PUBLISH_WINDOW", True):
        return True
    return kickoff <= now + timedelta(hours=_window_hours())


def _row_in_fallback_window(row: dict[str, Any], now: datetime) -> bool:
    kickoff = _kickoff(row)
    if kickoff is None:
        return _env_bool("CONTROLLED_FALLBACK_ALLOW_UNKNOWN_TIME", False)
    lead = timedelta(minutes=max(0, _min_lead_minutes()))
    return now + lead <= kickoff <= now + timedelta(hours=_window_hours())


def _source_count(row: dict[str, Any]) -> int:
    """Return strict provider identities, never bookmakers or numeric counters."""

    return int(evidence_truth(row)["odds_sources_count"])


def _is_a_cover(row: dict[str, Any]) -> bool:
    try:
        return bool(evidence_truth(row)["a_cover"])
    except Exception:
        return False


def _count_a_cover(rows: list[dict[str, Any]]) -> int:
    return sum(1 for row in rows if isinstance(row, dict) and _is_a_cover(row))


def _load_inventory_for_a_cover(day: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Pick the inventory source that actually exposes strict A-cover evidence.

    bcover.load_inventory_with_meta ranks sources by B-cover row count, which
    prefers the raw day_inventory file. That file has no per-row provider or
    context identities, so evidence_truth reported a_cover=False for all 232
    rows while the gap report counted 172 A-cover rows from coverage truth.
    """
    diagnostics: dict[str, Any] = {"sources": [], "basis": "strict_a_cover_row_count"}
    if not _env_bool("PROMOTE_A_COVER_PREFER_STRICT_EVIDENCE_INVENTORY", True):
        rows, meta = bcover.load_inventory_with_meta(day)
        diagnostics["selected_by"] = "bcover_loader_forced"
        diagnostics["bcover_inventory_load"] = meta
        diagnostics["selected_rows"] = len(rows)
        diagnostics["selected_a_cover_rows"] = _count_a_cover(rows)
        return rows, diagnostics

    best_key: tuple[int, int] | None = None
    best_path = ""
    best_rows: list[dict[str, Any]] = []
    for path in A_COVER_INVENTORY_PATHS:
        try:
            payload = bcover.load_json(path, None)
            rows = bcover._rows_from_payload(payload)
        except Exception:
            rows = []
        if not rows:
            if path.exists():
                diagnostics["sources"].append({"path": str(path), "rows": 0, "status": "no_rows"})
            continue
        a_cover = _count_a_cover(rows)
        diagnostics["sources"].append(
            {"path": str(path), "rows": len(rows), "a_cover_rows": a_cover}
        )
        key = (a_cover, len(rows))
        if best_key is None or key > best_key:
            best_key = key
            best_path = str(path)
            best_rows = rows

    if best_key is None or best_key[0] <= 0:
        rows, meta = bcover.load_inventory_with_meta(day)
        diagnostics["selected_by"] = "bcover_loader_fallback"
        diagnostics["fallback_reason"] = (
            "no_strict_inventory_rows" if best_key is None else "strict_inventory_has_zero_a_cover"
        )
        diagnostics["bcover_inventory_load"] = meta
        diagnostics["selected_path"] = meta.get("selected_path", "") if isinstance(meta, dict) else ""
        diagnostics["selected_rows"] = len(rows)
        diagnostics["selected_a_cover_rows"] = _count_a_cover(rows)
        return rows, diagnostics

    diagnostics["selected_by"] = "strict_evidence_inventory"
    diagnostics["selected_path"] = best_path
    diagnostics["selected_rows"] = len(best_rows)
    diagnostics["selected_a_cover_rows"] = best_key[0]
    return best_rows, diagnostics


def _price_of(row: dict[str, Any]) -> float | None:
    return bcover.as_price(
        row.get("price")
        or row.get("odds")
        or row.get("decimal_odds")
        or row.get("selected_odds")
    )


def _priced_rows(bucket: dict[str, Any]) -> list[tuple[float, dict[str, Any]]]:
    out: list[tuple[float, dict[str, Any]]] = []
    for row in bucket.get("rows") or []:
        if not isinstance(row, dict):
            continue
        price = _price_of(row)
        if price is not None:
            out.append((price, row))
    return out


def _bucket_from_pairs(pairs: list[tuple[float, dict[str, Any]]]) -> dict[str, Any]:
    books = set()
    for _, row in pairs:
        book = bcover.bookmaker_of(row)
        if book:
            books.add(book)
    return {
        "rows": [row for _, row in pairs],
        "books": books,
        "prices": [price for price, _ in pairs],
    }


def _build_candidate_in_band(
    inv_row: dict[str, Any], bucket_key: str, bucket: dict[str, Any]
) -> tuple[dict[str, Any] | None, str]:
    """Build a candidate from the best publishable price, not the highest one.

    bcover.build_candidate_from_bucket selects max(price) and then rejects the
    whole bucket if that price is outside the global odds band or deviates from
    the bucket median. One exchange or stale leaf therefore killed buckets whose
    remaining prices were fine. Retry with band-filtered prices and, on a median
    outlier, drop the top price and try the next one. All value, deviation and
    band checks stay inside the original builder.
    """
    candidate, reason = bcover.build_candidate_from_bucket(inv_row, bucket_key, bucket)
    if candidate is not None or reason not in _RETRYABLE_PRICE_SKIPS:
        return candidate, reason
    if not _env_bool("PROMOTE_A_COVER_IN_BAND_PRICE_SELECTION", True):
        return candidate, reason

    min_odds = _as_float(os.getenv("CONTROLLED_FALLBACK_GLOBAL_MIN_ODDS"), 1.55)
    max_odds = _as_float(os.getenv("CONTROLLED_FALLBACK_GLOBAL_MAX_ODDS"), 3.05)
    pairs = [pair for pair in _priced_rows(bucket) if min_odds <= pair[0] <= max_odds]
    pairs.sort(key=lambda pair: pair[0], reverse=True)
    if not pairs:
        return None, reason

    max_attempts = max(1, _as_int(os.getenv("PROMOTE_A_COVER_PRICE_RETRY_ATTEMPTS"), 4))
    considered = [price for price, _ in pairs]
    for attempt in range(max_attempts):
        if not pairs:
            break
        retry_candidate, retry_reason = bcover.build_candidate_from_bucket(
            inv_row, bucket_key, _bucket_from_pairs(pairs)
        )
        if retry_candidate is not None:
            reasons = list(retry_candidate.get("reasons") or [])
            reasons.append("price_selection=best_in_band_non_outlier")
            retry_candidate["reasons"] = reasons
            diagnostics = retry_candidate.get("diagnostics")
            diagnostics = dict(diagnostics) if isinstance(diagnostics, dict) else {}
            diagnostics["in_band_price_selection"] = {
                "original_skip_reason": reason,
                "band": [min_odds, max_odds],
                "considered_prices": considered[:20],
                "selected_price": retry_candidate.get("odds"),
                "attempts": attempt + 1,
            }
            retry_candidate["diagnostics"] = diagnostics
            return retry_candidate, "promoted"
        if retry_reason != "promotion_skip_price_outlier":
            return None, retry_reason
        # Highest remaining price is the outlier: drop it and try the next one.
        pairs = pairs[1:]
    return None, reason


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
    inventory, inventory_selection = _load_inventory_for_a_cover(day)
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

    def _sort_key(row: dict[str, Any]) -> tuple[int, int, int, float]:
        truth = evidence_truth(row)
        kickoff = _kickoff(row)
        # Strongest evidence first, then the soonest kickoff so the publisher
        # sees candidates that are still inside its own window.
        return (
            -int(truth["context_sources_count"]),
            -int(truth["books_count"]),
            -int(truth["odds_sources_count"]),
            kickoff.timestamp() if kickoff else float("inf"),
        )

    in_window_rows.sort(key=_sort_key)

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
            candidate, reason = _build_candidate_in_band(row, bucket_key, bucket)
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
        "window_hours": _window_hours(),
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
        "inventory_selection": inventory_selection,
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
                "inventory_selection": (payload.get("inventory_selection") or {}).get("selected_path"),
                "top_reasons": payload.get("reason_counts") or {},
                "error": payload.get("error"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
