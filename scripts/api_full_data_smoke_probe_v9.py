from __future__ import annotations

"""v9 adapter for provider-smoke full-data diagnostics.

Fixes SportLogic active-odds detail probing. Active odds rows expose both the
odds row id and game_id; v8 could follow the odds id and receive 404 from
/games/{id}. v9 always prefers game_id or nested game.id before generic ids.
"""

import asyncio
from typing import Any

from scripts import api_full_data_smoke_probe_v8 as v8
from scripts import api_full_data_smoke_probe_v7 as base

_BASE_RENDER = base.render


def _unique(values: list[str]) -> list[str]:
    out: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if text and text.lower() not in {"none", "null", "true", "false"} and text not in out:
            out.append(text)
    return out


def _sportlogic_game_ids_from_row(row: Any) -> list[str]:
    if not isinstance(row, dict):
        return []
    ids: list[str] = []
    for key in ("game_id", "gameId", "fixture_id", "fixtureId", "event_id", "eventId", "match_id", "matchId"):
        value = row.get(key)
        if value not in (None, ""):
            ids.append(str(value).strip())
    for key in ("game", "fixture", "event", "match"):
        value = row.get(key)
        if isinstance(value, dict):
            for subkey in ("id", "game_id", "gameId", "fixture_id", "fixtureId", "event_id", "eventId", "match_id", "matchId"):
                sub = value.get(subkey)
                if sub not in (None, ""):
                    ids.append(str(sub).strip())
    return _unique(ids)


def ids_from(result: dict[str, Any], provider: str) -> list[str]:
    if provider == "sportlogic":
        ids: list[str] = []
        for row in result.get("sample") or []:
            ids.extend(_sportlogic_game_ids_from_row(row))
        if ids:
            return _unique(ids)[:12]
    return v8.ids_from(result, provider)


async def detail_calls(client, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by = {(r.get("provider"), r.get("command")): r for r in results}
    active = by.get(("sportlogic", "active_odds")) or {}
    game_ids = ids_from(active, "sportlogic")[:3]
    details = await v8._ORIG_DETAIL_CALLS(client, results)
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
    return list(details) + list(extra)


def _render(payload: dict[str, Any]) -> str:
    return _BASE_RENDER(payload).replace("diagnostics v7", "diagnostics v9")


def install() -> None:
    v8.install()
    base.ids_from = ids_from
    base.detail_calls = detail_calls
    base.render = _render


async def run() -> dict[str, Any]:
    install()
    payload = await base.run()
    payload["mode"] = "api_full_data_smoke_probe_v9"
    payload.setdefault("notes", []).append("v9: SportLogic active odds detail probing follows game_id before odds row id.")
    base.JSON_OUT.write_text(base.json.dumps(base.safe(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    base.TXT_OUT.write_text(_render(payload), encoding="utf-8")
    print(_render(payload))
    return payload


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
