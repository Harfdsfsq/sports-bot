from __future__ import annotations

import argparse
import json
import math
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _iter_run_paths(runs_root: Path) -> list[Path]:
    if not runs_root.exists():
        return []
    return sorted(runs_root.glob("*/*-run.json"))


def _latest_run_path(runs_root: Path) -> Path | None:
    paths = _iter_run_paths(runs_root)
    return paths[-1] if paths else None


def _selection_kind(value: Any) -> str:
    text = str(value or "").strip().lower()
    if text in {"over", "under", "draw", "home", "away"}:
        return text
    if "больше" in text or text.startswith("over"):
        return "over"
    if "меньше" in text or text.startswith("under"):
        return "under"
    return text


def _match_offers(
    all_offers: list[dict[str, Any]],
    candidate: dict[str, Any],
    point_tol: float = 0.02,
) -> list[dict[str, Any]]:
    match_key = str(candidate.get("match_key") or "")
    family = str(candidate.get("family") or "")
    point = candidate.get("point")
    selection_key = _selection_kind(candidate.get("selection_key") or candidate.get("selection"))
    team_side = str(candidate.get("team_side") or "").strip().lower()

    matched: list[dict[str, Any]] = []
    for offer in all_offers:
        if str(offer.get("match_key") or "") != match_key:
            continue
        if str(offer.get("family") or "") != family:
            continue
        offer_selection = _selection_kind(offer.get("selection"))
        offer_team_side = str(offer.get("team_side") or "").strip().lower()
        offer_point = offer.get("point")
        if selection_key and offer_selection and selection_key != offer_selection:
            continue
        if team_side and offer_team_side and team_side != offer_team_side:
            continue
        if point is not None and offer_point is not None:
            try:
                if abs(float(point) - float(offer_point)) > point_tol:
                    continue
            except Exception:
                pass
        matched.append(dict(offer))
    return matched


def _safe_ratio(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    if b == 0:
        return None
    return a / b


def _candidate_issues(candidate: dict[str, Any], max_gap_pp: float, max_odds_to_fair_ratio: float) -> list[str]:
    issues: list[str] = []

    try:
        odds = float(candidate.get("odds") or 0.0)
    except Exception:
        odds = 0.0
    try:
        implied_probability = float(candidate.get("implied_probability") or 0.0)
    except Exception:
        implied_probability = 0.0
    try:
        fair_odds = float(candidate.get("fair_odds") or 0.0)
    except Exception:
        fair_odds = 0.0
    try:
        market_probability = float(candidate.get("market_probability") or 0.0)
    except Exception:
        market_probability = 0.0

    odds_implied_probability = (1.0 / odds) if odds > 1.0 else None
    if odds_implied_probability is not None:
        gap_pp = abs(implied_probability - odds_implied_probability) * 100.0
        if gap_pp > max_gap_pp:
            issues.append(f"implied_probability_mismatch:{gap_pp:.2f}pp")

    market_fair_odds = (1.0 / market_probability) if market_probability > 0 else None
    ratio = _safe_ratio(odds, fair_odds if fair_odds > 0 else market_fair_odds)
    if ratio is not None and ratio > max_odds_to_fair_ratio:
        issues.append(f"odds_to_fair_ratio_high:{ratio:.3f}")

    source_summary = dict(candidate.get("source_summary") or {})
    selected_price = source_summary.get("selected_price")
    if selected_price is not None:
        try:
            selected_price = float(selected_price)
            if odds > 0 and abs(odds - selected_price) > 0.01:
                issues.append(f"odds_vs_selected_price_mismatch:{abs(odds - selected_price):.3f}")
        except Exception:
            pass

    return issues


def _collect_candidates(run_payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    sections = [
        "candidates_before_quality",
        "candidates",
        "shadow_candidates",
        "candidates_zero_stake",
        "reused_candidates",
        "bet_ledger",
    ]
    out: list[tuple[str, dict[str, Any]]] = []
    for section in sections:
        for item in run_payload.get(section) or []:
            if isinstance(item, dict) and "odds" in item and "match_key" in item:
                out.append((section, dict(item)))
    return out


def build_report(
    runs_root: Path,
    report_path: Path,
    traces_dir: Path,
    latest_only: bool,
    max_gap_pp: float,
    max_odds_to_fair_ratio: float,
    fail_on_published: bool,
) -> tuple[int, dict[str, Any]]:
    run_paths = [_latest_run_path(runs_root)] if latest_only else _iter_run_paths(runs_root)
    run_paths = [path for path in run_paths if path is not None]
    traces_dir.mkdir(parents=True, exist_ok=True)

    suspicious: list[dict[str, Any]] = []
    by_run: list[dict[str, Any]] = []

    for run_path in run_paths:
        run_payload = _load_json(run_path)
        all_offers = [dict(item) for item in (run_payload.get("offers") or []) if isinstance(item, dict)]
        run_issues = 0
        for section, candidate in _collect_candidates(run_payload):
            issues = _candidate_issues(candidate, max_gap_pp=max_gap_pp, max_odds_to_fair_ratio=max_odds_to_fair_ratio)
            if not issues:
                continue
            offers = _match_offers(all_offers, candidate)
            row = {
                "run_path": str(run_path),
                "created_at": str(run_payload.get("created_at") or ""),
                "section": section,
                "match_key": candidate.get("match_key"),
                "league_name": candidate.get("league_name"),
                "home_team": candidate.get("home_team"),
                "away_team": candidate.get("away_team"),
                "family": candidate.get("family"),
                "selection": candidate.get("selection"),
                "selection_key": candidate.get("selection_key"),
                "point": candidate.get("point"),
                "odds": candidate.get("odds"),
                "fair_odds": candidate.get("fair_odds"),
                "implied_probability": candidate.get("implied_probability"),
                "market_probability": candidate.get("market_probability"),
                "consensus_probability": candidate.get("consensus_probability"),
                "books_count": candidate.get("books_count"),
                "sources_count": candidate.get("sources_count"),
                "issues": issues,
                "source_summary": candidate.get("source_summary") or {},
                "diagnostics": candidate.get("diagnostics") or {},
                "matched_offers": offers,
            }
            suspicious.append(row)
            run_issues += 1

            trace_name = (
                f"{Path(run_path).stem}__{candidate.get('family','unknown')}__"
                f"{str(candidate.get('home_team','')).replace(' ','_')}__"
                f"{str(candidate.get('away_team','')).replace(' ','_')}__"
                f"{str(candidate.get('selection_key') or candidate.get('selection') or 'selection').replace(' ','_')}.json"
            )
            (traces_dir / trace_name).write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")

        by_run.append({
            "run_path": str(run_path),
            "created_at": str(run_payload.get("created_at") or ""),
            "issues_found": run_issues,
            "published": int((run_payload.get("summary") or {}).get("published") or 0),
        })

    published_issues = [
        item for item in suspicious
        if item.get("section") in {"candidates", "bet_ledger"} or
        str((item.get("source_summary") or {}).get("quality_status") or "").startswith("passed")
    ]

    report = {
        "created_at": datetime.utcnow().isoformat() + "Z",
        "runs_root": str(runs_root),
        "latest_only": latest_only,
        "max_gap_pp": max_gap_pp,
        "max_odds_to_fair_ratio": max_odds_to_fair_ratio,
        "summary": {
            "runs_checked": len(run_paths),
            "suspicious_candidates": len(suspicious),
            "published_or_passed_suspicious_candidates": len(published_issues),
        },
        "runs": by_run,
        "suspicious_candidates": suspicious[:500],
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    exit_code = 0
    if fail_on_published and published_issues:
        exit_code = 2
    return exit_code, report


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit odds/probability integrity in archived run payloads.")
    parser.add_argument("--runs-root", default=".logs/runs")
    parser.add_argument("--report", default="artifacts/odds-integrity-report.json")
    parser.add_argument("--traces-dir", default="artifacts/odds-traces")
    parser.add_argument("--latest-only", action="store_true")
    args = parser.parse_args()

    max_gap_pp = float(os.getenv("ODDS_SANITY_MAX_IMPLIED_GAP_PP", "3.0"))
    max_odds_to_fair_ratio = float(os.getenv("ODDS_SANITY_MAX_ODDS_TO_FAIR_RATIO", "1.20"))
    fail_on_published = str(os.getenv("ODDS_SANITY_FAIL_ON_PUBLISHED", "true")).strip().lower() in {"1", "true", "yes", "on"}

    exit_code, report = build_report(
        runs_root=Path(args.runs_root),
        report_path=Path(args.report),
        traces_dir=Path(args.traces_dir),
        latest_only=bool(args.latest_only),
        max_gap_pp=max_gap_pp,
        max_odds_to_fair_ratio=max_odds_to_fair_ratio,
        fail_on_published=fail_on_published,
    )
    print(json.dumps(report.get("summary") or {}, ensure_ascii=False, indent=2))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
