from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc

from app.services import provider_smoke_matching_diagnostics as base

_ORIGINAL_MATCH = getattr(base, "_harizon_original_match_provider_to_inventory", base._match_provider_to_inventory)
_ORIGINAL_NOTE = getattr(base, "_harizon_original_diagnosis_note", base._diagnosis_note)
_ORIGINAL_FETCH_ROWS = getattr(base, "_harizon_original_fetch_provider_rows", base._fetch_provider_rows)
setattr(base, "_harizon_original_match_provider_to_inventory", _ORIGINAL_MATCH)
setattr(base, "_harizon_original_diagnosis_note", _ORIGINAL_NOTE)
setattr(base, "_harizon_original_fetch_provider_rows", _ORIGINAL_FETCH_ROWS)


def _future_inventory_window(inventory: list[Any], slack_hours: float = 18.0):
    starts = [event.start for event in inventory if getattr(event, "start", None) is not None]
    if not starts:
        return None, None
    now = datetime.now(UTC)
    return max(min(starts) - timedelta(hours=1), now - timedelta(minutes=45)), max(starts) + timedelta(hours=slack_hours)


def _future_events(events: list[Any], days: int = 2) -> list[Any]:
    now = datetime.now(UTC)
    upper = now + timedelta(days=days)
    out: list[Any] = []
    for event in events:
        start = getattr(event, "start", None)
        if start is None:
            continue
        try:
            start_utc = start.astimezone(UTC)
        except Exception:
            continue
        if now - timedelta(hours=2) <= start_utc <= upper:
            out.append(event)
    return out


async def _fetch_rows(client: Any, provider: str) -> dict[str, Any]:
    payload = await _ORIGINAL_FETCH_ROWS(client, provider)
    if provider != "sportlogic":
        return payload
    events = list(payload.get("events") or [])
    if _future_events(events):
        return payload
    key = base._secret("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN")
    if not key:
        return payload
    root = __import__("os").getenv("SPORTLOGIC_BASE_URL") or "https://api.sportlogic.io/api/v1"
    root = str(root).rstrip("/")
    headers = {str(__import__("os").getenv("SPORTLOGIC_HEADER_NAME") or "X-API-Key"): key}
    try:
        broad, attempt = await base._get(client, f"{root}/games", params={"per_page": 100}, headers=headers)
        rows = base._rows(broad)
        parsed = [event for row in rows if (event := base._event_from_generic("sportlogic", row)) is not None]
        future = _future_events(parsed)
        payload.setdefault("attempts", []).append(attempt)
        payload["broad_fallback_used"] = True
        payload["broad_rows"] = len(rows)
        payload["broad_future_rows"] = len(future)
        if future:
            payload["raw_rows"] = len(rows)
            payload["parsed_events"] = len(future)
            payload["events"] = future
            payload["samples"] = [event.sample() for event in future[:8]]
            payload["status"] = "ok"
    except Exception as exc:
        payload["broad_fallback_error"] = f"{type(exc).__name__}: {exc}"
    return payload


def _patched_match(provider_payload: dict[str, Any], inventory: list[Any]) -> dict[str, Any]:
    result = _ORIGINAL_MATCH(provider_payload, inventory)
    provider = str(result.get("provider") or "")
    stage = str(result.get("failure_stage") or "")
    if provider == "sstats":
        return result
    unmatched = result.get("unmatched_samples") if isinstance(result.get("unmatched_samples"), list) else []
    scores = []
    for item in unmatched:
        try:
            scores.append(float(item.get("best_score") or 0.0))
        except Exception:
            scores.append(0.0)
    if stage == "normalization_or_time_matching_failed" and unmatched and int(result.get("matched_to_odds_inventory") or 0) <= 0 and max(scores or [0.0]) <= 0.0:
        result["failure_stage"] = "no_team_pair_overlap_with_odds_inventory"
        result["diagnostic_note"] = "provider events are in time window but team pairs are absent from odds inventory"
    if provider == "sportlogic" and stage == "no_fixture_overlap_with_odds_inventory":
        samples = result.get("samples") if isinstance(result.get("samples"), list) else []
        stale = 0
        for sample in samples:
            try:
                start = datetime.fromisoformat(str(sample.get("start") or "").replace("Z", "+00:00"))
                if start.astimezone(UTC) < datetime.now(UTC) - timedelta(days=2):
                    stale += 1
            except Exception:
                pass
        if samples and stale >= max(1, len(samples) // 2):
            result["failure_stage"] = "stale_provider_rows_date_filter_ignored"
            result["diagnostic_note"] = "SportLogic returned old fixtures for current date params"
    return result


def _note(item: dict[str, Any]) -> str:
    stage = str(item.get("failure_stage") or "")
    provider = str(item.get("provider") or "")
    if stage == "no_team_pair_overlap_with_odds_inventory":
        return f"{provider}: события есть, но таких пар нет в odds inventory. Это покрытие линий, не алиасы."
    if stage == "stale_provider_rows_date_filter_ignored":
        return f"{provider}: API отдал старые матчи при date params. Нужен другой date/status filter."
    return _ORIGINAL_NOTE(item)


def install() -> None:
    base._inventory_window = _future_inventory_window
    base._fetch_provider_rows = _fetch_rows
    base._match_provider_to_inventory = _patched_match
    base._diagnosis_note = _note


async def run(timeout_seconds: float | None = None) -> dict[str, Any]:
    install()
    try:
        return await asyncio.wait_for(base.run(timeout_seconds=timeout_seconds), timeout=95.0)
    except Exception as exc:
        payload = {"mode": "provider_smoke_matching_diagnostics", "status": "failed_or_timeout", "error": f"{type(exc).__name__}: {exc}"}
        try:
            base.MATCH_JSON.write_text(__import__("json").dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            base.MATCH_TXT.write_text("🧬 Provider matching diagnostics\n" f"• status: failed_or_timeout\n• error: {payload['error']}\n", encoding="utf-8")
        except Exception:
            pass
        return payload
