"""Country-scoped exact team catalogue discovery; a team ID is not context."""

from __future__ import annotations

import asyncio
import hashlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.api_budget import BudgetedAsyncClient
from app.services.daily_match_registry import parse_time, read_json, write_json
from app.services.fixture_identity import (
    history_team_name,
    reviewed_team_alias_match,
    same_history_team_identity,
    squad_identity,
)
from app.utils import canonicalize_league_name, canonicalize_team_name

COUNTRIES = {
    "india": "IN", "thailand": "TH", "myanmar": "MM", "montenegro": "ME",
    "ethiopia": "ET", "turkmenistan": "TM", "singapore": "SG", "vietnam": "VN",
    "ukraine": "UA", "germany": "DE", "brazil": "BR", "colombia": "CO",
    "mexico": "MX", "usa": "US", "united states": "US", "el salvador": "SV",
    "panama": "PA", "suriname": "SR", "algeria": "DZ", "congo dr": "CD",
    "dr congo": "CD", "republic of korea": "KR", "south korea": "KR",
}


def country_for(match):
    league = canonicalize_league_name(match.league_name)
    for name in sorted(COUNTRIES, key=len, reverse=True):
        if league == name or league.startswith(name + " "):
            return COUNTRIES[name]
    return None


def verified_lookup(match, side, receipt):
    country = country_for(match)
    if not country or not isinstance(receipt, dict) or receipt.get("country") != country:
        return False
    team = receipt.get("team") or {}
    if not isinstance(team, dict) or not isinstance(team.get("country"), dict):
        return False
    return (receipt.get("unique_exact") is True and str(team.get("id", "")).isdecimal()
            and int(team["id"]) > 0 and (team.get("country") or {}).get("code") == receipt.get("country")
            and catalogue_name_matches(history_team_name(match, side), team.get("name")))


def catalogue_name_matches(target, name):
    return (bool(name) and bool(canonicalize_team_name(target)) and squad_identity(target) == squad_identity(name)
            and (canonicalize_team_name(target) == canonicalize_team_name(name)
                 or reviewed_team_alias_match(target, name)))


async def discover(provider, matches, bindings, stats):
    report = stats["team_catalogue"] = {"requests": 0, "cache_hits": 0, "bound": 0, "ambiguous": 0}
    found, provenance = {}, {}
    start_requests = int(stats.get("requests", 0))
    ceiling = start_requests + 6
    matches = [m for m in matches if country_for(m) and any(
        not any(same_history_team_identity(history_team_name(m, side), [n]) for n in bindings.values())
        for side in ("home", "away"))]
    if not matches:
        return found, provenance
    try:
        async with asyncio.timeout(18):
            async with BudgetedAsyncClient(timeout=4) as client:
                for match in matches:
                    country = country_for(match)
                    if not country:
                        continue
                    for side in ("home", "away"):
                        name = history_team_name(match, side)
                        if any(same_history_team_identity(name, [n]) for n in [*bindings.values(), *found.values()]):
                            continue
                        digest = hashlib.sha256((country + "|" + name).encode()).hexdigest()[:24]
                        path = Path(".data/provider_cache") / ("sstats-team-catalogue-v1-" + digest + ".json")
                        saved = read_json(path, {})
                        saved = saved if isinstance(saved, dict) else {}
                        observed = parse_time(saved.get("observed_at"))
                        receipt = saved.get("receipt")
                        if observed and timedelta(0) <= datetime.now(UTC) - observed < timedelta(hours=6) and saved.get("negative"):
                            report["negative_cache_hits"] = report.get("negative_cache_hits", 0) + 1
                            continue
                        if observed and timedelta(0) <= datetime.now(UTC) - observed < timedelta(days=7) and verified_lookup(match, side, receipt):
                            report["cache_hits"] += 1
                        else:
                            if int(stats.get("requests", 0)) >= ceiling or provider._http_budget_exhausted(stats):
                                continue
                            response = await provider._request_with_retries(client, "https://api.sstats.net/Teams/list", params={
                                "Name": name, "Country": country, "Offset": 0, "Limit": 100,
                                "apikey": provider.settings.sstats_api_key,
                            }, stats=stats, request_ceiling=ceiling)
                            if response is None:
                                return found, provenance
                            payload = response.json()
                            rows = payload.get("data", []) if isinstance(payload, dict) else []
                            total = payload.get("TotalCount") if isinstance(payload, dict) else None
                            complete = isinstance(total, int) and not isinstance(total, bool) and total == len(rows)
                            exact = {str(r.get("id")): r for r in rows if isinstance(r, dict)
                                     and isinstance(r.get("country"), dict) and r["country"].get("code") == country
                                     and catalogue_name_matches(name, r.get("name"))}
                            if len(exact) != 1 or not complete:
                                report["ambiguous"] += 1
                                write_json(path, {"observed_at": datetime.now(UTC).isoformat(), "negative": True})
                                continue
                            receipt = {"country": country, "unique_exact": True, "team": next(iter(exact.values()))}
                            if not verified_lookup(match, side, receipt):
                                continue
                            write_json(path, {"observed_at": datetime.now(UTC).isoformat(), "receipt": receipt})
                        sid = str(receipt["team"]["id"])
                        if sid in bindings or sid in found:
                            continue
                        found[sid] = name
                        provenance.setdefault(match.match_key, {})[side] = receipt
                        report["bound"] += 1
    except (TimeoutError, OSError, ValueError, TypeError, KeyError):
        report["partial_or_unavailable"] = True
    finally:
        report["requests"] = max(0, int(stats.get("requests", 0)) - start_requests)
    return found, provenance
