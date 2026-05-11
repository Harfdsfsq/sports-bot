from __future__ import annotations

"""Provider signal coverage blueprint v3.

v3 keeps the v2 coverage parser and additionally runs SStats inventory crosswalk
inside the existing workflow blueprint step. This avoids another workflow edit
while exposing the next actionable metric: how many top-300 inventory matches can
be connected to concrete SStats gameId values for deep enrichment.
"""

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts import provider_signal_coverage_blueprint_v2 as v2
from scripts import provider_signal_coverage_blueprint as base
from scripts import sstats_crosswalk_probe

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


def _run_crosswalk() -> dict[str, Any]:
    try:
        return asyncio.run(sstats_crosswalk_probe.run())
    except RuntimeError:
        # Very defensive: GitHub Actions normal path has no running loop.
        return _load_json(OUT_DIR / "latest-sstats-crosswalk.json")
    except Exception as exc:
        payload = {
            "mode": "sstats_crosswalk_probe_failed",
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "summary": {},
        }
        (OUT_DIR / "latest-sstats-crosswalk.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (OUT_DIR / "latest-sstats-crosswalk.txt").write_text(f"# SStats inventory crosswalk probe\nERROR: {payload['error']}\n", encoding="utf-8")
        return payload


def _crosswalk_plan(crosswalk: dict[str, Any]) -> dict[str, Any]:
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
        "integration_order": [
            "Use the crosswalk's SStats gameId for all matched inventory rows.",
            "For matches missing context_2plus, call SStats /Games/glicko/{id} + /Games/last-games-stats first.",
            "For matches missing venue/weather, call /Games/{id} and extract stadium/referee/city fields.",
            "For matches with odds confirmations <2, call /Odds/{gameId} as secondary odds rescue.",
            "Persist provider_source_ids.sstats=gameId into day_inventory before model enrichment.",
        ],
    }


def recommendations(provider_status: dict[str, Any], coverage: dict[str, Any], sstats_plan: dict[str, Any], crosswalk_plan: dict[str, Any]) -> list[str]:
    recs = v2.recommendations(provider_status, coverage, sstats_plan)
    matched = int(crosswalk_plan.get("matched") or 0)
    if matched > 0:
        recs.insert(0, f"SStats crosswalk matched {matched} inventory matches: persist provider_source_ids.sstats and start deep enrichment for the queue.")
    else:
        recs.insert(0, "SStats deep works, but crosswalk matched 0 rows: fix team/kickoff extraction and aliases before runtime enrichment.")
    return recs


def render(payload: dict[str, Any]) -> str:
    text = v2.render(payload).replace("# Provider signal coverage blueprint v2", "# Provider signal coverage blueprint v3")
    cross = payload.get("sstats_crosswalk_plan") or {}
    lines = text.rstrip().splitlines()
    insert = [
        "",
        "## SStats inventory crosswalk plan",
        f"- checked: {cross.get('inventory_matches_checked', 0)} inventory matches | SStats events: {cross.get('sstats_events_seen', 0)}",
        f"- matched: {cross.get('matched', 0)} | unmatched: {cross.get('unmatched', 0)} | rate={cross.get('match_rate_pct', 0)}%",
        f"- potential uplift: context_deep={cross.get('potential_context_deep', 0)} xg_or_rating={cross.get('potential_xg_or_rating', 0)} form={cross.get('potential_form', 0)} odds_rescue={cross.get('potential_odds_rescue', 0)}",
    ]
    by_bucket = cross.get("by_bucket") if isinstance(cross.get("by_bucket"), dict) else {}
    if by_bucket:
        insert.append("- by bucket: " + "; ".join(f"{k}=matched:{v.get('matched', 0)}" for k, v in sorted(by_bucket.items())))
    queue = cross.get("queue_top") if isinstance(cross.get("queue_top"), list) else []
    if queue:
        insert.append("- next deep queue:")
        for item in queue[:10]:
            insert.append(f"  - {item.get('bucket')} | {item.get('home_team')} — {item.get('away_team')} | gameId={item.get('sstats_game_id')} score={item.get('score')}")
    # Place right after SStats deep integration plan section if possible.
    try:
        idx = lines.index("## Provider roles and current status")
        lines[idx:idx] = insert
    except ValueError:
        lines.extend(insert)
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    crosswalk = _run_crosswalk()
    provider_status = base.current_provider_status()
    coverage = v2.current_coverage()
    sstats_plan = v2.sstats_deep_capability_plan()
    crosswalk_plan = _crosswalk_plan(crosswalk)
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "provider_signal_coverage_blueprint_v3",
        "targets": base.TARGETS,
        "provider_roles": base.PROVIDER_ROLES,
        "current_provider_status": provider_status,
        "current_coverage": coverage,
        "sstats_deep_capability_plan": sstats_plan,
        "sstats_crosswalk_plan": crosswalk_plan,
        "recommendations": recommendations(provider_status, coverage, sstats_plan, crosswalk_plan),
    }
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
