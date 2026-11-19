"""Competition-bound form independent of a provider's future fixture listing."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.providers.sstats import SStatsContextProvider
from app.services.api_budget import BudgetedAsyncClient
from app.services.daily_match_registry import parse_time, read_json, write_json
from app.services.goal_research import archive_results, normalize_result
from app.utils import canonicalize_league_name


async def fetch_team_contexts(provider, matches, stats):
    report = stats["team_fallback"] = {
        "targets": len(matches),
        "competitions": 0,
        "contexts": 0,
        "cache_hits": 0,
        "requests": 0,
    }
    if not matches or stats.get("rate_limited"):
        return {}
    now = datetime.now(UTC)
    start = int(stats.get("requests", 0))
    cache_key = hashlib.sha256(str(provider.settings.football_data_api_key).encode()).hexdigest()[:16]
    async with BudgetedAsyncClient(
        timeout=provider.timeout, headers={"X-Auth-Token": str(provider.settings.football_data_api_key)}
    ) as client:

        async def get(path, params=None, ttl=21600):
            import json

            digest = hashlib.sha256((cache_key + path + json.dumps(params or {}, sort_keys=True)).encode()).hexdigest()[
                :24
            ]
            target = Path(".data/provider_cache") / ("football-team-" + digest + ".json")
            saved = read_json(target, {})
            at = parse_time(saved.get("observed_at"))
            if at and timedelta(0) <= now - at < timedelta(seconds=ttl):
                report["cache_hits"] += 1
                return saved.get("payload"), at
            if int(stats.get("requests", 0)) - start >= 6:
                return None, None
            data = await provider._fetch_json(client, path, stats, params=params, soft_fail_statuses={400, 403, 404})
            at = datetime.now(UTC)
            if data is not None:
                write_json(target, {"payload": data, "observed_at": at.isoformat()})
            return data, at

        catalogue, _ = await get("/competitions", {"plan": "TIER_ONE"}, ttl=86400)
        competitions = provider._extract_rows(catalogue, "competitions")
        groups = {}
        for match in matches:
            key = canonicalize_league_name(match.league_name)
            found = []
            for comp in competitions:
                if comp.get("plan") not in (None, "TIER_ONE"):
                    continue
                area = (comp.get("area") or {}).get("name") or ""
                aliases = provider._league_aliases(comp)
                aliases += [area + " " + n for n in aliases]
                if key and key in {canonicalize_league_name(n) for n in aliases}:
                    found.append(comp)
            if len(found) == 1 and found[0].get("code"):
                comp = found[0]
                groups.setdefault(comp["code"], (comp, []))[1].append(match)
        result = {}
        builder = SStatsContextProvider(provider.settings)
        for code, (comp, targets) in sorted(groups.items(), key=lambda kv: min(m.commence_time for m in kv[1][1]))[:2]:
            season = comp.get("currentSeason") or {}
            season_start = parse_time(str(season.get("startDate")) + "T00:00:00+00:00")
            season_end = parse_time(str(season.get("endDate")) + "T23:59:59+00:00")
            targets = [
                m for m in targets if season_start and season_end and season_start <= m.commence_time <= season_end
            ]
            if not targets:
                continue
            payload, observed = await get(
                "/competitions/" + code + "/matches", {"season": season_start.year, "status": "FINISHED", "limit": 500}
            )
            rows = provider._extract_rows(payload, "matches")
            safe = []
            seen, conflicts = {}, set()
            for row in rows:
                normalized = normalize_result("football_data", row)
                start_time = parse_time(row.get("utcDate"))
                row_comp = row.get("competition") or {}
                if row_comp.get("code") != code:
                    continue
                if (
                    normalized
                    and str(row.get("id") or "").isdecimal()
                    and int(row["id"]) > 0
                    and start_time
                    and season_start <= start_time < now
                ):
                    converted = {
                        "id": row.get("id"),
                        "date": row["utcDate"],
                        "homeTeam": row["homeTeam"],
                        "awayTeam": row["awayTeam"],
                        "homeFTResult": normalized["hg"],
                        "awayFTResult": normalized["ag"],
                        "season": {"league": {"name": comp["name"]}},
                    }
                    sid = str(row["id"])
                    if sid in seen and seen[sid] != converted:
                        conflicts.add(sid)
                    seen.setdefault(sid, converted)
            safe = [row for sid, row in seen.items() if sid not in conflicts]
            if safe:
                archive_results("football_data", rows, comp["name"])
                contexts = builder._build_team_form_contexts(
                    targets, safe, {"team_form_examples": [], "skip_goal_history_archive": True}
                )
                table_data, _ = await get("/competitions/" + code + "/standings")
                table = provider._extract_standings_table(table_data)
                for context in contexts.values():
                    context.source = "football_data"
                    context.details.update(
                        football_data_mode="verified_team_history",
                        football_data_competition=code,
                        football_data_home_sample=context.details["home_recent_count"],
                        football_data_away_sample=context.details["away_recent_count"],
                        daily_source_observed_at=observed.isoformat(),
                    )
                from app.services.fixture_identity import same_team_identity

                for match in targets:
                    context = contexts.get(match.match_key)
                    pair = {}
                    for side, name in [("home", match.home_team), ("away", match.away_team)]:
                        found = [r for r in table if same_team_identity(name, [(r.get("team") or {}).get("name")])]
                        if len(found) == 1:
                            pair[side] = found[0]
                    if context and len(pair) == 2:
                        context.payload["verified_standings"] = pair
                        context.details["football_data_standings_available"] = True
                result.update(contexts)
            report["competitions"] += 1
            if stats.get("rate_limited"):
                break
        report.update(contexts=len(result), requests=int(stats.get("requests", 0)) - start)
        return result
