from __future__ import annotations

"""Provider day discovery canonical pool v2.

v1 proved the discovery-first idea, but SStats got 429 because provider-smoke had
already spent SStats quota on crosswalk/deep enrichment. v2 treats the existing
`latest-sstats-crosswalk.json` as the SStats fixture-discovery cache and merges
those gameIds into the canonical pool without another SStats API call.
"""

import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from scripts import provider_day_discovery_canonical_pool as v1

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "provider-day-discovery-canonical-pool.json"
TXT_OUT = OUT_DIR / "provider-day-discovery-canonical-pool.txt"


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def crosswalk_events() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = load(OUT_DIR / "latest-sstats-crosswalk.json")
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in ("matched_sample", "enrichment_queue"):
        rows = payload.get(key) if isinstance(payload.get(key), list) else []
        for row in rows:
            if not isinstance(row, dict):
                continue
            game_id = str(row.get("sstats_game_id") or row.get("game_id") or "").strip()
            home = str(row.get("sstats_home_team") or row.get("home_team") or "").strip()
            away = str(row.get("sstats_away_team") or row.get("away_team") or "").strip()
            if not game_id or not home or not away:
                continue
            dedupe = game_id or f"{home}|{away}|{row.get('kickoff_utc')}"
            if dedupe in seen:
                continue
            seen.add(dedupe)
            league = str(row.get("sstats_league_name") or row.get("league_name") or "")
            events.append({
                "provider": "sstats",
                "source_id": game_id,
                "home_team": home,
                "away_team": away,
                "league_name": league,
                "kickoff_utc": row.get("sstats_kickoff_utc") or row.get("kickoff_utc"),
                "home_norm": v1.normalize(home),
                "away_norm": v1.normalize(away),
                "league_norm": v1.normalize(league),
                "raw_keys": ["from_latest_sstats_crosswalk"],
            })
    summary = payload.get("summary") if isinstance(payload.get("summary"), dict) else {}
    return events, {
        "provider": "sstats",
        "command": "latest_sstats_crosswalk_cache",
        "role": "fixture_primary_context_cache",
        "status": "OK" if events else "EMPTY",
        "http_status": None,
        "duration_ms": 0.0,
        "rows_count": int(summary.get("matched") or len(events)),
        "event_like_rows": len(events),
        "events": events[:1000],
        "error": "",
    }


async def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    max_seconds = float(v1.env("PROVIDER_DAY_DISCOVERY_MAX_SECONDS", "140"))
    timeout = float(v1.env("PROVIDER_DAY_DISCOVERY_TIMEOUT_SECONDS", "18"))
    concurrency = max(1, v1.as_int(v1.env("PROVIDER_DAY_DISCOVERY_CONCURRENCY"), 5))
    specs = [spec for spec in v1.build_calls() if spec.provider != "sstats"]
    sem = asyncio.Semaphore(concurrency)
    started = time.perf_counter()
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(6.0, timeout)), follow_redirects=True) as client:
        async def guarded(spec: v1.CallSpec) -> dict[str, Any]:
            async with sem:
                return await v1.call(client, spec)
        try:
            results = await asyncio.wait_for(asyncio.gather(*(guarded(spec) for spec in specs)), timeout=max_seconds)
        except asyncio.TimeoutError:
            results = []
    cached_events, cached_result = crosswalk_events()
    results.append(cached_result)
    events = [event for result in results for event in (result.get("events") or []) if isinstance(event, dict)]
    canonical = v1.merge_events(events, min_score=float(v1.env("PROVIDER_DAY_DISCOVERY_MIN_SCORE", "0.74")))
    canonical = sorted(canonical, key=lambda item: (str(item.get("kickoff_utc") or ""), -len(item.get("providers") or [])))
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "provider_day_discovery_canonical_pool_v2_cached_sstats",
        "status": "ok",
        "target_date": v1.target_date(),
        "duration_seconds": round(time.perf_counter() - started, 2),
        "summary": v1.summarize(results, canonical),
        "results_summary": [{k: val for k, val in result.items() if k != "events"} for result in results],
        "canonical_matches_sample": canonical[:80],
        "targeted_enrichment_plan": v1.enrichment_plan(canonical),
        "notes": [
            "v2 does not call SStats discovery endpoints when latest-sstats-crosswalk.json exists; it reuses cached gameIds from the earlier crosswalk step.",
            "This avoids 429 during provider-smoke and shows whether discovery-first can preserve odds-api.io/Bzzoiro/SStats source_ids together.",
        ],
    }
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    TXT_OUT.write_text(v1.render(payload), encoding="utf-8")
    print(v1.render(payload))
    return payload


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
