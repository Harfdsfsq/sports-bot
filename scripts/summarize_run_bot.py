from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _latest_run_archive() -> Path | None:
    candidates = [p for p in Path(".logs/runs").glob("*/*-run.json") if p.is_file()] if Path(".logs/runs").exists() else []
    return sorted(candidates, key=lambda p: (p.parent.name, p.name))[-1] if candidates else None


def _load_payload() -> tuple[dict[str, Any], str]:
    debug = _read_json(Path(".logs/debug-last-run.json"), None)
    if isinstance(debug, dict) and debug:
        return debug, ".logs/debug-last-run.json"
    latest = _latest_run_archive()
    if latest:
        return _read_json(latest, {}), str(latest)
    return {}, ""


def _quality_stops(payload: dict[str, Any]) -> dict[str, int]:
    decisions = ((payload.get("quality_debug") or {}).get("decisions") or [])
    counts: Counter[str] = Counter()
    for item in decisions:
        reasons = item.get("reasons") if isinstance(item, dict) else []
        reason = str((reasons or [""])[0] or "unknown")
        if reason:
            counts[reason] += 1
    return dict(counts.most_common())


def _candidate_snapshot(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in payload.get("candidates_before_quality") or []:
        if not isinstance(item, dict):
            continue
        ss = item.get("source_summary") if isinstance(item.get("source_summary"), dict) else {}
        rows.append({
            "match_key": item.get("match_key"),
            "league_name": item.get("league_name"),
            "family": item.get("family"),
            "selection": item.get("selection"),
            "point": item.get("point"),
            "odds": item.get("odds"),
            "bookmaker": item.get("bookmaker") or ss.get("selected_bookmaker"),
            "market_probability": item.get("market_probability"),
            "model_probability": item.get("model_probability"),
            "adjusted_probability": item.get("adjusted_probability"),
            "source_summary_adjusted_probability": ss.get("adjusted_probability"),
            "edge_pct": item.get("edge_pct"),
            "ev_pct": item.get("ev_pct"),
            "confidence": item.get("confidence"),
            "publication_score": item.get("publication_score"),
            "quality_status": ss.get("quality_status"),
            "quality_reasons": ss.get("quality_reasons"),
            "model_mode": item.get("model_mode"),
        })
    return rows


def main() -> int:
    out = Path(".data/exports")
    out.mkdir(parents=True, exist_ok=True)
    payload, source_path = _load_payload()
    summary = dict(payload.get("summary") or {})
    integrity = _read_json(out / "latest-candidate-integrity.json", {})
    quality_report = _read_json(out / "latest-quality-report.json", {})
    qsum = dict((quality_report.get("summary") or {}))

    run_summary = {
        "created_at": payload.get("created_at") or datetime.now(UTC).isoformat(),
        "source_path": source_path,
        "matches_seen": summary.get("matches_seen"),
        "matches_before_publish_window": summary.get("matches_before_publish_window"),
        "matches_with_offers": summary.get("matches_with_offers"),
        "contexts_built": summary.get("contexts_built"),
        "candidates_before_quality": summary.get("candidates_before_quality"),
        "candidates_after_quality": summary.get("candidates"),
        "candidates_publishable": summary.get("candidates_publishable"),
        "published": summary.get("published"),
        "dry_run": summary.get("dry_run"),
        "prediction_publication_enabled": summary.get("prediction_publication_enabled"),
        "top_rejections": dict(Counter(summary.get("rejections") or {}).most_common(20)),
        "quality_stops": _quality_stops(payload),
        "integrity": dict((integrity.get("summary") or {})),
        "candidate_snapshot": _candidate_snapshot(payload),
        "quality_summary": {
            "settled_binary_bets": qsum.get("settled_binary_bets"),
            "wins": qsum.get("wins"),
            "losses": qsum.get("losses"),
            "roi_pct": qsum.get("roi_pct"),
            "hit_rate_pct": qsum.get("hit_rate_pct"),
            "avg_odds": qsum.get("avg_odds"),
        },
        "provider_flags": {
            name: {
                "api_key_present": ((stats.get("stats") or {}).get("api_key_present") if isinstance(stats, dict) else None),
                "rate_limited": ((stats.get("stats") or {}).get("rate_limited") if isinstance(stats, dict) else None),
                "items_total": stats.get("items_total") if isinstance(stats, dict) else None,
                "matches_with_data": stats.get("matches_with_data") if isinstance(stats, dict) else None,
            }
            for name, stats in (summary.get("source_stats") or {}).items()
            if name in {"odds_api_io", "oddspapi", "allsportsapi", "sstats", "api_football", "football_data"}
        },
    }

    (out / "latest-run-summary.json").write_text(json.dumps(run_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    md_lines = [
        "# Run bot summary",
        "",
        f"- matches_seen: {run_summary['matches_seen']}",
        f"- candidates_before_quality: {run_summary['candidates_before_quality']}",
        f"- candidates_after_quality: {run_summary['candidates_after_quality']}",
        f"- published: {run_summary['published']}",
        f"- integrity_suspicious: {(run_summary['integrity'] or {}).get('suspicious_candidates')}",
        "",
        "## Quality stops",
    ]
    for key, value in run_summary["quality_stops"].items():
        md_lines.append(f"- {key}: {value}")
    (out / "latest-run-summary.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(json.dumps(run_summary, ensure_ascii=False, indent=2)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
