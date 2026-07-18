from __future__ import annotations

import asyncio
import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from app.schemas import Match, Offer
from app.utils import canonicalize_team_name, score_event_match

_INSTALLED = False


def _match_info(row: Any) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    for key in ("matchInfo", "match_info", "fixture", "event"):
        value = row.get(key)
        if isinstance(value, dict):
            merged = dict(value)
            for extra in ("odds", "markets", "outcomes"):
                if extra not in merged and extra in row:
                    merged[extra] = row[extra]
            return merged
    return row


def _extract_list(payload: Any) -> list[dict[str, Any]]:
    current = payload
    for _ in range(5):
        if not isinstance(current, dict):
            break
        value = next(
            (
                current.get(key)
                for key in ("data", "result", "response")
                if isinstance(current.get(key), (dict, list))
            ),
            None,
        )
        if value is None:
            break
        current = value
    if isinstance(current, dict):
        current = next(
            (
                current.get(key)
                for key in ("items", "matches", "rows", "data", "result")
                if isinstance(current.get(key), list)
            ),
            [],
        )
    if not isinstance(current, list):
        return []
    return [info for item in current if (info := _match_info(item))]


def _extract_odds(payload: Any) -> list[dict[str, Any]]:
    current = payload
    for _ in range(5):
        if not isinstance(current, dict):
            break
        value = next(
            (
                current.get(key)
                for key in ("data", "result", "response")
                if isinstance(current.get(key), dict)
            ),
            None,
        )
        if value is None:
            break
        current = value
    if not isinstance(current, dict):
        return []
    for key in ("odds", "Odds", "coefficients", "outcomes"):
        value = current.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
    return []


def _total_count(payload: Any, fallback: int) -> int:
    current = payload
    for _ in range(5):
        if not isinstance(current, dict):
            break
        for key in ("totalCount", "TotalCount", "total_count", "total", "count"):
            if key in current:
                try:
                    return max(fallback, int(float(current[key])))
                except (TypeError, ValueError):
                    pass
        current = next(
            (
                current.get(key)
                for key in ("data", "result", "response")
                if isinstance(current.get(key), dict)
            ),
            None,
        )
        if current is None:
            break
    return fallback


def _event_id(row: dict[str, Any]) -> Any:
    info = _match_info(row)
    return info.get("eventId") or info.get("event_id") or info.get("id")


def _league_name(row: dict[str, Any]) -> str:
    info = _match_info(row)
    value = info.get("tournament") or info.get("league") or info.get("competition")
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or "").strip()
    return str(value or "").strip()


def _team_name(value: Any) -> str:
    if isinstance(value, dict):
        return str(value.get("name") or value.get("title") or value.get("teamName") or "").strip()
    return str(value or "").strip()


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        from app.utils import parse_datetime

        return parse_datetime(value).astimezone(UTC)
    except Exception:
        return None


def _timezone() -> ZoneInfo:
    try:
        return ZoneInfo(str(os.getenv("APP_TIMEZONE") or "Europe/Moscow"))
    except Exception:
        return ZoneInfo("Europe/Moscow")


def _timezone_offset_hours(now: datetime | None = None) -> int:
    local = (now or datetime.now(UTC)).astimezone(_timezone())
    offset = local.utcoffset() or timedelta(0)
    return max(-12, min(12, int(offset.total_seconds() // 3600)))


def _status(event: dict[str, Any]) -> str:
    return str(_match_info(event).get("status") or "").strip().lower().replace("_", "")


def _event_fields(event: dict[str, Any]) -> tuple[str, str, datetime | None, str]:
    info = _match_info(event)
    home = _team_name(info.get("homeTeam") or info.get("home_team") or info.get("home"))
    away = _team_name(info.get("awayTeam") or info.get("away_team") or info.get("away"))
    start = _parse_dt(info.get("startDate") or info.get("start_time") or info.get("date"))
    return home, away, start, _league_name(info)


def _match_events(
    matches: list[Match],
    events: list[dict[str, Any]],
    stats: dict[str, Any],
    preview: dict[str, Any],
) -> dict[str, tuple[Match, dict[str, Any], float, str]]:
    by_date: dict[str, list[Match]] = defaultdict(list)
    for match in matches:
        kickoff = match.commence_time.astimezone(UTC)
        for offset in (-1, 0, 1):
            by_date[(kickoff.date() + timedelta(days=offset)).isoformat()].append(match)
    mapping: dict[str, tuple[Match, dict[str, Any], float, str]] = {}
    now = datetime.now(UTC)
    finished = {"finished", "ended", "cancelled", "canceled", "postponed", "abandoned"}
    for event in events:
        home, away, start, league = _event_fields(event)
        if not home or not away or start is None:
            stats["rows_missing_match_info"] += 1
            continue
        if _status(event) in finished and start < now - timedelta(minutes=15):
            stats["events_skipped_finished"] += 1
            continue
        candidates = by_date.get(start.date().isoformat(), [])
        if not candidates:
            stats["events_without_date_candidates"] += 1
            continue
        home_tokens = set(canonicalize_team_name(home).split())
        away_tokens = set(canonicalize_team_name(away).split())
        shortlist = [
            match
            for match in candidates
            if home_tokens.intersection(canonicalize_team_name(match.home_team).split())
            or away_tokens.intersection(canonicalize_team_name(match.away_team).split())
            or home_tokens.intersection(canonicalize_team_name(match.away_team).split())
            or away_tokens.intersection(canonicalize_team_name(match.home_team).split())
        ]
        if not shortlist:
            shortlist = sorted(
                candidates,
                key=lambda match: abs((match.commence_time.astimezone(UTC) - start).total_seconds()),
            )[:32]
        best: tuple[Match, float, str] | None = None
        for match in shortlist[:48]:
            try:
                score, quality = score_event_match(
                    sport="soccer",
                    match_home=match.home_team,
                    match_away=match.away_team,
                    match_start=match.commence_time,
                    match_league=match.league_name,
                    event_home=home,
                    event_away=away,
                    event_start=start,
                    event_league=league,
                    exact_tolerance_hours=12,
                    fuzzy_tolerance_hours=24,
                )
            except Exception:
                continue
            candidate = (match, float(score), str(quality or ""))
            if best is None or candidate[1] > best[1]:
                best = candidate
        if best is None or best[1] < 72.0 or not best[2]:
            if len(preview["unmatched"]) < 16:
                preview["unmatched"].append(
                    {"home": home, "away": away, "start": start.isoformat(), "best_score": best[1] if best else 0}
                )
            continue
        current = mapping.get(best[0].match_key)
        if current is None or best[1] > current[2]:
            mapping[best[0].match_key] = (best[0], _match_info(event), best[1], best[2])
            if len(preview["matched"]) < 16:
                preview["matched"].append(
                    {
                        "match_key": best[0].match_key,
                        "event_id": _event_id(event),
                        "score": round(best[1], 2),
                        "quality": best[2],
                    }
                )
    stats["events_matched"] = len(mapping)
    return mapping


async def _fetch_day(provider: Any, client: Any, date_key: str, stats: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    offset = 0
    limit = 1000
    for _ in range(3):
        payload = await provider._get_json(
            client,
            "/Pari/matches",
            {
                "date": date_key,
                "upcoming": "true",
                "timezone": _timezone_offset_hours(),
                "sortDesc": "false",
                "offset": offset,
                "limit": limit,
            },
            stats,
        )
        stats["list_requests"] += 1
        page = _extract_list(payload)
        rows.extend(page)
        total = _total_count(payload, len(rows))
        if not page or len(rows) >= total or len(page) < limit:
            break
        offset += len(page)
    return rows


async def _fetch_offers(
    self: Any, matches: list[Match]
) -> tuple[dict[str, list[Offer]], dict[str, Any], dict[str, Any]]:
    stats: dict[str, Any] = {
        "enabled": self.enabled,
        "api_key_present": bool(self.api_key),
        "requests": 0,
        "list_requests": 0,
        "detail_requests": 0,
        "cache_hits": 0,
        "response_errors": 0,
        "rows_fetched": 0,
        "rows_missing_match_info": 0,
        "events_without_date_candidates": 0,
        "events_skipped_finished": 0,
        "events_matched": 0,
        "details_fetched": 0,
        "offer_matches": 0,
        "offers_parsed": 0,
        "detail_limit": self.detail_limit,
        "concurrency": self.concurrency,
        "source": "sstats_pari",
        "independent_from_odds_api_io": True,
        "runtime_repair": "run_29634993958_v2",
    }
    preview: dict[str, Any] = {"matched": [], "unmatched": [], "samples": []}
    soccer = [match for match in matches if getattr(match, "sport_key", "") == "soccer"]
    if not self.enabled or not self.api_key or not soccer or self.detail_limit <= 0:
        self._export(stats, preview)
        return {}, stats, preview
    local_tz = _timezone()
    dates = sorted({match.commence_time.astimezone(local_tz).date().isoformat() for match in soccer})[:2]
    import httpx

    async with httpx.AsyncClient(
        timeout=self.timeout,
        follow_redirects=True,
        headers={"User-Agent": "HARIZON-sstats-pari/2.0"},
    ) as client:
        events: list[dict[str, Any]] = []
        for date_key in dates:
            events.extend(await _fetch_day(self, client, date_key, stats))
        stats["rows_fetched"] = len(events)
        mapping = _match_events(soccer, events, stats, preview)
        selected = sorted(
            mapping.values(),
            key=lambda item: (item[0].commence_time, -item[2], item[0].match_key),
        )[: self.detail_limit]
        semaphore = asyncio.Semaphore(self.concurrency)

        async def fetch_one(
            item: tuple[Match, dict[str, Any], float, str],
        ) -> tuple[Match, dict[str, Any], Any]:
            match, event, _score, _quality = item
            source_id = str(_event_id(event) or "").strip()
            if not source_id:
                return match, event, None
            async with semaphore:
                return match, event, await self._fetch_detail(client, source_id, event, stats)

        results = await asyncio.gather(*(fetch_one(item) for item in selected))
    offers_by_match: dict[str, list[Offer]] = defaultdict(list)
    from app.providers.sstats_pari_parser import parse_offers

    for match, event, payload in results:
        source_id = str(_event_id(event) or "")
        parsed = parse_offers(_extract_odds(payload), match, source_id)
        if not parsed:
            continue
        offers_by_match[match.match_key].extend(parsed)
        stats["offer_matches"] += 1
        stats["offers_parsed"] += len(parsed)
        if len(preview["samples"]) < 8:
            preview["samples"].append(
                {
                    "match_key": match.match_key,
                    "event_id": source_id,
                    "offers": [
                        {
                            "family": offer.family,
                            "selection": offer.selection,
                            "point": offer.point,
                            "price": offer.price,
                        }
                        for offer in parsed[:8]
                    ],
                }
            )
    result = {key: value for key, value in offers_by_match.items() if value}
    self._export(stats, preview)
    return result, stats, preview


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    from app.providers import sstats_pari_odds as provider_module
    from app.providers import sstats_pari_parser as parser_module

    parser_module.extract_list = _extract_list
    parser_module.extract_odds = _extract_odds
    parser_module.total_count = _total_count
    parser_module.event_id = _event_id
    parser_module.league_name = _league_name
    provider_module.extract_list = _extract_list
    provider_module.extract_odds = _extract_odds
    provider_module.total_count = _total_count
    provider_module.event_id = _event_id
    provider_module.league_name = _league_name
    provider_module.SStatsPariOddsProvider.fetch_offers = _fetch_offers
    _INSTALLED = True
    return {
        "status": "installed",
        "nested_match_info_supported": True,
        "upcoming_filter": True,
        "timezone_aware_dates": True,
        "strict_event_match_min_score": 72.0,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
