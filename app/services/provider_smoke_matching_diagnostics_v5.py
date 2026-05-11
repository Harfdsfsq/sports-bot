from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services import provider_smoke_matching_diagnostics as base
from app.services import provider_smoke_matching_diagnostics_v3 as v3

UTC = timezone.utc
ADAPTER_VERSION = "v5_sportlogic_adapter_error_safe_active_odds"


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


def _game_id(row: dict[str, Any]) -> str:
    for key in ("game_id", "gameId", "fixture_id", "fixtureId", "event_id", "eventId"):
        value = row.get(key)
        if value not in (None, ""):
            return str(value).strip()
    game = row.get("game")
    if isinstance(game, dict):
        for key in ("id", "game_id", "gameId"):
            value = game.get(key)
            if value not in (None, ""):
                return str(value).strip()
    return ""


async def _active_odds_events(client: Any) -> dict[str, Any]:
    key = base._secret("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN")
    if not key:
        return {"provider": "sportlogic", "status": "missing_key", "raw_rows": 0, "parsed_events": 0, "events": [], "attempts": []}
    root = str(os.getenv("SPORTLOGIC_BASE_URL") or "https://api.sportlogic.io/api/v1").rstrip("/")
    headers = {str(os.getenv("SPORTLOGIC_HEADER_NAME") or "X-API-Key"): key}
    attempts: list[dict[str, Any]] = []
    odds_payload, attempt = await base._get(client, f"{root}/odds", params={"is_active": "true", "per_page": 100}, headers=headers)
    attempts.append(attempt)
    odds_rows = base._rows(odds_payload)
    game_ids: list[str] = []
    for row in odds_rows:
        gid = _game_id(row)
        if gid and gid not in game_ids:
            game_ids.append(gid)
    events = []
    for gid in game_ids[:12]:
        game_payload, game_attempt = await base._get(client, f"{root}/games/{gid}", params={}, headers=headers)
        attempts.append(game_attempt)
        rows = base._rows(game_payload)
        if not rows and isinstance(game_payload, dict) and isinstance(game_payload.get("data"), dict):
            rows = [game_payload["data"]]
        for row in rows:
            event = base._event_from_generic("sportlogic", row)
            if event is not None:
                events.append(event)
    future = _future(events)
    status = "ok" if future else "documented_active_odds_no_future_games" if odds_rows else "documented_active_odds_empty"
    return {
        "provider": "sportlogic",
        "status": status,
        "raw_rows": len(odds_rows),
        "parsed_events": len(future),
        "missing_team_rows": 0,
        "missing_start_rows": max(0, len(events) - len(future)),
        "events": future,
        "samples": [event.sample() for event in future[:8]],
        "attempts": attempts[:10],
        "documented_active_odds_rows": len(odds_rows),
        "documented_active_game_ids_checked": game_ids[:12],
    }


async def _provider_adapter_events() -> tuple[list[Any], dict[str, Any], dict[str, Any], str | None]:
    try:
        from app.config import Settings
        from app.providers import sportlogic_docs_runtime_patch
        from app.providers.sportlogic_provider import SportLogicProvider

        sportlogic_docs_runtime_patch.install()
        matches, stats, preview = await SportLogicProvider(Settings()).fetch_matches()
        events = [event for item in matches if (event := v3._event_from_match(item)) is not None and event.start is not None]
        return _future(events), stats, preview, None
    except Exception as exc:
        return [], {}, {}, f"{type(exc).__name__}: {exc}"


async def _fetch_rows_v5(client: Any, provider: str) -> dict[str, Any]:
    if provider != "sportlogic":
        return await v3._ORIGINAL_FETCH_ROWS(client, provider)
    events, stats, preview, adapter_error = await _provider_adapter_events()
    if events:
        return {
            "provider": "sportlogic",
            "status": "ok",
            "adapter_version": ADAPTER_VERSION,
            "raw_rows": int(stats.get("fixtures_fetched") or stats.get("games_fetched") or len(events) or 0),
            "parsed_events": len(events),
            "missing_team_rows": 0,
            "missing_start_rows": 0,
            "events": events,
            "samples": [event.sample() for event in events[:8]],
            "attempts": [{"ok": True, "http_status": 200, "url": "SportLogicProvider.fetch_matches", "params_keys": ["documented_adapter"], "payload_shape": "matches"}],
            "provider_stats": stats,
            "provider_preview": preview,
        }
    odds = await _active_odds_events(client)
    odds["adapter_version"] = ADAPTER_VERSION
    odds["documented_adapter_status"] = "error_then_active_odds" if adapter_error else "empty_then_active_odds"
    if adapter_error:
        odds["documented_adapter_error"] = adapter_error
    odds["documented_adapter_stats"] = stats
    odds["documented_adapter_preview"] = preview
    return odds


def install() -> None:
    v3.install()
    base._fetch_provider_rows = _fetch_rows_v5


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
