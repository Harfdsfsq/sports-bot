from __future__ import annotations

"""v8 adapter for broad full-data smoke.

Keeps the v7 endpoint matrix, but improves three diagnostic blind spots:
- TheSportsDB rows are now recognized as matchable events.
- Empty wrapper payloads like {success: 1} are not counted as data rows.
- SportLogic active odds rows are mined for game ids and followed to /games/{id}.
"""

import asyncio
from typing import Any

from scripts import api_full_data_smoke_probe_v7 as base

_ORIG_ROWS = base.rows
_ORIG_EVENT_LIKE = base.event_like
_ORIG_IDS_FROM = base.ids_from
_ORIG_DETAIL_CALLS = base.detail_calls

WRAPPER_ONLY_KEYS = {"success", "status", "error", "message", "msg", "code"}
ID_KEYS = {"id", "game_id", "gameId", "fixture_id", "fixtureId", "event_id", "eventId", "match_id", "matchId"}
ENTITY_KEYS = {"game", "fixture", "event", "match"}


def rows(payload: Any) -> list[Any]:
    if isinstance(payload, dict):
        lowered = {str(k).strip() for k in payload.keys()}
        if lowered and lowered.issubset(WRAPPER_ONLY_KEYS):
            return []
        for key in ("result", "data", "events", "matches", "fixtures", "games", "response"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = rows(value)
                if nested:
                    return nested
            if value in (None, "", [], {}):
                continue
    return _ORIG_ROWS(payload)


def event_like(row: Any, provider: str) -> dict[str, str] | None:
    if not isinstance(row, dict):
        return None
    if provider == "thesportsdb":
        home = base.first(row, ("strHomeTeam", "home_team", "home"))
        away = base.first(row, ("strAwayTeam", "away_team", "away"))
        if not home or not away:
            return None
        start = str(row.get("strTimestamp") or "")
        if not start and row.get("dateEvent"):
            start = f"{row.get('dateEvent')}T{row.get('strTime') or '00:00:00'}+00:00"
        return {
            "home": home,
            "away": away,
            "league": str(row.get("strLeague") or ""),
            "start": start,
            "source_id": str(row.get("idEvent") or ""),
        }
    if provider == "allsportsapi":
        home = base.first(row, ("event_home_team", "home_team", "home", "homeTeam"))
        away = base.first(row, ("event_away_team", "away_team", "away", "awayTeam"))
        if not home or not away:
            return None
        start = ""
        if row.get("event_date"):
            start = f"{row.get('event_date')}T{row.get('event_time') or '00:00'}:00+00:00"
        return {
            "home": home,
            "away": away,
            "league": str(row.get("league_name") or row.get("league") or ""),
            "start": start,
            "source_id": str(row.get("event_key") or row.get("id") or row.get("match_id") or ""),
        }
    return _ORIG_EVENT_LIKE(row, provider)


def id_candidates(value: Any, depth: int = 0) -> list[str]:
    if depth > 5:
        return []
    out: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            low = str(key).strip()
            low_norm = low.lower()
            if low in ID_KEYS or low_norm in {k.lower() for k in ID_KEYS}:
                if item not in (None, "") and not isinstance(item, (dict, list)):
                    out.append(str(item).strip())
            elif low_norm in ENTITY_KEYS and isinstance(item, dict):
                for subkey in ID_KEYS:
                    sub = item.get(subkey)
                    if sub not in (None, ""):
                        out.append(str(sub).strip())
                out.extend(id_candidates(item, depth + 1))
            elif isinstance(item, (dict, list)):
                out.extend(id_candidates(item, depth + 1))
    elif isinstance(value, list):
        for item in value[:40]:
            out.extend(id_candidates(item, depth + 1))
    uniq: list[str] = []
    for item in out:
        if item and item.lower() not in {"none", "null", "true", "false"} and item not in uniq:
            uniq.append(item)
    return uniq


def ids_from(result: dict[str, Any], provider: str) -> list[str]:
    direct = _ORIG_IDS_FROM(result, provider)
    if direct:
        return direct
    out: list[str] = []
    for row in result.get("sample") or []:
        for item in id_candidates(row):
            if item not in out:
                out.append(item)
        if len(out) >= 12:
            break
    return out


async def detail_calls(client, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    details = await _ORIG_DETAIL_CALLS(client, results)
    if any(str(item.get("provider")) == "sportlogic" and str(item.get("command")) == "game_detail" for item in details):
        return details
    by = {(r.get("provider"), r.get("command")): r for r in results}
    active = by.get(("sportlogic", "active_odds")) or {}
    game_ids = ids_from(active, "sportlogic")[:3]
    key = base.env("SPORTLOGIC_API_KEY") or base.env("SPORTLOGIC_KEY") or base.env("SPORTLOGIC_TOKEN")
    if not game_ids or not key:
        return details
    root = base.env("SPORTLOGIC_BASE_URL", "https://api.sportlogic.io/api/v1").rstrip("/")
    headers = {(base.env("SPORTLOGIC_HEADER_NAME", "X-API-Key") or "X-API-Key"): key}
    specs: list[base.CallSpec] = []
    for gid in game_ids:
        specs.append(base.CallSpec("sportlogic", "active_game_detail", f"{root}/games/{gid}", "fixture_detail_from_active_odds", {}, headers))
        specs.append(base.CallSpec("sportlogic", "active_game_odds", f"{root}/games/{gid}/odds", "odds_detail_from_active_odds", {}, headers))
        specs.append(base.CallSpec("sportlogic", "active_game_outcomes", f"{root}/outcomes/{gid}", "settlement_from_active_odds", {}, headers))
    sem = asyncio.Semaphore(max(1, base.as_int(base.os.getenv("API_FULL_SMOKE_DETAIL_CONCURRENCY"), 3)))

    async def guarded(spec: base.CallSpec) -> dict[str, Any]:
        async with sem:
            return await base.call(client, spec)

    extra = await asyncio.gather(*(guarded(spec) for spec in specs))
    return details + list(extra)


def install() -> None:
    base.rows = rows
    base.event_like = event_like
    base.ids_from = ids_from
    base.detail_calls = detail_calls


async def run() -> dict[str, Any]:
    install()
    payload = await base.run()
    payload["mode"] = "api_full_data_smoke_probe_v8"
    payload.setdefault("notes", []).append("v8: TheSportsDB/AllSportsAPI row parsing and SportLogic active-odds detail probing enabled.")
    base.JSON_OUT.write_text(base.json.dumps(base.safe(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    base.TXT_OUT.write_text(base.render(payload), encoding="utf-8")
    return payload


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
