"""Optional verified league/year statistics; no extra independent source or xG."""

from __future__ import annotations

import hashlib
import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.services.api_budget import BudgetedAsyncClient
from app.services.daily_match_registry import parse_time, read_json, write_json
from app.services.sstats_targeted_history import verified_fixture


def safe_row(row, sid):
    if not isinstance(row, dict) or str(row.get("teamId")) != sid:
        return False
    return all(
        isinstance(row.get(k), (int, float))
        and not isinstance(row[k], bool)
        and math.isfinite(row[k])
        and row[k] >= (1 if k == "rank" else 0)
        for k in ("rank", "totalGames", "points", "goalsScored", "goalsMissed")
    )


async def enrich_standings(provider, matches, contexts, stats):
    report = stats["standings_enrichment"] = {"requests": 0, "cache_hits": 0, "contexts": 0, "errors": 0}
    limit = min(8, max(0, int(os.getenv("DAILY_SSTATS_STANDINGS_REQUEST_LIMIT", "4"))))
    now = datetime.now(UTC)
    groups = {}
    for match in sorted(matches, key=lambda m: m.commence_time):
        if match.commence_time - now < timedelta(minutes=30):
            continue
        row = verified_fixture(match) if match.match_key in contexts else None
        season = (row or {}).get("season") or {}
        league = season.get("league") or {}
        lid, year = str(league.get("id") or ""), str(season.get("year") or "")
        if lid.isdecimal() and int(lid) > 0 and year.isdecimal() and 2011 <= int(year) <= 2070:
            groups.setdefault((lid, year), []).append((match, row))
    if not groups or not limit:
        return
    ceiling = int(stats.get("requests", 0)) + limit
    async with BudgetedAsyncClient(timeout=8) as client:
        for (lid, year), targets in groups.items():
            digest = hashlib.sha256((str(provider.settings.sstats_api_key) + lid + "|" + year).encode()).hexdigest()[
                :24
            ]
            path = Path(".data/provider_cache") / ("sstats-standings-v1-" + digest + ".json")
            saved = read_json(path, {})
            observed = parse_time(saved.get("observed_at"))
            ttl = timedelta(minutes=15 if saved.get("error") else 360)
            if observed and timedelta(0) <= now - observed < ttl:
                payload = saved.get("data")
                report["cache_hits"] += 1
            else:
                if report["requests"] >= limit or stats.get("budget_exhausted") or stats.get("server_error_stop"):
                    continue
                start = int(stats.get("requests", 0))
                try:
                    response = await provider._request_with_retries(
                        client,
                        "https://api.sstats.net/Games/season-table",
                        params={"league": lid, "year": year, "apikey": provider.settings.sstats_api_key},
                        stats=stats,
                        request_ceiling=ceiling,
                    )
                    payload = response.json().get("data") if response is not None else None
                except (ValueError, TypeError, AttributeError, OSError, httpx.HTTPError):
                    payload = None
                report["requests"] += int(stats.get("requests", 0)) - start
                observed = datetime.now(UTC)
                if not isinstance(payload, dict):
                    report["errors"] += 1
                write_json(
                    path, {"data": payload, "observed_at": observed.isoformat(), "error": not isinstance(payload, dict)}
                )
            if not isinstance(payload, dict):
                continue
            for match, fixture in targets:
                pair = {}
                for side in ("home", "away"):
                    sid = str(fixture[side + "Team"].get("id") or "")
                    row = payload.get(sid)
                    if safe_row(row, sid):
                        pair[side] = dict(row)
                if len(pair) == 2:
                    context = contexts[match.match_key]
                    context.payload["verified_standings"] = pair
                    context.details["sstats_standings"] = {
                        "league_id": lid,
                        "year": year,
                        "observed_at": observed.isoformat(),
                        "home_rank": pair["home"]["rank"],
                        "away_rank": pair["away"]["rank"],
                    }
                    report["contexts"] += 1
