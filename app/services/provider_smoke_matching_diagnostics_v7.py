from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services import provider_smoke_matching_diagnostics as base
from app.services import provider_smoke_matching_diagnostics_v3 as upstream

UTC = timezone.utc
ADAPTER_VERSION = "v7_sportlogic_active_odds_no_recursion"
_MARK = "_harizon_provider_smoke_matching_diagnostics_v7_installed"
DIAG_KEYS = (
    "adapter_version",
    "documented_adapter_status",
    "documented_adapter_error",
    "documented_active_odds_rows",
    "documented_active_odds_pages_scanned",
    "documented_active_game_ids_checked",
    "documented_active_odds_sample_keys",
    "documented_active_id_candidates_sample",
    "documented_active_game_samples_all",
    "documented_adapter_stats",
    "documented_adapter_preview",
)


def _parse_dt(value: Any):
    try:
        from app.utils import parse_datetime
        return parse_datetime(value)
    except Exception:
        return None


def _text(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("name", "display_name", "displayName", "short_name", "shortName"):
            raw = value.get(key)
            if raw not in (None, ""):
                return str(raw).strip()
        return ""
    return str(value or "").strip()


def _event_from_game(row: dict[str, Any]):
    home = _text(row.get("home_team") or row.get("homeTeam") or row.get("home"))
    away = _text(row.get("away_team") or row.get("awayTeam") or row.get("away"))
    league = _text(row.get("league") or row.get("competition") or row.get("tournament"))
    start = _parse_dt(row.get("start_time") or row.get("starts_at") or row.get("start") or row.get("date") or row.get("commence_time"))
    if not home or not away:
        return None
    return base.EventRow(
        provider="sportlogic",
        home=home,
        away=away,
        league=league,
        start=start,
        source_id=str(row.get("id") or row.get("game_id") or row.get("gameId") or "").strip(),
        raw_shape="sportlogic_documented_game",
    )


def _future(events: list[Any]) -> list[Any]:
    now = datetime.now(UTC)
    upper = now + timedelta(days=2)
    out = []
    for event in events:
        start = getattr(event, "start", None)
        if start is None:
            continue
        try:
            start = start.astimezone(UTC)
        except Exception:
            continue
        if now - timedelta(hours=2) <= start <= upper:
            out.append(event)
    return out


def _sample_keys(rows: list[dict[str, Any]]) -> list[list[str]]:
    return [sorted(str(k) for k in row.keys())[:30] for row in rows[:5]]


def _id_candidates(value: Any, depth: int = 0) -> list[str]:
    if depth > 5:
        return []
    found: list[str] = []
    if isinstance(value, dict):
        for key, item in value.items():
            low = str(key).lower()
            likely = low in {"game", "game_id", "gameid", "fixture", "fixture_id", "fixtureid", "event", "event_id", "eventid", "match", "match_id", "matchid"}
            if likely and isinstance(item, dict):
                for subkey in ("id", "game_id", "gameId", "fixture_id", "fixtureId", "event_id", "eventId", "match_id", "matchId"):
                    sub = item.get(subkey)
                    if sub not in (None, ""):
                        found.append(str(sub).strip())
                found.extend(_id_candidates(item, depth + 1))
            elif likely and item not in (None, ""):
                found.append(str(item).strip())
            elif isinstance(item, (dict, list)):
                found.extend(_id_candidates(item, depth + 1))
    elif isinstance(value, list):
        for item in value[:30]:
            found.extend(_id_candidates(item, depth + 1))
    return [x for x in found if x and x.lower() not in {"none", "null"}]


def _next_cursor(payload: Any) -> str:
    if not isinstance(payload, dict):
        return ""
    for key in ("meta", "pagination"):
        meta = payload.get(key)
        if isinstance(meta, dict):
            value = meta.get("next_cursor") or meta.get("next") or meta.get("cursor")
            if value not in (None, ""):
                return str(value)
    value = payload.get("next_cursor") or payload.get("next")
    return str(value) if value not in (None, "") else ""


async def _adapter_events() -> tuple[list[Any], dict[str, Any], dict[str, Any], str | None]:
    try:
        from app.config import Settings
        from app.providers import sportlogic_docs_runtime_patch
        from app.providers.sportlogic_provider import SportLogicProvider
        sportlogic_docs_runtime_patch.install()
        matches, stats, preview = await SportLogicProvider(Settings()).fetch_matches()
        events = [event for match in matches if (event := upstream._event_from_match(match)) is not None and event.start is not None]
        return _future(events), stats, preview, None
    except Exception as exc:
        return [], {}, {}, f"{type(exc).__name__}: {exc}"


async def _active_odds(client: Any) -> dict[str, Any]:
    key = base._secret("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN")
    if not key:
        return {"provider": "sportlogic", "status": "missing_key", "raw_rows": 0, "parsed_events": 0, "events": [], "attempts": []}
    root = str(os.getenv("SPORTLOGIC_BASE_URL") or "https://api.sportlogic.io/api/v1").rstrip("/")
    headers = {str(os.getenv("SPORTLOGIC_HEADER_NAME") or "X-API-Key"): key}
    attempts: list[dict[str, Any]] = []
    game_ids: list[str] = []
    odds_count = 0
    sample_keys: list[list[str]] = []
    candidate_samples: list[list[str]] = []
    cursor = ""
    max_pages = max(1, int(float(os.getenv("SPORTLOGIC_ACTIVE_ODDS_SMOKE_PAGES") or 4)))
    max_games = max(6, int(float(os.getenv("SPORTLOGIC_ACTIVE_ODDS_SMOKE_GAME_LIMIT") or 24)))
    for _ in range(max_pages):
        params = {"is_active": "true", "per_page": 100}
        if cursor:
            params["cursor"] = cursor
        payload, attempt = await base._get(client, f"{root}/odds", params=params, headers=headers)
        attempts.append(attempt)
        rows = base._rows(payload)
        odds_count += len(rows)
        if not sample_keys:
            sample_keys = _sample_keys(rows)
            candidate_samples = [_id_candidates(row)[:8] for row in rows[:5]]
        for row in rows:
            for gid in _id_candidates(row):
                if gid not in game_ids:
                    game_ids.append(gid)
                    break
            if len(game_ids) >= max_games:
                break
        cursor = _next_cursor(payload)
        if len(game_ids) >= max_games or not rows or not cursor:
            break

    events = []
    missing_team = 0
    missing_start = 0
    all_game_samples = []
    for gid in game_ids[:max_games]:
        payload, attempt = await base._get(client, f"{root}/games/{gid}", params={}, headers=headers)
        attempts.append(attempt)
        rows = base._rows(payload)
        if not rows and isinstance(payload, dict) and isinstance(payload.get("data"), dict):
            rows = [payload["data"]]
        for row in rows:
            event = _event_from_game(row) or base._event_from_generic("sportlogic", row)
            if event is None:
                missing_team += 1
                continue
            if event.start is None:
                missing_start += 1
            if len(all_game_samples) < 8:
                all_game_samples.append(event.sample())
            events.append(event)
    future = _future(events)
    status = "ok" if future else "documented_active_odds_no_future_games" if odds_count else "documented_active_odds_empty"
    if odds_count and not game_ids:
        status = "documented_active_odds_no_game_ids"
    return {
        "provider": "sportlogic",
        "status": status,
        "raw_rows": odds_count,
        "parsed_events": len(future),
        "missing_team_rows": missing_team,
        "missing_start_rows": missing_start,
        "events": future,
        "samples": [event.sample() for event in future[:8]],
        "attempts": attempts[:16],
        "documented_active_odds_rows": odds_count,
        "documented_active_odds_pages_scanned": len([a for a in attempts if str(a.get("url", "")).endswith("/odds")]),
        "documented_active_game_ids_checked": game_ids[:max_games],
        "documented_active_odds_sample_keys": sample_keys,
        "documented_active_id_candidates_sample": candidate_samples,
        "documented_active_game_samples_all": all_game_samples,
    }


async def _fetch_provider_rows(client: Any, provider: str) -> dict[str, Any]:
    if provider != "sportlogic":
        return await upstream._ORIGINAL_FETCH_ROWS(client, provider)
    events, stats, preview, adapter_error = await _adapter_events()
    if events:
        return {"provider": "sportlogic", "status": "ok", "adapter_version": ADAPTER_VERSION, "raw_rows": len(events), "parsed_events": len(events), "events": events, "samples": [e.sample() for e in events[:8]], "attempts": [{"ok": True, "http_status": 200, "url": "SportLogicProvider.fetch_matches", "params_keys": ["documented_adapter"], "payload_shape": "matches"}], "provider_stats": stats, "provider_preview": preview}
    payload = await _active_odds(client)
    payload["adapter_version"] = ADAPTER_VERSION
    payload["documented_adapter_status"] = "error_then_active_odds" if adapter_error else "empty_then_active_odds"
    if adapter_error:
        payload["documented_adapter_error"] = adapter_error
    payload["documented_adapter_stats"] = stats
    payload["documented_adapter_preview"] = preview
    return payload


def _patch_result_copy() -> None:
    original_match = base._match_provider_to_inventory
    def patched(provider_payload: dict[str, Any], inventory: list[Any]) -> dict[str, Any]:
        result = original_match(provider_payload, inventory)
        for key in DIAG_KEYS:
            value = provider_payload.get(key)
            if value not in (None, "", [], {}):
                result[key] = value
        if str(provider_payload.get("provider") or "") == "sportlogic":
            result["adapter_version"] = ADAPTER_VERSION
        return result
    base._match_provider_to_inventory = patched


def install() -> None:
    upstream.install()
    if getattr(base, _MARK, False):
        return
    base._fetch_provider_rows = _fetch_provider_rows
    _patch_result_copy()
    setattr(base, _MARK, True)


def _mark(payload: dict[str, Any]) -> None:
    payload["matching_adapter_version"] = ADAPTER_VERSION
    try:
        base.MATCH_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass
    try:
        text = base.MATCH_TXT.read_text(encoding="utf-8") if base.MATCH_TXT.exists() else ""
        marker = f"• matching_adapter_version: {ADAPTER_VERSION}"
        if marker not in text:
            lines = text.splitlines()
            if lines and lines[0].startswith("🧬 Provider matching diagnostics"):
                lines.insert(1, marker)
                text = "\n".join(lines) + "\n"
            else:
                text = marker + "\n" + text
            base.MATCH_TXT.write_text(text, encoding="utf-8")
    except Exception:
        pass


async def run(timeout_seconds: float | None = None) -> dict[str, Any]:
    install()
    try:
        payload = await asyncio.wait_for(base.run(timeout_seconds=timeout_seconds), timeout=95.0)
    except Exception as exc:
        payload = {"mode": "provider_smoke_matching_diagnostics", "status": "failed_or_timeout", "error": f"{type(exc).__name__}: {exc}"}
        try:
            base.MATCH_TXT.write_text("🧬 Provider matching diagnostics\n" f"• matching_adapter_version: {ADAPTER_VERSION}\n" f"• status: failed_or_timeout\n• error: {payload['error']}\n", encoding="utf-8")
        except Exception:
            pass
    _mark(payload)
    return payload
