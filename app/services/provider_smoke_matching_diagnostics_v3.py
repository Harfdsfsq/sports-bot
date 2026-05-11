from __future__ import annotations

import asyncio
import json
import os
from datetime import datetime, timedelta, timezone
from typing import Any

from app.services import provider_smoke_matching_diagnostics as base
from app.services import provider_smoke_matching_diagnostics_v2 as v2

UTC = timezone.utc


def _event_from_match(match: Any):
    try:
        return base.EventRow(
            provider="sportlogic",
            home=str(getattr(match, "home_team", "") or ""),
            away=str(getattr(match, "away_team", "") or ""),
            league=str(getattr(match, "league_name", "") or ""),
            start=getattr(match, "commence_time", None),
            source_id=str(getattr(match, "source_event_id", "") or ""),
            raw_shape="SportLogicProvider.Match",
        )
    except Exception:
        return None


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
        game_id = str(row.get("game_id") or row.get("gameId") or row.get("game", {}).get("id") if isinstance(row.get("game"), dict) else "").strip()
        if game_id and game_id not in game_ids:
            game_ids.append(game_id)
    events = []
    for game_id in game_ids[:12]:
        game_payload, game_attempt = await base._get(client, f"{root}/games/{game_id}", headers=headers)
        attempts.append(game_attempt)
        rows = base._rows(game_payload)
        if not rows and isinstance(game_payload, dict) and isinstance(game_payload.get("data"), dict):
            rows = [game_payload["data"]]
        for row in rows:
            event = base._event_from_generic("sportlogic", row)
            if event is not None:
                events.append(event)
    future = _future(events)
    return {
        "provider": "sportlogic",
        "status": "ok" if odds_rows else "documented_active_odds_empty",
        "raw_rows": len(odds_rows),
        "parsed_events": len(future),
        "missing_team_rows": 0,
        "missing_start_rows": max(0, len(events) - len(future)),
        "events": future,
        "samples": [event.sample() for event in future[:8]],
        "attempts": attempts[:8],
        "documented_active_odds_rows": len(odds_rows),
        "documented_active_game_ids_checked": game_ids[:12],
    }


async def _fetch_rows_v3(client: Any, provider: str) -> dict[str, Any]:
    if provider != "sportlogic":
        return await _ORIGINAL_FETCH_ROWS(client, provider)
    try:
        from app.config import Settings
        from app.providers import sportlogic_docs_runtime_patch
        from app.providers.sportlogic_provider import SportLogicProvider

        sportlogic_docs_runtime_patch.install()
        adapter = SportLogicProvider(Settings())
        matches, stats, preview = await adapter.fetch_matches()
        events = [event for item in matches if (event := _event_from_match(item)) is not None and event.start is not None]
        events = _future(events)
        if events:
            return {
                "provider": "sportlogic",
                "status": "ok",
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
        odds_payload = await _active_odds_events(client)
        if int(odds_payload.get("parsed_events") or 0) > 0:
            odds_payload["documented_adapter_status"] = "empty_then_active_odds_fallback_ok"
            odds_payload["documented_adapter_stats"] = stats
            odds_payload["documented_adapter_preview"] = preview
            return odds_payload
        return {
            "provider": "sportlogic",
            "status": "documented_adapter_empty",
            "raw_rows": int(odds_payload.get("raw_rows") or 0),
            "parsed_events": 0,
            "missing_team_rows": 0,
            "missing_start_rows": int(odds_payload.get("missing_start_rows") or 0),
            "events": [],
            "samples": [],
            "attempts": odds_payload.get("attempts") or [{"ok": True, "http_status": 200, "url": "SportLogicProvider.fetch_matches", "params_keys": ["documented_adapter"], "payload_shape": "empty"}],
            "documented_adapter_status": "empty",
            "documented_adapter_stats": stats,
            "documented_adapter_preview": preview,
            "documented_active_odds_rows": odds_payload.get("documented_active_odds_rows", 0),
            "documented_active_game_ids_checked": odds_payload.get("documented_active_game_ids_checked", []),
        }
    except Exception as exc:
        return {"provider": "sportlogic", "status": "documented_adapter_error", "raw_rows": 0, "parsed_events": 0, "events": [], "attempts": [{"ok": False, "status": "adapter_error", "error": f"{type(exc).__name__}: {exc}", "url": "SportLogicProvider.fetch_matches", "params_keys": ["documented_adapter"]}]}


_ORIGINAL_FETCH_ROWS = base._fetch_provider_rows


def install() -> None:
    v2.install()
    global _ORIGINAL_FETCH_ROWS
    _ORIGINAL_FETCH_ROWS = base._fetch_provider_rows
    base._fetch_provider_rows = _fetch_rows_v3


async def run(timeout_seconds: float | None = None) -> dict[str, Any]:
    install()
    try:
        return await asyncio.wait_for(base.run(timeout_seconds=timeout_seconds), timeout=95.0)
    except Exception as exc:
        payload = {"mode": "provider_smoke_matching_diagnostics", "status": "failed_or_timeout", "error": f"{type(exc).__name__}: {exc}"}
        try:
            base.MATCH_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            base.MATCH_TXT.write_text("🧬 Provider matching diagnostics\n" f"• status: failed_or_timeout\n• error: {payload['error']}\n", encoding="utf-8")
        except Exception:
            pass
        return payload
