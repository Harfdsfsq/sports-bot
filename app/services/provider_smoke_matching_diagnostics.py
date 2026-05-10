from __future__ import annotations

"""Cross-provider matching diagnostics for provider-smoke.

This module is intentionally independent from the full bot runtime.  It uses a
small number of direct HTTP probes and the same app.utils matching helpers to
answer the practical question: does a provider fail at request, parser, normalizer,
fixture matching, or runtime integration?
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
        if "." in key:
            value = _dig(row, *key.split("."))
        else:
            value = row.get(key)
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
            row.get("date") or row.get("start") or row.get("starts_at") or row.get("start_time") or row.get("kickoff") or row.get("commence_time")
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
    limit = max(5, int(float(os.getenv("PROVIDER_SMOKE_MATCHING_ODDS_LIMIT") or 16)))
    params = {
        "apiKey": key,
        "sport": "football",
        "status": "pending,live",
        "from": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "to": (now + timedelta(days=1)).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "limit": limit,
        "page": 1,
    }
    payload, attempt = await _get(client, "https://api.odds-api.io/v3/events", params=params)
    rows = _rows(payload)
    events = [event for row in rows if (event := _event_from_generic("odds_api_io", row)) is not None and event.start is not None]
    return {
        "provider": "odds_api_io",
        "status": "ok" if attempt.get("ok") else "request_failed",
        "raw_rows": len(rows),
        "parsed_events": len(events),
        "events": events,
        "samples": [event.sample() for event in events[:8]],
        "attempts": [attempt],
    }


async def _fetch_provider_rows(client: httpx.AsyncClient, provider: str) -> dict[str, Any]:
    now = datetime.now(UTC)
    today = now.date().isoformat()
    tomorrow = (now + timedelta(days=1)).date().isoformat()
    yesterday = (now - timedelta(days=1)).date().isoformat()
    attempts: list[dict[str, Any]] = []
    rows: list[dict[str, Any]] = []
    status = "not_run"

    if provider == "sstats":
        key = _secret("SSTATS_API_KEY")
        if not key:
            return {"provider": provider, "status": "missing_key", "raw_rows": 0, "events": [], "attempts": []}
        payload, attempt = await _get(client, "https://api.sstats.net/Games/list", params={"from": yesterday, "to": tomorrow, "limit": 80, "offset": 0, "apikey": key})
        attempts.append(attempt)
        rows = _rows(payload)
        status = "ok" if attempt.get("ok") else "request_failed"
    elif provider == "bzzoiro":
        key = _secret("BZZOIRO_API_KEY")
        if not key:
            return {"provider": provider, "status": "missing_key", "raw_rows": 0, "events": [], "attempts": []}
        if not _truthy("BZZOIRO_PROVIDER_SMOKE_ENABLED", True):
            return {"provider": provider, "status": "skipped_by_env", "raw_rows": 0, "events": [], "attempts": []}
        payload, attempt = await _get(client, "https://sports.bzzoiro.com/api/predictions/", params={"date_from": today, "date_to": tomorrow, "upcoming": "true", "tz": "UTC", "page": 1}, headers={"Authorization": f"Token {key}"})
        attempts.append(attempt)
        rows = _rows(payload)
        status = "ok" if attempt.get("ok") else "request_failed"
    elif provider == "football_data":
        key = _secret("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY")
        if not key:
            return {"provider": provider, "status": "missing_key", "raw_rows": 0, "events": [], "attempts": []}
        payload, attempt = await _get(client, "https://api.football-data.org/v4/matches", params={"dateFrom": today, "dateTo": tomorrow}, headers={"X-Auth-Token": key})
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
            {"date_from": today, "date_to": tomorrow, "per_page": 80},
            {"from": today, "to": tomorrow, "per_page": 80},
            {"date": today, "per_page": 80},
            {"per_page": 80},
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
        payload, attempt = await _get(client, "https://apiv2.allsportsapi.com/football/", params={"met": "Fixtures", "APIkey": key, "from": today, "to": tomorrow, "timezone": "UTC"})
        attempts.append(attempt)
        rows = _rows(payload)
        status = "ok" if attempt.get("ok") else "request_failed"
    else:
        return {"provider": provider, "status": "unsupported", "raw_rows": 0, "events": [], "attempts": []}

    events: list[EventRow] = []
    missing_team = 0
    missing_start = 0
    for row in rows[:160]:
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


def _match_provider_to_inventory(provider_payload: dict[str, Any], inventory: list[EventRow]) -> dict[str, Any]:
    from app.utils import canonicalize_league_name, canonicalize_team_name, score_event_match

    events: list[EventRow] = list(provider_payload.get("events") or [])
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
                fuzzy_tolerance_hours=12,
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
    parsed = int(provider_payload.get("parsed_events") or len(events))
    raw_rows = int(provider_payload.get("raw_rows") or 0)
    if raw_rows <= 0:
        failure_stage = "request_or_empty_query"
    elif parsed <= 0:
        failure_stage = "parser_extract_failed"
    elif matched <= 0:
        failure_stage = "normalization_or_time_matching_failed"
    elif matched < max(1, int(parsed * 0.35)):
        failure_stage = "partial_matching_low_yield"
    else:
        failure_stage = "matching_ok"
    return {
        "provider": provider_payload.get("provider"),
        "status": provider_payload.get("status"),
        "raw_rows": raw_rows,
        "parsed_events": parsed,
        "missing_team_rows": provider_payload.get("missing_team_rows", 0),
        "missing_start_rows": provider_payload.get("missing_start_rows", 0),
        "matched_to_odds_inventory": matched,
        "matched_exact": exact,
        "matched_loose": loose,
        "matched_fuzzy": fuzzy,
        "match_rate_pct": round((matched / parsed) * 100.0, 1) if parsed else 0.0,
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
        raw_names = sorted({f"{e.home} — {e.away}" for e in items})
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
    if stage == "normalization_or_time_matching_failed":
        return f"{provider}: события распарсились, но не матчятся к odds inventory. Чинить алиасы команд/лиг или tolerance времени."
    if stage == "partial_matching_low_yield":
        return f"{provider}: матчинг частичный. Нужно добить алиасы и проверить дату/лигy, чтобы поднять yield."
    return f"{provider}: матчинг выглядит рабочим; дальше проверять runtime wiring и попадание context/offers в модель."


def _render(payload: dict[str, Any]) -> str:
    inv = payload.get("odds_inventory", {})
    lines = [
        "🧬 Provider matching diagnostics",
        f"• UTC: {payload.get('created_at_utc')}",
        f"• odds inventory: rows {inv.get('raw_rows', 0)} | parsed {inv.get('parsed_events', 0)} | status {inv.get('status')}",
        f"• duplicate canonical pairs in odds inventory: {len(payload.get('inventory_duplicate_pairs') or [])}",
        "",
        "| provider | status | raw | parsed | matched | rate | stage |",
        "| --- | --- | ---: | ---: | ---: | ---: | --- |",
    ]
    for item in payload.get("providers", []):
        lines.append(
            f"| {item.get('provider')} | {item.get('status')} | {item.get('raw_rows')} | {item.get('parsed_events')} | "
            f"{item.get('matched_to_odds_inventory')} | {item.get('match_rate_pct')}% | {item.get('failure_stage')} |"
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
    lines.append("📎 Send me this text plus latest-provider-smoke-diagnostics.json if you want exact parser fixes.")
    return "\n".join(lines)


async def run(timeout_seconds: float | None = None) -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    try:
        from app.services import provider_matching_alias_runtime_patch
        provider_matching_alias_runtime_patch.install()
    except Exception:
        pass

    timeout_value = float(timeout_seconds or os.getenv("PROVIDER_SMOKE_MATCHING_TIMEOUT_SECONDS") or 14.0)
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
        "odds_inventory": {
            key: value for key, value in inventory_payload.items() if key != "events"
        },
        "inventory_duplicate_pairs": _inventory_duplicates(inventory),
        "providers": provider_reports,
    }
    MATCH_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    MATCH_TXT.write_text(_render(payload) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    print(json.dumps(asyncio.run(run()), ensure_ascii=False, indent=2, sort_keys=True))
