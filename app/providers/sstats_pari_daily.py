"""Bounded native PARI quotes: current pre-match prices, never closing history."""

from __future__ import annotations

import json
import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.providers.sstats_pari_parser import extract_list, parse_offers, unwrap
from app.services.api_budget import BudgetedAsyncClient
from app.services.daily_match_registry import parse_time, read_json, write_json
from app.services.fixture_identity import direct_fixture_identity, squad_identity, team_identity_key


def info(row):
    value = row.get("matchInfo", row) if isinstance(row, dict) else {}
    return value if isinstance(value, dict) else {}


def identity(row):
    r = info(row)

    def name(v):
        return v.get("name") if isinstance(v, dict) else v

    return {"home": name(r.get("homeTeam")), "away": name(r.get("awayTeam")), "commence_time": r.get("startDate")}


def matches_fixture(match, row):
    """Exact names or explicit same-source team-ID crosswalk, never fuzzy text."""
    from app.services.daily_match_registry import parse_time
    from app.services.sstats_targeted_history import verified_fixture

    event = info(row)
    fixture = verified_fixture(match)
    source_id = str((fixture or {}).get("id") or "")
    mapping = event.get("mappings") or {}
    mapped_games = {str(mapping[k]) for k in ("sstats", "sapi") if isinstance(mapping, dict) and mapping.get(k)}
    if source_id and mapped_games and mapped_games != {source_id}:
        return False
    if fixture:
        for side in ("home", "away"):
            team = event.get(side + "Team") or {}
            maps = team.get("mappings") if isinstance(team, dict) else {}
            ids = set()
            for key in ("sstats", "sapi"):
                values = maps.get(key) if isinstance(maps, dict) else None
                if isinstance(values, list):
                    ids.update(str(v) for v in values)
                elif values is not None:
                    ids.add(str(values))
            if ids and ids != {str(fixture[side + "Team"].get("id") or "")}:
                return False
    if direct_fixture_identity(match, identity(row), tolerance_hours=0.25):
        return True
    if not fixture:
        return False
    start = parse_time(event.get("startDate"))
    if not start or abs(start - match.commence_time) > timedelta(minutes=15):
        return False
    for side, name in (("home", match.home_team), ("away", match.away_team)):
        team = event.get(side + "Team") or {}
        if not isinstance(team, dict) or squad_identity(name) != squad_identity(team.get("name")):
            return False
        mappings = team.get("mappings") or {}
        ids = set()
        for key in ("sstats", "sapi"):
            values = mappings.get(key) if isinstance(mappings, dict) else None
            if isinstance(values, list):
                ids.update(str(v) for v in values)
            elif values is not None:
                ids.add(str(values))
        expected = str(fixture[side + "Team"].get("id") or "")
        if not expected.isdecimal() or int(expected) <= 0 or ids != {expected}:
            return False
    return True


def market_definitions(payload):
    definitions, conflicts, owners = {}, set(), {}
    markets = extract_list(payload)
    for market in markets:
        for outcome in (market.get("outcomes") if isinstance(market.get("outcomes"), list) else []):
            if not isinstance(outcome, dict):
                continue
            sid = str(outcome.get("id") or "")
            signature = json.dumps(
                [market.get("name"), outcome.get("name"), outcome.get("period"), outcome.get("parameter")],
                sort_keys=True,
            )
            if sid in owners and owners[sid] != signature:
                conflicts.add(sid)
            owners[sid] = signature
    for market in markets:
        family = market.get("name")
        if family not in {"1X2", "Handicap", "Total", "BTS"}:
            continue
        for outcome in (market.get("outcomes") if isinstance(market.get("outcomes"), list) else []):
            if not isinstance(outcome, dict) or outcome.get("period") != "FullTime":
                continue
            sid = str(outcome.get("id") or "")
            if not sid.isdecimal() or int(sid) <= 0:
                continue
            name, point = outcome.get("name"), outcome.get("parameter")
            if family == "1X2" and name in {"1", "2", "X"}:
                label = name
            elif family == "BTS" and name in {"Yes", "No"}:
                label = "Both teams to score " + name
            elif family in {"Handicap", "Total"} and point is not None:
                allowed = {"Over", "Under"} if family == "Total" else {"Home", "Away"}
                if name not in allowed:
                    continue
                try:
                    numeric = float(point)
                except (TypeError, ValueError, OverflowError):
                    continue
                if isinstance(point, bool) or not math.isfinite(numeric):
                    continue
                label = ("Goals Total " if family == "Total" else "Asian Handicap ") + name + " " + f"{numeric:g}"
            else:
                continue
            value = {"name": label, "period": "FullTime"}
            if sid in definitions and definitions[sid] != value:
                conflicts.add(sid)
            definitions[sid] = value
    return {sid: value for sid, value in definitions.items() if sid not in conflicts}


def prematch(row):
    r = info(row)
    return r.get("status") in {"NotStarted", "WillStartSoon"} and not r.get("isDeleted")


class SStatsPariDailyProvider:
    def __init__(self, settings):
        self.api_key = str(getattr(settings, "sstats_api_key", "") or "")
        self.used = 0
        self.limit = min(16, max(0, int(os.getenv("DAILY_PARI_REQUEST_LIMIT", "12"))))

    async def confirm_offers(self, matches):
        return await self.fetch_offers(matches, confirmation=True)

    async def watch_offers(self, matches):
        return await self.fetch_offers(matches, closing_watch=True)

    async def fetch_offers(self, matches, *, confirmation=False, closing_watch=False):
        stats = {
            "enabled": os.getenv("DAILY_PARI_ENABLED", "false").lower() == "true",
            "api_key_present": bool(self.api_key),
            "requests": 0,
            "response_errors": 0,
            "fixtures_matched": 0,
            "offers_parsed": 0,
            "skip_reasons": {},
        }
        result = {}
        preview = {"verified_fixture_bindings": {}}
        if not stats["enabled"] or not self.api_key or not self.limit:
            return result, stats, {}
        now = datetime.now(UTC)
        lead = timedelta(microseconds=1) if closing_watch else timedelta(minutes=30)
        horizon = (
            4
            if confirmation
            else 0.75
            if closing_watch
            else min(48, max(4, float(os.getenv("DAILY_DATA_COLLECTION_WINDOW_HOURS", "24"))))
        )
        targets = [m for m in matches if lead <= m.commence_time - now <= timedelta(hours=horizon)]
        # Preserve the registry gap-first order for bounded detail fallbacks.
        stats["collection_horizon_hours"] = horizon
        stats["attempted_match_keys"] = [m.match_key for m in targets]
        blocked = False
        ceiling = self.limit if confirmation or self.limit <= 4 else self.limit - 4
        stats.update(
            collection_request_limit=ceiling,
            physical_request_limit=self.limit,
            confirmation_requests_reserved=max(0, self.limit - ceiling),
        )

        def skip(reason):
            stats["skip_reasons"][reason] = stats["skip_reasons"].get(reason, 0) + 1

        async with BudgetedAsyncClient(timeout=8) as client:

            async def get(path, params=None):
                nonlocal blocked
                if blocked or self.used >= ceiling:
                    stats["budget_exhausted"] = self.used >= ceiling
                    return None, None
                self.used += 1
                stats["requests"] += 1
                try:
                    response = await client.get(
                        "https://api.sstats.net" + path, params={**(params or {}), "apikey": self.api_key}
                    )
                    stats.setdefault("http_statuses", []).append(response.status_code)
                    if response.status_code in {401, 402, 403, 429}:
                        blocked = True
                        stats["blocked_status"] = response.status_code
                    if response.status_code != 200:
                        stats["response_errors"] += 1
                        return None, None
                    return response.json(), datetime.now(UTC)
                except (TimeoutError, OSError, ValueError, httpx.HTTPError) as exc:
                    stats["response_errors"] += 1
                    stats["last_error_type"] = type(exc).__name__
                    return None, None

            rows = []
            observed_by_id = {}
            stats["bulk_quotes_matches"] = 0
            stats["detail_requests_avoided"] = 0
            for day in sorted({m.commence_time.date().isoformat() for m in targets}):
                offset, seen = 0, set()
                for _ in range(4):
                    payload, observed = await get(
                        "/Pari/matches",
                        {
                            "date": day,
                            "timezone": 0,
                            "upcoming": "true",
                            "includeOdds": "false" if confirmation else "true",
                            "limit": 1000,
                            "offset": offset,
                        },
                    )
                    if payload is None:
                        break
                    page = extract_list(payload)
                    signature = tuple(str(info(r).get("eventId")) for r in page)
                    if not page or signature in seen:
                        break
                    seen.add(signature)
                    rows.extend(page)
                    for event in page:
                        observed_by_id[str(info(event).get("eventId"))] = observed
                    offset += len(page)
                    # Documented schema capitalizes TotalCount. Do not trust count,
                    # which describes only this page, or a server-capped short page.
                    total = payload.get("TotalCount", payload.get("totalCount")) if isinstance(payload, dict) else None
                    try:
                        if total is not None and offset >= int(total):
                            break
                    except (ValueError, TypeError):
                        skip("invalid_total_count")
                        break
            # A provider may repeat the same event on two date pages. Identical
            # receipts are one fixture; contradictory versions remain ambiguous.
            unique_rows = {}
            for row in rows:
                unique_rows.setdefault(json.dumps(row, sort_keys=True), row)
            rows = list(unique_rows.values())
            definitions = {}
            dictionary_path = Path(".data/provider_cache/pari-market-types-v1.json")
            if any(
                isinstance(q, dict) and not (q.get("name") or q.get("label"))
                for row in rows
                for q in (row.get("currentOdds") if isinstance(row.get("currentOdds"), list) else [])
            ):
                saved = read_json(dictionary_path, {})
                at = parse_time(saved.get("observed_at"))
                if at and timedelta(0) <= datetime.now(UTC) - at < timedelta(days=1):
                    definitions = market_definitions(saved.get("payload"))
                    stats["market_dictionary_cache_hit"] = bool(definitions)
                if not definitions:
                    payload, at = await get("/Pari/odds/market-types")
                    definitions = market_definitions(payload)
                    if definitions and at:
                        write_json(dictionary_path, {"payload": payload, "observed_at": at.isoformat()})
                stats["market_dictionary_outcomes"] = len(definitions)
            selected = []
            for match in targets:
                found = [r for r in rows if prematch(r) and matches_fixture(match, r)]
                if len(found) != 1:
                    skip("no_unique_fixture")
                    continue
                selected.append((match, found[0]))
                preview["verified_fixture_bindings"][match.match_key] = {
                    **identity(found[0]),
                    "event_id": str(info(found[0]).get("eventId") or ""),
                    "provider_event": {"matchInfo": info(found[0])},
                }
            # One source event must not become two independent fixture receipts.
            # Prefer a unique exact provider spelling; ties remain unresolved.
            groups = {}
            for match, event in selected:
                groups.setdefault(str(info(event).get("eventId")), []).append((match, event))
            selected = []
            stats["duplicate_detail_requests_avoided"] = 0
            for group in groups.values():
                if len(group) == 1:
                    selected.extend(group)
                    continue
                exact = [
                    (m, e)
                    for m, e in group
                    if team_identity_key(m.home_team) == team_identity_key(identity(e)["home"])
                    and team_identity_key(m.away_team) == team_identity_key(identity(e)["away"])
                ]
                stats["duplicate_detail_requests_avoided"] += len(group) - int(len(exact) == 1)
                if len(exact) == 1:
                    selected.extend(exact)
                else:
                    skip("ambiguous_runtime_fixture")
            for match, event in selected:
                sid = str(info(event).get("eventId") or "")
                if not sid.isdecimal() or int(sid) <= 0:
                    skip("invalid_event_id")
                    continue
                if not confirmation:
                    bulk = self._parse_receipt(event, match, sid, observed_by_id.get(sid), definitions)
                    if bulk:
                        result[match.match_key] = bulk
                        stats["offers_parsed"] += len(bulk)
                        stats["fixtures_matched"] += 1
                        stats["bulk_quotes_matches"] += 1
                        stats["detail_requests_avoided"] += 1
                        continue
                payload, observed = await get("/Pari/match/" + sid)
                if payload is None:
                    skip("detail_budget_or_request_failure")
                    continue
                detail = unwrap(payload)
                if (
                    not isinstance(detail, dict)
                    or str(info(detail).get("eventId")) != sid
                    or not prematch(detail)
                    or not matches_fixture(match, detail)
                ):
                    skip("detail_identity_or_status")
                    continue
                parsed = self._parse_receipt(detail, match, sid, observed, definitions)
                if parsed:
                    stats["fixtures_matched"] += 1
                    result[match.match_key] = parsed
                    stats["offers_parsed"] += len(parsed)
                else:
                    skip("no_usable_current_odds")
        return result, stats, preview

    @staticmethod
    def _parse_receipt(detail, match, sid, observed, definitions=None):
        from app.services.market_period import is_full_match_market

        if (
            not observed
            or not prematch(detail)
            or str(info(detail).get("eventId")) != sid
            or not matches_fixture(match, detail)
        ):
            return []
        quotes = detail.get("currentOdds")
        if not isinstance(quotes, list):
            return []
        definitions = definitions or {}
        quotes = [
            {
                **definitions.get(str(q.get("id")), {}),
                **q,
                **(
                    {"name": definitions[str(q.get("id"))]["name"]}
                    if not (q.get("name") or q.get("label")) and str(q.get("id")) in definitions
                    else {}
                ),
            }
            for q in quotes
            if isinstance(q, dict)
        ]
        parsed = parse_offers(
            [q for q in quotes if isinstance(q, dict) and not q.get("isBlocked") and not q.get("isDeleted")], match, sid
        )
        raw = {str(q.get("id")): q for q in quotes if isinstance(q, dict)}
        parsed = [o for o in parsed if is_full_match_market(raw.get(str(o.metadata.get("raw_outcome_id")), {}))]
        for offer in parsed:
            offer.metadata.update(
                fetched_at_utc=observed.isoformat(), current_prematch=True, independent_odds_source=False
            )
        return parsed
