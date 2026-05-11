from __future__ import annotations

"""Provider signal coverage blueprint v2.

v1 looked for a non-existent `summary` field in provider-smoke-coverage-matrix.json.
The coverage matrix actually stores the useful counters in `totals`,
`matrix_matches`, `coverage_by_kickoff_window`, and `next_enrichment_queue`.
This v2 adapter keeps the provider role map from v1, but reads the real matrix
shape and adds an integration plan from SStats deep-smoke capabilities.
"""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts import provider_signal_coverage_blueprint as base

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "provider-signal-coverage-blueprint.json"
TXT_OUT = OUT_DIR / "provider-signal-coverage-blueprint.txt"


def _load_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _coverage_summary_from_matrix(matrix: dict[str, Any]) -> dict[str, Any]:
    totals = matrix.get("totals") if isinstance(matrix.get("totals"), dict) else {}
    if not totals:
        legacy = matrix.get("summary") if isinstance(matrix.get("summary"), dict) else {}
        return dict(legacy)
    matrix_matches = int(matrix.get("matrix_matches") or totals.get("matches") or 0)
    return {
        "total": matrix_matches,
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
    matrix = _load_json(OUT_DIR / "provider-smoke-coverage-matrix.json")
    queue = matrix.get("next_enrichment_queue") if isinstance(matrix.get("next_enrichment_queue"), list) else []
    summary = _coverage_summary_from_matrix(matrix)
    return {
        "matrix_found": bool(matrix),
        "matrix_status": matrix.get("status"),
        "matrix_version": matrix.get("matrix_version") or matrix.get("mode") or "base",
        "summary": summary,
        "coverage_by_kickoff_window": matrix.get("coverage_by_kickoff_window") if isinstance(matrix.get("coverage_by_kickoff_window"), dict) else {},
        "queue_top": queue[:20],
        "missing_counter": dict(Counter(m for item in queue if isinstance(item, dict) for m in (item.get("missing") or []))),
    }


def sstats_deep_capability_plan() -> dict[str, Any]:
    payload = _load_json(OUT_DIR / "latest-sstats-deep-smoke.json")
    results = payload.get("results") if isinstance(payload.get("results"), list) else []
    command_rows: list[dict[str, Any]] = []
    capability_hits: Counter[str] = Counter()
    for row in results:
        if not isinstance(row, dict):
            continue
        caps = [str(cap) for cap in (row.get("capabilities") or [])]
        for cap in caps:
            capability_hits[cap] += 1
        command_rows.append({
            "command": row.get("command"),
            "role": row.get("role"),
            "status": row.get("status"),
            "rows_count": row.get("rows_count"),
            "event_like_rows": row.get("event_like_rows"),
            "capabilities": caps,
        })
    useful = [
        row for row in command_rows
        if row.get("status") == "OK" and any(cap in set(row.get("capabilities") or []) for cap in {"fixture_matchable", "xg", "rating", "lineups", "injuries", "odds", "profits", "venue_referee", "text_summary"})
    ]
    return {
        "status": payload.get("status") or ("ok" if results else "missing"),
        "commands_total": len(results),
        "ok_commands": sum(1 for row in results if isinstance(row, dict) and row.get("status") == "OK"),
        "sample_game_ids": payload.get("sample_game_ids") or [],
        "capability_hits": dict(capability_hits),
        "useful_commands": useful,
        "integration_order": [
            "SStats /Games/list today/upcoming -> build sstats_match_id crosswalk against odds inventory by normalized home/away/kickoff.",
            "For matched priority games call /Games/last-games-stats and /Games/glicko first: these become independent context+xG/rating sources.",
            "Then call /Games/{id} for venue/referee/lineups/player stats only for matches still missing context_2plus or weather venue.",
            "Use /Odds/{gameId} only as secondary odds rescue when odds-api.io has <2 bookmaker/source confirmations.",
            "Use /Games/injuries and /Games/text-summary as explanatory context, not as standalone publication triggers.",
        ],
    }


def recommendations(provider_status: dict[str, Any], coverage: dict[str, Any], sstats_plan: dict[str, Any]) -> list[str]:
    recs = base.recommendations(provider_status, coverage)
    summary = coverage.get("summary") or {}
    if (sstats_plan.get("ok_commands") or 0) >= 10:
        recs.insert(0, "SStats deep is confirmed: promote it from passive historical form into active per-match enrichment for matched SStats IDs.")
    if int(summary.get("context_any") or 0) > int(summary.get("context_2plus_sources") or 0):
        recs.append("Current context is mostly single-source. Treat SStats last-games-stats/glicko/profits as separate context families only after source_id matching, not global history rows.")
    if int(summary.get("odds_any") or 0) > int(summary.get("odds_2plus_sources") or 0):
        recs.append("Use SStats /Odds/{gameId} and Bzzoiro odds comparison only for matches where odds-api.io has <2 independent price confirmations.")
    return recs


def render(payload: dict[str, Any]) -> str:
    coverage = payload.get("current_coverage") or {}
    summary = coverage.get("summary") or {}
    sstats = payload.get("sstats_deep_capability_plan") or {}
    lines = [
        "# Provider signal coverage blueprint v2",
        f"UTC: {payload.get('created_at_utc')}",
        "",
        "## Target",
        f"- odds sources per match: >= {base.TARGETS['odds_sources_min']}",
        f"- context sources per match: >= {base.TARGETS['context_sources_min']}",
        f"- desired flags: {', '.join(base.TARGETS['desired_match_flags'])}",
        "",
        "## Current coverage summary",
    ]
    if summary:
        for key in ("total", "fixture_2plus_sources", "odds_any", "odds_2plus_sources", "context_any", "context_2plus_sources", "xg", "form", "weather", "news", "ready_for_model", "publishable_like"):
            if key in summary:
                lines.append(f"- {key}: {summary.get(key)}")
    else:
        lines.append("- no coverage matrix found")
    lines += ["", "## SStats deep integration plan"]
    lines.append(f"- commands: {sstats.get('ok_commands', 0)} OK / {sstats.get('commands_total', 0)} total")
    lines.append(f"- sample_game_ids: {', '.join(str(x) for x in (sstats.get('sample_game_ids') or [])) or '-'}")
    caps = sstats.get("capability_hits") or {}
    if caps:
        lines.append("- capabilities: " + ", ".join(f"{k}={v}" for k, v in sorted(caps.items())))
    for item in (sstats.get("useful_commands") or [])[:12]:
        lines.append(f"  - {item.get('command')} [{item.get('role')}]: rows={item.get('rows_count')} caps={','.join(item.get('capabilities') or []) or '-'}")
    lines += ["", "## Provider roles and current status"]
    status = payload.get("current_provider_status") or {}
    for provider, meta in sorted(base.PROVIDER_ROLES.items(), key=lambda kv: (kv[1]["tier"], kv[0])):
        cur = status.get(provider, {})
        lines.append(
            f"- {provider}: tier={meta['tier']} signals={','.join(meta['signals'])} | "
            f"commands={cur.get('commands', 0)} ok={cur.get('ok', 0)} rows={cur.get('rows', 0)} rate_limit={cur.get('rate_limit', 0)} auth={cur.get('auth', 0)}"
        )
    lines += ["", "## Recommendations"]
    for rec in payload.get("recommendations") or []:
        lines.append(f"- {rec}")
    lines += ["", "## Next enrichment queue top missing reasons"]
    for reason, count in sorted((coverage.get("missing_counter") or {}).items(), key=lambda kv: (-kv[1], kv[0]))[:20]:
        lines.append(f"- {reason}: {count}")
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    provider_status = base.current_provider_status()
    coverage = current_coverage()
    sstats_plan = sstats_deep_capability_plan()
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "provider_signal_coverage_blueprint_v2",
        "targets": base.TARGETS,
        "provider_roles": base.PROVIDER_ROLES,
        "current_provider_status": provider_status,
        "current_coverage": coverage,
        "sstats_deep_capability_plan": sstats_plan,
        "recommendations": recommendations(provider_status, coverage, sstats_plan),
    }
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
