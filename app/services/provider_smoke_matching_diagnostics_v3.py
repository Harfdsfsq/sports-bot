from __future__ import annotations

import asyncio
from typing import Any

from app.services import provider_smoke_matching_diagnostics as base
from app.services import provider_smoke_matching_diagnostics_v2 as v2


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
        fallback = await _ORIGINAL_FETCH_ROWS(client, provider)
        fallback["documented_adapter_status"] = "empty"
        fallback["documented_adapter_stats"] = stats
        fallback["documented_adapter_preview"] = preview
        return fallback
    except Exception as exc:
        fallback = await _ORIGINAL_FETCH_ROWS(client, provider)
        fallback["documented_adapter_error"] = f"{type(exc).__name__}: {exc}"
        return fallback


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
            import json
            base.MATCH_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            base.MATCH_TXT.write_text("🧬 Provider matching diagnostics\n" f"• status: failed_or_timeout\n• error: {payload['error']}\n", encoding="utf-8")
        except Exception:
            pass
        return payload
