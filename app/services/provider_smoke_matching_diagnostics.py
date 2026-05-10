from __future__ import annotations

"""Cross-provider matching diagnostics for provider-smoke.

This script is diagnostic, not a publication gate.  It answers where provider
value is lost:

1. request / endpoint returned nothing;
2. parser could not extract teams/start;
3. direct future fixture rows do not overlap odds inventory;
4. normalization/time matching is weak;
5. historical providers, especially SStats, have enough team-form coverage.
"""

import asyncio
import json
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
MATCH_JSON = OUT_DIR / "latest-provider-smoke-matching-diagnostics.json"
MATCH_TXT = OUT_DIR / "latest-provider-smoke-matching-diagnostics.txt"

HISTORICAL_TEAM_FORM_PROVIDERS = {"sstats"}
DIRECT_FIXTURE_PROVIDERS = {"bzzoiro", "football_data", "sportlogic", "allsportsapi"}


@dataclass
class EventRow:
    provider: str
    home: str
    away: str
    league: str
    start: datetime | None
    source_id: str = ""
    raw_shape: str = ""

    def sample(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "home": self.home,
            "away": self.away,
            "league": self.league,
            "start": self.start.isoformat() if self.start else None,
            "source_id": self.source_id,
            "raw_shape": self.raw_shape,
        }


def _secret(*names: str) -> str:
    for name in names:
        value = str(os.getenv(name) or "").strip()
        if value:
            return value
    return ""


def _truthy(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _shape(value: Any) -> str:
    if isinstance(value, list):
        return "list"
    if isinstance(value, dict):
        return ",".join(sorted(str(k) for k in value.keys())[:12])
    return type(value).__name__


def _safe_json(response: httpx.Response) -> Any | None:
    try:
        return response.json()
    except Exception:
        return None


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "results", "result", "response", "fixtures", "matches", "events", "items", "games"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = _rows(value)
            if nested:
                return nested
    return []


def _dig(row: dict[str, Any], *path: str) -> Any:
    cur: Any = row
    for part in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def _text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "short_name", "shortName", "display_name", "displayName", "team_name", "club_name"):
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item.strip()
        return ""
    return str(value or "").strip()


def _first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = _dig(row, *key.split(".")) if "." in key else row.get(key)
        text = _text(value)
        if text:
            return text
    return ""


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        from app.utils import parse_datetime
        return parse_datetime(value)
    except Exception:
        return None


def _event_from_generic(provider: str, row: dict[str, Any]) -> EventRow | None:
    if provider == "odds_api_io":
        home = _first_text(row, ("home", "home_team", "homeTeam"))
        away = _first_text(row, ("away", "away_team", "awayTeam"))
        league = _first_text(row, ("league.name", "competition.name", "league", "competition"))
        start = _parse_dt(row.get("date") or row.get("commence_time") or row.get("start"))
        source_id = str(row.get("id") or "").strip()
    elif provider == "football_data":
        home = _first_text(row, ("homeTeam.name", "homeTeam.shortName", "home_team"))
        away = _first_text(row, ("awayTeam.name", "awayTeam.shortName", "away_team"))
        league = _first_text(row, ("competition.name", "competition.code", "league"))
        start = _parse_dt(row.get("utcDate") or row.get("date"))
        source_id = str(row.get("id") or "").strip()
    elif provider == "bzzoiro":
        event = row.get("event") if isinstance(row.get("event"), dict) else row
        home = _first_text(event, ("home_team", "home_team_obj.name", "home.name", "home"))
        away = _first_text(event, ("away_team", "away_team_obj.name", "away.name", "away"))
        league = _first_text(event, ("league.name", "league", "competition.name"))
        start = _parse_dt(event.get("event_date") or event.get("date") or event.get("start"))
        source_id = str(event.get("id") or row.get("id") or "").strip()
    elif provider == "allsportsapi":
        home = _first_text(row, ("event_home_team", "home_team", "home"))
        away = _first_text(row, ("event_away_team", "away_team", "away"))
        league = _first_text(row, ("league_name", "league", "competition"))
        raw_date = row.get("event_date") or row.get("date")
        raw_time = row.get("event_time") or row.get("time") or "00:00"
        start = _parse_dt(f"{raw_date}T{raw_time}:00+00:00") if raw_date else None
        source_id = str(row.get("event_key") or row.get("match_id") or row.get("id") or "").strip()
    else:
        home = _first_text(row, (
            "home_team", "homeTeam", "home.name", "home_team.name", "home_team_obj.name", "localteam.name", "localTeam.name", "team_home.name", "home",
        ))
        away = _first_text(row, (
            "away_team", "awayTeam", "away.name", "away_team.name", "away_team_obj.name", "visitorteam.name", "visitorTeam.name", "team_away.name", "away",
        ))
        league = _first_text(row, ("league.name", "competition.name", "tournament.name", "league", "competition", "tournament"))
        start = _parse_dt(
            row.get("date") or row.get("event_date") or row.get("start") or row.get("starts_at") or row.get("start_time") or row.get("kickoff") or row.get("commence_time")
        )
        source_id = str(row.get("id") or row.get("game_id") or row.get("fixture_id") or row.get("match_id") or "").strip()
    if not home or not away:
        return None
    return EventRow(provider=provider, home=home, away=away, league=league, start=start, source_id=source_id, raw_shape=_shape(row))


async def _get(client: httpx.AsyncClient, url: str, *, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> tuple[Any | None, dict[str, Any]]:
    started = datetime.now(UTC)
    try:
        response = await client.get(url, params=params or None, headers=headers or None)
    except Exception as exc:
        return None, {"ok": False, "status": "request_error", "error": f"{type(exc).__name__}: {exc}", "url": url, "params_keys": sorted((params or {}).keys())}
    payload = _safe_json(response)
    return payload, {
        "ok": response.status_code == 200,
        "http_status": response.status_code,
        "url": url,
        "params_keys": sorted((params or {}).keys()),
        "payload_shape": _shape(payload),
        "body_preview": response.text[:350],
        "duration_ms": round((datetime.now(UTC) - started).total_seconds() * 1000.0, 1),
    }


async def _fetch_odds_inventory(client: httpx.AsyncClient) -> dict[str, Any]:
    key = _secret("ODDS_API_IO_KEY")
    if not key:
        return {"provider": "odds_api_io", "status": "missing_key", "raw_rows": 0, "events": [], "attempts": []}
    now = datetime.now(UTC)
    limit = max(20, int(float(os.getenv("PROVIDER_SMOKE_MATCHING_ODDS_LIMIT") or 100)))
    pages = max(1, int(float(os.getenv("PROVIDER_SMOKE_MATCHING_ODDS_PAGES") or 3)))
    days_ahead = max(1, int(float(os.getenv("PROVIDER_SMOKE_MATCHING_DAYS_AHEAD") or 2)))
    attempts: list[dict[str, Any]] = []
    raw_rows: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for page in range(1, pages + 1):
        params = {
            "apiKey": key,
            "sport": "football",
            "status": "pending,live",
            "from": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "to": (now + timedelta(days=days_ahead)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "limit": limit,
            "page": page,
        }
        payload, attempt = await _get(client, "https://api.odds-api.io/v3/events", params=params)
        attempts.append(attempt)
        batch = _rows(payload)
        if not batch:
            break
        for row in batch:
            event_id = str(row.get("id") or "").strip() or repr(sorted(row.items()))[:200]
            if event_id in seen_ids:
                continue
            seen_ids.add(event_id)
            raw_rows.append(row)
        if len(batch) < limit:
            break
    events = [event for row in raw_rows if (event := _event_from_generic("odds_api_io", row)) is not None and event.start is not None]
    return {
        "provider": "odds_api_io",
        "status": "ok" if any(a.get("ok") for a in attempts) else "request_failed",
        "raw_rows": len(raw_rows),
        "parsed_events": len(events),
        "pages_requested": len(attempts),
        "events": events,
        "samples": [event.sample() for event in events[:10]],
        "attempts": attempts,
    }


async def _fetch_provider_rows(client: httpx.AsyncClient, provider: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    today = now.date().isoformat()
    tomorrow = (now + timedelta(days=1)).date().isoformat()
    day_after = (now + timedelta(days=2)).date().isoformat()
    lookback = max(7, int(float(os.getenv("PROVIDER_SMOKE_SSTATS_LOOKBACK_DAYS") or 30)))
    historical_from = (now - timedelta(days=lookback)).date().isoformat()
    attempts: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    status = "not_run"

    if provider == "sstats":
        key = _secret("SSTATS_API_KEY")
        if not key:
            return {"provider": provider, "status": "missing_key", "raw_rows": 0, "events": [], "attempts": []}
        payload, attempt = await _get(client, "https://api.sstats.net/Games/list", params={"from": historical_from, "to": tomorrow, "limit": 1000, "offset": 0, "apikey": key})
        attempts.append(attempt)
        rows = _rows(payload)
        status = "ok" if attempt.get("ok") else "request_failed"
    elif provider == "bzzoiro":
        key = _secret("BZZOIRO_API_KEY")
        if not key:
            return {"provider": provider, "status": "missing_key", "raw_rows": 0, "events": [], "attempts": []}
        if not _truthy("BZZOIRO_PROVIDER_SMOKE_ENABLED", True):
            return {"provider": provider, "status": "skipped_by_env", "raw_rows": 0, "events": [], "attempts": []}
        payload, attempt = await _get(client, "https://sports.bzzoiro.com/api/predictions/", params={"date_from": today, "date_to": day_after, "upcoming": "true", "tz": "UTC", "page": 1}, headers={"Authorization": f"Token {key}"})
        attempts.append(attempt)
        rows = _rows(payload)
        status = "ok" if attempt.get("ok") else "request_failed"
    elif provider == "football_data":
        key = _secret("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY")
        if not key:
            return {"provider": provider, "status": "missing_key", "raw_rows": 0, "events": [], "attempts": []}
        payload, attempt = await _get(client, "https://api.football-data.org/v4/matches", params={"dateFrom": today, "dateTo": day_after}, headers={"X-Auth-Token": key})
        attempts.append(attempt)
        rows = _rows(payload)
        status = "ok" if attempt.get("ok") else "request_failed"
    elif provider == "sportlogic":
        key = _secret("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN")
        if not key:
            return {"provider": provider, "status": "missing_key", "raw_rows": 0, "events": [], "attempts": []}
        base = str(os.getenv("SPORTLOGIC_BASE_URL") or "https://api.sportlogic.io/api/v1").rstrip("/")
        headers = {str(os.getenv("SPORTLOGIC_HEADER_NAME") or "X-API-Key"): key}
        variants = [
            {"date_from": today, "date_to": day_after, "per_page": 100},
            {"from": today, "to": day_after, "per_page": 100},
            {"start_date": today, "end_date": day_after, "per_page": 100},
            {"date": today, "per_page": 100},
            {"per_page": 100},
        ]
        for params in variants:
            payload, attempt = await _get(client, f"{base}/games", params=params, headers=headers)
            attempts.append(attempt)
            batch = _rows(payload)
            if batch:
                rows = batch
                status = "ok"
                break
        if not rows:
            status = "empty" if any(a.get("ok") for a in attempts) else "request_failed"
    elif provider == "allsportsapi":
        key = _secret("ALLSPORTSAPI_API_KEY", "ALLSPORTSAPI_KEY")
        if not key:
            return {"provider": provider, "status": "missing_key", "raw_rows": 0, "events": [], "attempts": []}
        payload, attempt = await _get(client, "https://apiv2.allsportsapi.com/football/", params={"met": "Fixtures", "APIkey": key, "from": today, "to": day_after, "timezone": "UTC"})
        attempts.append(attempt)
        rows = _rows(payload)
        status = "ok" if attempt.get("ok") else "request_failed"
    else:
        return {"provider": provider, "status": "unsupported", "raw_rows": 0, "events": [], "attempts": []}

    events: list[EventRow] = []
    missing_team = 0
    missing_start = 0
    for row in rows[:1200]:
        event = _event_from_generic(provider, row)
        if event is None:
            missing_team += 1
            continue
        if event.start is None:
            missing_start += 1
        events.append(event)
    return {
        "provider": provider,
        "status": status,
        "raw_rows": len(rows),
        "parsed_events": len(events),
        "missing_team_rows": missing_team,
        "missing_start_rows": missing_start,
        "events": events,
        "samples": [event.sample() for event in events[:8]],
        "attempts": attempts,
    }


def _inventory_window(inventory: list[EventRow], slack_hours: float = 18.0) -> tuple[datetime | None, datetime | None]:
    starts = [event.start for event in inventory if event.start is not None]
    if not starts:
        return None, None
    return min(starts) - timedelta(hours=slack_hours), max(starts) + timedelta(hours=slack_hours)


def _filter_to_window(events: list[EventRow], start: datetime | None, end: datetime | None) -> list[EventRow]:
    if start is None or end is None:
        return events
    return [event for event in events if event.start is not None and start <= event.start <= end]


def _team_form_coverage(provider_payload: dict[str, Any], inventory: list[EventRow]) -> dict[str, Any]:
    from app.utils import canonicalize_team_name, team_similarity

    events: list[EventRow] = [event for event in list(provider_payload.get("events") or []) if event.start is not None]
    team_names: list[str] = []
    team_keys: set[str] = set()
    for event in events:
        for name in (event.home, event.away):
            key = canonicalize_team_name(name)
            if key:
                team_keys.add(key)
                team_names.append(name)

    def covered(name: str) -> bool:
        key = canonicalize_team_name(name)
        if key in team_keys:
            return True
        return any(team_similarity(name, candidate) >= 0.88 for candidate in team_names[:2500])

    both = 0
    one = 0
    missing_samples: list[dict[str, Any]] = []
    covered_samples: list[dict[str, Any]] = []
    for match in inventory:
        home_ok = covered(match.home)
        away_ok = covered(match.away)
        if home_ok and away_ok:
            both += 1
            if len(covered_samples) < 5:
                covered_samples.append({"match": match.sample(), "home_history": True, "away_history": True})
        elif home_ok or away_ok:
            one += 1
        elif len(missing_samples) < 8:
            missing_samples.append({
                "match": match.sample(),
                "home_norm": canonicalize_team_name(match.home),
                "away_norm": canonicalize_team_name(match.away),
            })

    total = len(inventory)
    both_rate = round((both / total) * 100.0, 1) if total else 0.0
    any_rate = round(((both + one) / total) * 100.0, 1) if total else 0.0
    if int(provider_payload.get("raw_rows") or 0) <= 0:
        stage = "request_or_empty_query"
    elif not events:
        stage = "parser_extract_failed"
    elif both_rate >= 40.0 or both >= 8:
        stage = "team_form_coverage_ok"
    elif both_rate >= 15.0 or any_rate >= 35.0:
        stage = "team_form_coverage_partial"
    else:
        stage = "team_form_coverage_low"
    return {
        "provider": provider_payload.get("provider"),
        "status": provider_payload.get("status"),
        "provider_role": "historical_team_form",
        "raw_rows": int(provider_payload.get("raw_rows") or 0),
        "parsed_events": int(provider_payload.get("parsed_events") or len(events)),
        "eligible_events": len(events),
        "inventory_matches": total,
        "matched_to_odds_inventory": both,
        "team_form_both_teams": both,
        "team_form_one_team": one,
        "team_form_both_rate_pct": both_rate,
        "team_form_any_rate_pct": any_rate,
        "match_rate_pct": both_rate,
        "failure_stage": stage,
        "samples": provider_payload.get("samples", [])[:5],
        "matched_samples": covered_samples,
        "unmatched_samples": missing_samples,
        "attempts": provider_payload.get("attempts", [])[:6],
    }


def _match_provider_to_inventory(provider_payload: dict[str, Any], inventory: list[EventRow]) -> dict[str, Any]:
    from app.utils import canonicalize_league_name, canonicalize_team_name, score_event_match

    provider = str(provider_payload.get("provider") or "")
    if provider in HISTORICAL_TEAM_FORM_PROVIDERS:
        return _team_form_coverage(provider_payload, inventory)

    all_events: list[EventRow] = list(provider_payload.get("events") or [])
    window_start, window_end = _inventory_window(inventory)
    events = _filter_to_window(all_events, window_start, window_end)
    exact = loose = fuzzy = 0
    matched = 0
    score_sum = 0.0
    unmatched_samples: list[dict[str, Any]] = []
    matched_samples: list[dict[str, Any]] = []
    threshold = 54.0
    for event in events:
        if event.start is None:
            continue
        best_score = 0.0
        best_quality: str | None = None
        best_inv: EventRow | None = None
        for inv in inventory:
            if inv.start is None:
                continue
            score, quality = score_event_match(
                sport="soccer",
                match_home=inv.home,
                match_away=inv.away,
                match_start=inv.start,
                match_league=inv.league,
                event_home=event.home,
                event_away=event.away,
                event_start=event.start,
                event_league=event.league,
                exact_tolerance_hours=12,
                fuzzy_tolerance_hours=18,
            )
            if score > best_score:
                best_score = float(score)
                best_quality = quality
                best_inv = inv
        if best_score >= threshold and best_inv is not None:
            matched += 1
            score_sum += best_score
            if best_quality == "exact":
                exact += 1
            elif best_quality == "loose":
                loose += 1
            elif best_quality == "fuzzy":
                fuzzy += 1
            if len(matched_samples) < 5:
                matched_samples.append({"provider_event": event.sample(), "odds_event": best_inv.sample(), "score": round(best_score, 2), "quality": best_quality})
        elif len(unmatched_samples) < 8:
            unmatched_samples.append({
                "provider_event": event.sample(),
                "best_score": round(best_score, 2),
                "best_quality": best_quality,
                "best_odds_event": best_inv.sample() if best_inv else None,
                "provider_norm": {
                    "home": canonicalize_team_name(event.home),
                    "away": canonicalize_team_name(event.away),
                    "league": canonicalize_league_name(event.league),
                },
                "odds_norm": {
                    "home": canonicalize_team_name(best_inv.home) if best_inv else None,
                    "away": canonicalize_team_name(best_inv.away) if best_inv else None,
                    "league": canonicalize_league_name(best_inv.league) if best_inv else None,
                },
            })
    parsed = int(provider_payload.get("parsed_events") or len(all_events))
    raw_rows = int(provider_payload.get("raw_rows") or 0)
    eligible = len(events)
    if raw_rows <= 0:
        failure_stage = "request_or_empty_query"
    elif parsed <= 0:
        failure_stage = "parser_extract_failed"
    elif eligible <= 0:
        failure_stage = "no_fixture_overlap_with_odds_inventory"
    elif matched <= 0:
        failure_stage = "normalization_or_time_matching_failed"
    elif matched < max(1, int(eligible * 0.35)):
        failure_stage = "partial_matching_low_yield"
    else:
        failure_stage = "matching_ok"
    return {
        "provider": provider,
        "status": provider_payload.get("status"),
        "provider_role": "direct_fixture_or_prediction",
        "raw_rows": raw_rows,
        "parsed_events": parsed,
        "eligible_events": eligible,
        "events_outside_inventory_window": max(0, parsed - eligible),
        "missing_team_rows": provider_payload.get("missing_team_rows", 0),
        "missing_start_rows": provider_payload.get("missing_start_rows", 0),
        "matched_to_odds_inventory": matched,
        "matched_exact": exact,
        "matched_loose": loose,
        "matched_fuzzy": fuzzy,
        "match_rate_pct": round((matched / eligible) * 100.0, 1) if eligible else 0.0,
        "avg_matched_score": round(score_sum / matched, 2) if matched else 0.0,
        "failure_stage": failure_stage,
        "samples": provider_payload.get("samples", [])[:5],
        "matched_samples": matched_samples,
        "unmatched_samples": unmatched_samples,
        "attempts": provider_payload.get("attempts", [])[:6],
    }


def _inventory_duplicates(inventory: list[EventRow]) -> list[dict[str, Any]]:
    from app.utils import canonicalize_team_name

    buckets: dict[tuple[str, str], list[EventRow]] = {}
    for event in inventory:
        key = tuple(sorted([canonicalize_team_name(event.home), canonicalize_team_name(event.away)]))
        buckets.setdefault(key, []).append(event)
    out: list[dict[str, Any]] = []
    for key, items in buckets.items():
        raw_names = sorted({f"{event.home} — {event.away}" for event in items})
        if len(raw_names) > 1:
            out.append({"canonical_pair": list(key), "raw_names": raw_names[:8], "count": len(items)})
    return out[:12]


def _diagnosis_note(item: dict[str, Any]) -> str:
    stage = item.get("failure_stage")
    provider = item.get("provider")
    if stage == "request_or_empty_query":
        return f"{provider}: запрос живой/неживой надо смотреть по attempts; данных нет или окно слишком узкое."
    if stage == "parser_extract_failed":
        return f"{provider}: API вернул rows, но парсер не достал home/away/start. Нужно расширять extractor под sample payload."
    if stage == "no_fixture_overlap_with_odds_inventory":
        return f"{provider}: provider работает, но в текущем smoke нет пересечения с odds inventory. Это не ошибка матчинга; нужно расширить inventory или проверять runtime-окно."
    if stage == "normalization_or_time_matching_failed":
        return f"{provider}: события в том же окне есть, но не матчятся к odds inventory. Чинить алиасы команд/лиг, timezone или start-time tolerance."
    if stage == "partial_matching_low_yield":
        return f"{provider}: матчинг частичный. Нужно добить алиасы и проверить дату/лигу, чтобы поднять yield."
    if stage == "team_form_coverage_ok":
        return f"{provider}: historical/team-form покрытие нормальное; источник надо использовать как форму команд, а не прямой fixture-match."
    if stage == "team_form_coverage_partial":
        return f"{provider}: team-form покрытие частичное; стоит увеличить lookback/limit и улучшить team aliases."
    if stage == "team_form_coverage_low":
        return f"{provider}: team-form покрытие низкое; вероятно, историческое окно/лигa не совпадают с odds inventory."
    return f"{provider}: матчинг выглядит рабочим; дальше проверять runtime wiring и попадание context/offers в модель."


def _render(payload: dict[str, Any]) -> str:
    inv = payload.get("odds_inventory", {})
    lines = [
        "🧬 Provider matching diagnostics",
        f"• UTC: {payload.get('created_at_utc')}",
        f"• odds inventory: rows {inv.get('raw_rows', 0)} | parsed {inv.get('parsed_events', 0)} | pages {inv.get('pages_requested', 0)} | status {inv.get('status')}",
        f"• duplicate canonical pairs in odds inventory: {len(payload.get('inventory_duplicate_pairs') or [])}",
        "",
        "| provider | role | status | raw | parsed | eligible | matched | rate | stage |",
        "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in payload.get("providers", []):
        lines.append(
            f"| {item.get('provider')} | {item.get('provider_role')} | {item.get('status')} | {item.get('raw_rows')} | {item.get('parsed_events')} | "
            f"{item.get('eligible_events', '')} | {item.get('matched_to_odds_inventory')} | {item.get('match_rate_pct')}% | {item.get('failure_stage')} |"
        )
    lines.append("")
    lines.append("🔧 Notes")
    for item in payload.get("providers", []):
        lines.append(f"• {_diagnosis_note(item)}")
    duplicates = payload.get("inventory_duplicate_pairs") or []
    if duplicates:
        lines.append("")
        lines.append("⚠️ Odds inventory duplicate aliases")
        for row in duplicates[:8]:
            lines.append(f"• {' / '.join(row.get('canonical_pair') or [])}: {', '.join(row.get('raw_names') or [])}")
    lines.append("")
    lines.append("📎 Send me this text plus latest-provider-smoke-diagnostics.json for exact parser/matching fixes.")
    return "\n".join(lines)


async def run(timeout_seconds: float | None = None) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from app.services import provider_matching_alias_runtime_patch
        provider_matching_alias_runtime_patch.install()
    except Exception:
        pass

    timeout_value = float(timeout_seconds or os.getenv("PROVIDER_SMOKE_MATCHING_TIMEOUT_SECONDS") or 18.0)
    timeout = httpx.Timeout(timeout_value, connect=min(5.0, timeout_value))
    providers = [p.strip() for p in str(os.getenv("PROVIDER_SMOKE_MATCHING_PROVIDERS") or "sstats,bzzoiro,football_data,sportlogic,allsportsapi").split(",") if p.strip()]
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        inventory_payload = await _fetch_odds_inventory(client)
        tasks = [_fetch_provider_rows(client, provider) for provider in providers]
        provider_payloads = await asyncio.gather(*tasks)

    inventory = list(inventory_payload.get("events") or [])
    provider_reports = [_match_provider_to_inventory(payload, inventory) for payload in provider_payloads]
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "provider_smoke_matching_diagnostics",
        "odds_inventory": {key: value for key, value in inventory_payload.items() if key != "events"},
        "inventory_duplicate_pairs": _inventory_duplicates(inventory),
        "providers": provider_reports,
    }
    MATCH_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    MATCH_TXT.write_text(_render(payload) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2, sort_keys=True))
