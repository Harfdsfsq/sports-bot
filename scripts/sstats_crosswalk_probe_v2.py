from __future__ import annotations

"""SStats crosswalk probe v2.

v1 reports the correct matched count but persists only matched_sample + a capped
enrichment_queue. Discovery-first needs the full matched list so it can preserve
all SStats gameIds in the canonical pool and inventory merge.
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts import sstats_crosswalk_probe as v1

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "latest-sstats-crosswalk.json"
TXT_OUT = OUT_DIR / "latest-sstats-crosswalk.txt"


def _future_order(bucket: str) -> int:
    return {"0_2h": 0, "2_6h": 1, "6_12h": 2, "12_24h": 3, "24h_plus": 4, "unknown": 5, "started": 6}.get(str(bucket or "unknown"), 5)


def _render(payload: dict[str, Any]) -> str:
    text = v1.render(payload)
    text = text.replace("# SStats inventory crosswalk probe", "# SStats inventory crosswalk probe v2")
    text += f"\n## Full matched persistence\n- matched_full: {len(payload.get('matched') or [])}\n- matched_sample: {len(payload.get('matched_sample') or [])}\n- enrichment_queue: {len(payload.get('enrichment_queue') or [])}\n"
    return text


async def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    inv_path = v1.inventory_path()
    inventory = v1.load_json(inv_path, {})
    matches = [row for row in inventory.get("matches", []) if isinstance(row, dict)] if isinstance(inventory, dict) else []
    limit = max(1, v1.as_int(v1.env("SSTATS_CROSSWALK_MATCH_LIMIT"), 300))
    selected = sorted(matches, key=lambda r: (str(r.get("kickoff_utc") or ""), -float(r.get("priority") or 0)))[:limit]
    sstats_events, call_results = await v1.fetch_sstats_games()
    min_score = float(v1.env("SSTATS_CROSSWALK_MIN_SCORE", "0.72"))

    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for inv in selected:
        best_event: dict[str, Any] | None = None
        best_score = 0.0
        best_debug: dict[str, Any] = {}
        for event in sstats_events:
            score, debug = v1.match_score(inv, event)
            if score > best_score:
                best_event, best_score, best_debug = event, score, debug
        flags = v1.coverage_flags(inv)
        row_base = {
            "match_key": inv.get("match_key") or inv.get("canonical_match_id"),
            "kickoff_utc": inv.get("kickoff_utc"),
            "bucket": v1.bucket(inv.get("kickoff_utc")),
            "league_name": inv.get("league_name"),
            "home_team": inv.get("home_team"),
            "away_team": inv.get("away_team"),
            "priority": inv.get("priority"),
            "coverage": flags,
        }
        if best_event and best_score >= min_score:
            matched.append({
                **row_base,
                "score": round(best_score, 4),
                "debug": best_debug,
                "sstats_game_id": best_event.get("game_id"),
                "sstats_home_team": best_event.get("home_team"),
                "sstats_away_team": best_event.get("away_team"),
                "sstats_league_name": best_event.get("league_name"),
                "sstats_kickoff_utc": best_event.get("kickoff_utc"),
                "recommended_deep_endpoints": ["/Games/glicko/{id}", "/Games/last-games-stats", "/Games/{id}", "/Games/profits", "/Games/injuries"],
                "recommended_odds_endpoint": "/Odds/{gameId}" if not flags.get("odds") else None,
            })
        else:
            unmatched.append({**row_base, "best_score": round(best_score, 4), "best_candidate": best_event, "debug": best_debug})

    by_bucket: dict[str, dict[str, int]] = {}
    for item in matched:
        b = str(item.get("bucket") or "unknown")
        by_bucket.setdefault(b, {"matched": 0, "missing_context": 0, "missing_xg": 0, "missing_form": 0, "odds_rescue": 0})
        by_bucket[b]["matched"] += 1
        cov = item.get("coverage") or {}
        if not cov.get("context"):
            by_bucket[b]["missing_context"] += 1
        if not cov.get("xg"):
            by_bucket[b]["missing_xg"] += 1
        if not cov.get("form"):
            by_bucket[b]["missing_form"] += 1
        if not cov.get("odds"):
            by_bucket[b]["odds_rescue"] += 1

    enrichment_queue = sorted(
        [item for item in matched if not (item.get("coverage") or {}).get("context") or not (item.get("coverage") or {}).get("xg") or not (item.get("coverage") or {}).get("form")],
        key=lambda item: (_future_order(str(item.get("bucket") or "unknown")), str(item.get("kickoff_utc") or ""), -float(item.get("priority") or 0)),
    )[:max(80, min(160, len(matched)))]

    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "sstats_crosswalk_probe_v2_full_matched",
        "duration_seconds": round(time.perf_counter() - started, 2),
        "inventory_path": str(inv_path),
        "inventory_matches_seen": len(matches),
        "inventory_matches_checked": len(selected),
        "sstats_events_seen": len(sstats_events),
        "sstats_call_results": call_results,
        "min_score": min_score,
        "summary": {
            "matched": len(matched),
            "unmatched": len(unmatched),
            "match_rate_pct": round((len(matched) / max(1, len(selected))) * 100, 2),
            "potential_context_deep": len(matched),
            "potential_xg_or_rating": len(matched),
            "potential_form": len(matched),
            "potential_odds_rescue": sum(1 for item in matched if not (item.get("coverage") or {}).get("odds")),
        },
        "by_bucket": by_bucket,
        "matched": matched,
        "matched_sample": matched[:40],
        "unmatched_sample": unmatched[:40],
        "enrichment_queue": enrichment_queue,
        "notes": [
            "v2 persists the full matched list for discovery-first canonical pool and inventory merge.",
            "A matched row means SStats gameId can be used for deep context and optional Odds/{gameId}.",
        ],
    }
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    TXT_OUT.write_text(_render(payload), encoding="utf-8")
    print(_render(payload))
    return payload


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
