from __future__ import annotations

"""Provider day discovery canonical pool v2.

Uses cached `latest-sstats-crosswalk.json` as the SStats fixture-discovery source
so provider-smoke does not spend extra SStats requests and hit 429. v2 also
persists the full canonical pool, not only the sample, so discovery-first can be
merged into day_inventory completely.  The provider date range follows the run
horizon so the 300-match production target is not capped by one calendar day.
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from scripts import provider_day_discovery_canonical_pool as v1

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "provider-day-discovery-canonical-pool.json"
LATEST_JSON_OUT = OUT_DIR / "latest-provider-day-discovery-canonical-pool.json"
TXT_OUT = OUT_DIR / "provider-day-discovery-canonical-pool.txt"
LATEST_TXT_OUT = OUT_DIR / "latest-provider-day-discovery-canonical-pool.txt"


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def horizon_days() -> int:
    raw = os.getenv("DAY_INVENTORY_HORIZON_DAYS") or os.getenv("DAY_INVENTORY_TARGET_HORIZON_DAYS") or os.getenv("RUN_DAYS_AHEAD") or "2"
    try:
        return max(1, min(int(float(raw)), 4))
    except Exception:
        return 2


def crosswalk_events() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    payload = load(OUT_DIR / "latest-sstats-crosswalk.json")
    events: list[dict[str, Any]] = []
    seen: set[str] = set()
    for key in ("matched", "matched_rows", "matched_full", "matched_sample", "enrichment_queue"):
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
        "events": events[:2000],
        "error": "",
    }


def build_specs_for_horizon() -> list[v1.CallSpec]:
    days = horizon_days()
    original_date_plus = v1.date_plus
    try:
        def date_plus_horizon(date_text: str, step_days: int) -> str:
            return original_date_plus(date_text, days if int(step_days or 0) == 1 else step_days)
        v1.date_plus = date_plus_horizon  # type: ignore[assignment]
        return [spec for spec in v1.build_calls() if spec.provider != "sstats"]
    finally:
        v1.date_plus = original_date_plus  # type: ignore[assignment]


async def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    days = horizon_days()
    max_seconds = float(v1.env("PROVIDER_DAY_DISCOVERY_MAX_SECONDS", "140"))
    timeout = float(v1.env("PROVIDER_DAY_DISCOVERY_TIMEOUT_SECONDS", "18"))
    concurrency = max(1, v1.as_int(v1.env("PROVIDER_DAY_DISCOVERY_CONCURRENCY"), 5))
    specs = build_specs_for_horizon()
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
        "mode": "provider_day_discovery_canonical_pool_v2_cached_sstats_full_pool_horizon",
        "status": "ok",
        "target_date": v1.target_date(),
        "horizon_days": days,
        "duration_seconds": round(time.perf_counter() - started, 2),
        "summary": v1.summarize(results, canonical),
        "results_summary": [{k: val for k, val in result.items() if k != "events"} for result in results],
        "canonical_matches": canonical,
        "canonical_matches_sample": canonical[:80],
        "targeted_enrichment_plan": v1.enrichment_plan(canonical),
        "notes": [
            "SStats discovery is reused from latest-sstats-crosswalk.json to avoid extra quota.",
            "The full canonical_matches list is persisted for inventory merge.",
            "Provider calls use the configured two-day run horizon when RUN_DAYS_AHEAD/DAY_INVENTORY_HORIZON_DAYS is 2.",
        ],
    }
    rendered = v1.render(payload)
    for path in (JSON_OUT, LATEST_JSON_OUT):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for path in (TXT_OUT, LATEST_TXT_OUT):
        path.write_text(rendered, encoding="utf-8")
    print(rendered)
    return payload


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
