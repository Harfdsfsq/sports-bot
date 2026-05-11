from __future__ import annotations

"""Provider signal coverage blueprint v3.

Self-contained blueprint with SStats inventory crosswalk. The workflow calls v2,
and v2 delegates here. Therefore v3 must not import helper functions from v2.
"""

import asyncio
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts import provider_signal_coverage_blueprint as base
from scripts import sstats_crosswalk_probe

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "provider-signal-coverage-blueprint.json"
TXT_OUT = OUT_DIR / "provider-signal-coverage-blueprint.txt"


def load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def coverage_summary(matrix: dict[str, Any]) -> dict[str, Any]:
    totals = matrix.get("totals") if isinstance(matrix.get("totals"), dict) else {}
    if not totals:
        return dict(matrix.get("summary") or {}) if isinstance(matrix.get("summary"), dict) else {}
    return {
        "total": int(matrix.get("matrix_matches") or totals.get("matches") or 0),
        "fixture_2plus_sources": int(totals.get("fixture_2plus_sources") or 0),
        "odds_any": int(totals.get("odds_any") or 0),
        "odds_2plus_sources": int(totals.get("odds_2plus_sources") or 0),
        "context_any": int(totals.get("context_any") or 0),
        "context_2plus_sources": int(totals.get("context_2plus_sources") or 0),
        "xg": int(totals.get("xg") or 0),
        "form": int(totals.get("form") or 0),
        "weather": int(totals.get("weather") or 0),
        "news": int(totals.get("news") or 0),
        "ready_for_model": int(totals.get("ready_for_model") or 0),
        "publishable_like": int(totals.get("ready_for_publish") or 0),
    }


def current_coverage() -> dict[str, Any]:
    matrix = load_json(OUT_DIR / "provider-smoke-coverage-matrix.json")
    queue = matrix.get("next_enrichment_queue") if isinstance(matrix.get("next_enrichment_queue"), list) else []
    return {
        "matrix_found": bool(matrix),
        "matrix_status": matrix.get("status"),
        "matrix_version": matrix.get("matrix_version") or matrix.get("mode") or "base",
        "summary": coverage_summary(matrix),
        "coverage_by_kickoff_window": matrix.get("coverage_by_kickoff_window") if isinstance(matrix.get("coverage_by_kickoff_window"), dict) else {},
        "queue_top": queue[:20],
        "missing_counter": dict(Counter(m for item in queue if isinstance(item, dict) for m in (item.get("missing") or []))),
    }


def sstats_deep_capability_plan() -> dict[str, Any]:
    payload = load_json(OUT_DIR / "latest-sstats-deep-smoke.json")
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    capability_hits: Counter[str] = Counter()
    useful: list[dict[str, Any]] = []
    useful_caps = {"fixture_matchable", "xg", "rating", "lineups", "injuries", "odds", "profits", "venue_referee", "text_summary"}
    for row in results:
        if not isinstance(row, dict):
            continue
        caps = [str(cap) for cap in (row.get("capabilities") or [])]
        for cap in caps:
            capability_hits[cap] += 1
        if row.get("status") == "OK" and useful_caps.intersection(caps):
            useful.append({
                "command": row.get("command"),
                "role": row.get("role"),
                "status": row.get("status"),
                "rows_count": row.get("rows_count"),
                "event_like_rows": row.get("event_like_rows"),
                "capabilities": caps,
            })
    return {
        "status": payload.get("status") or ("ok" if results else "missing"),
        "commands_total": len(results),
        "ok_commands": sum(1 for row in results if isinstance(row, dict) and row.get("status") == "OK"),
        "sample_game_ids": payload.get("sample_game_ids") or [],
        "capability_hits": dict(capability_hits),
        "useful_commands": useful,
    }


def run_crosswalk() -> dict[str, Any]:
    try:
        return asyncio.run(sstats_crosswalk_probe.run())
    except Exception as exc:
        payload = {"mode": "sstats_crosswalk_probe_failed", "status": "error", "error": f"{type(exc).__name__}: {exc}", "summary": {}}
        (OUT_DIR / "latest-sstats-crosswalk.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (OUT_DIR / "latest-sstats-crosswalk.txt").write_text(f"# SStats inventory crosswalk probe\nERROR: {payload['error']}\n", encoding="utf-8")
        return payload


def crosswalk_plan(crosswalk: dict[str, Any]) -> dict[str, Any]:
    summary = crosswalk.get("summary") if isinstance(crosswalk.get("summary"), dict) else {}
    queue = crosswalk.get("enrichment_queue") if isinstance(crosswalk.get("enrichment_queue"), list) else []
    return {
        "status": crosswalk.get("status") or ("ok" if summary else "missing"),
        "inventory_matches_checked": crosswalk.get("inventory_matches_checked") or 0,
        "sstats_events_seen": crosswalk.get("sstats_events_seen") or 0,
        "matched": summary.get("matched") or 0,
        "unmatched": summary.get("unmatched") or 0,
        "match_rate_pct": summary.get("match_rate_pct") or 0,
        "potential_context_deep": summary.get("potential_context_deep") or 0,
        "potential_xg_or_rating": summary.get("potential_xg_or_rating") or 0,
        "potential_form": summary.get("potential_form") or 0,
        "potential_odds_rescue": summary.get("potential_odds_rescue") or 0,
        "by_bucket": crosswalk.get("by_bucket") if isinstance(crosswalk.get("by_bucket"), dict) else {},
        "queue_top": queue[:20],
    }


def recommendations(provider_status: dict[str, Any], coverage: dict[str, Any], sstats_plan: dict[str, Any], cross: dict[str, Any]) -> list[str]:
    recs = base.recommendations(provider_status, coverage)
    matched = int(cross.get("matched") or 0)
    if matched:
        recs.insert(0, f"SStats crosswalk matched {matched} inventory matches: persist provider_source_ids.sstats and start deep enrichment for the queue.")
    else:
        recs.insert(0, "SStats deep works, but crosswalk matched 0 rows: fix aliases/kickoff extraction first.")
    if int(sstats_plan.get("ok_commands") or 0) >= 10:
        recs.insert(1, "SStats deep is confirmed: promote it from passive historical form into active per-match enrichment.")
    return recs


def render(payload: dict[str, Any]) -> str:
    coverage = payload.get("current_coverage") or {}
    summary = coverage.get("summary") or {}
    sstats = payload.get("sstats_deep_capability_plan") or {}
    cross = payload.get("sstats_crosswalk_plan") or {}
    lines = [
        "# Provider signal coverage blueprint v3",
        f"UTC: {payload.get('created_at_utc')}",
        "",
        "## Current coverage summary",
    ]
    for key in ("total", "fixture_2plus_sources", "odds_any", "odds_2plus_sources", "context_any", "context_2plus_sources", "xg", "form", "weather", "news", "ready_for_model", "publishable_like"):
        if key in summary:
            lines.append(f"- {key}: {summary.get(key)}")
    lines += ["", "## SStats deep integration plan"]
    lines.append(f"- commands: {sstats.get('ok_commands', 0)} OK / {sstats.get('commands_total', 0)} total")
    caps = sstats.get("capability_hits") or {}
    if caps:
        lines.append("- capabilities: " + ", ".join(f"{k}={v}" for k, v in sorted(caps.items())))
    for item in (sstats.get("useful_commands") or [])[:12]:
        lines.append(f"  - {item.get('command')} [{item.get('role')}]: rows={item.get('rows_count')} caps={','.join(item.get('capabilities') or []) or '-'}")
    lines += ["", "## SStats inventory crosswalk plan"]
    lines.append(f"- checked: {cross.get('inventory_matches_checked', 0)} inventory matches | SStats events: {cross.get('sstats_events_seen', 0)}")
    lines.append(f"- matched: {cross.get('matched', 0)} | unmatched: {cross.get('unmatched', 0)} | rate={cross.get('match_rate_pct', 0)}%")
    lines.append(f"- potential uplift: context_deep={cross.get('potential_context_deep', 0)} xg_or_rating={cross.get('potential_xg_or_rating', 0)} form={cross.get('potential_form', 0)} odds_rescue={cross.get('potential_odds_rescue', 0)}")
    by_bucket = cross.get("by_bucket") if isinstance(cross.get("by_bucket"), dict) else {}
    if by_bucket:
        lines.append("- by bucket: " + "; ".join(f"{k}=matched:{v.get('matched', 0)}" for k, v in sorted(by_bucket.items())))
    queue = cross.get("queue_top") if isinstance(cross.get("queue_top"), list) else []
    if queue:
        lines.append("- next deep queue:")
        for item in queue[:10]:
            lines.append(f"  - {item.get('bucket')} | {item.get('home_team')} — {item.get('away_team')} | gameId={item.get('sstats_game_id')} score={item.get('score')}")
    lines += ["", "## Recommendations"]
    for rec in payload.get("recommendations") or []:
        lines.append(f"- {rec}")
    lines += ["", "## Next enrichment queue top missing reasons"]
    for reason, count in sorted((coverage.get("missing_counter") or {}).items(), key=lambda kv: (-kv[1], kv[0]))[:20]:
        lines.append(f"- {reason}: {count}")
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    crosswalk = run_crosswalk()
    provider_status = base.current_provider_status()
    coverage = current_coverage()
    sstats_plan = sstats_deep_capability_plan()
    cross = crosswalk_plan(crosswalk)
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "provider_signal_coverage_blueprint_v3",
        "targets": base.TARGETS,
        "provider_roles": base.PROVIDER_ROLES,
        "current_provider_status": provider_status,
        "current_coverage": coverage,
        "sstats_deep_capability_plan": sstats_plan,
        "sstats_crosswalk_plan": cross,
        "recommendations": recommendations(provider_status, coverage, sstats_plan, cross),
    }
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
