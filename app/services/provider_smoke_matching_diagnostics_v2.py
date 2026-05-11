from __future__ import annotations

"""Refined wrapper for provider smoke matching diagnostics.

Adds:
- future-only inventory window;
- clearer no-overlap diagnosis;
- unified odds inventory from primary odds-api.io + secondary odds-api.io + Bzzoiro odds;
- separate quota-safe SportLogic/SStats odds probes appended to provider-smoke artifacts.
"""

import json
from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc

from app.services import provider_smoke_matching_diagnostics as base

_ORIGINAL_MATCH = getattr(base, "_harizon_original_match_provider_to_inventory", base._match_provider_to_inventory)
_ORIGINAL_DIAGNOSIS_NOTE = getattr(base, "_harizon_original_diagnosis_note", base._diagnosis_note)
_ORIGINAL_FETCH_ODDS = getattr(base, "_harizon_original_fetch_odds_inventory", base._fetch_odds_inventory)
setattr(base, "_harizon_original_match_provider_to_inventory", _ORIGINAL_MATCH)
setattr(base, "_harizon_original_diagnosis_note", _ORIGINAL_DIAGNOSIS_NOTE)
setattr(base, "_harizon_original_fetch_odds_inventory", _ORIGINAL_FETCH_ODDS)


def _future_inventory_window(inventory: list[Any], slack_hours: float = 18.0):
    starts = [event.start for event in inventory if getattr(event, "start", None) is not None]
    if not starts:
        return None, None
    now = datetime.now(UTC)
    lower = max(min(starts) - timedelta(hours=1), now - timedelta(minutes=45))
    upper = max(starts) + timedelta(hours=slack_hours)
    return lower, upper


def _patched_match_provider_to_inventory(provider_payload: dict[str, Any], inventory: list[Any]) -> dict[str, Any]:
    result = _ORIGINAL_MATCH(provider_payload, inventory)
    provider = str(result.get("provider") or "")
    stage = str(result.get("failure_stage") or "")
    if provider in {"sstats"}:
        return result

    unmatched = result.get("unmatched_samples") if isinstance(result.get("unmatched_samples"), list) else []
    best_scores: list[float] = []
    for item in unmatched:
        try:
            best_scores.append(float(item.get("best_score") or 0.0))
        except Exception:
            best_scores.append(0.0)

    if (
        stage == "normalization_or_time_matching_failed"
        and int(result.get("matched_to_odds_inventory") or 0) <= 0
        and unmatched
        and max(best_scores or [0.0]) <= 0.0
    ):
        result["failure_stage"] = "no_team_pair_overlap_with_odds_inventory"
        result["diagnostic_note"] = "provider events are in time window but their team pairs are absent from the current unified odds inventory"

    if provider == "sportlogic" and stage == "no_fixture_overlap_with_odds_inventory":
        samples = result.get("samples") if isinstance(result.get("samples"), list) else []
        stale = 0
        for sample in samples:
            raw_start = str(sample.get("start") or "")
            try:
                start = datetime.fromisoformat(raw_start.replace("Z", "+00:00"))
                if start.astimezone(UTC) < datetime.now(UTC) - timedelta(days=2):
                    stale += 1
            except Exception:
                pass
        if samples and stale >= max(1, len(samples) // 2):
            result["failure_stage"] = "stale_provider_rows_date_filter_ignored"
            result["diagnostic_note"] = "SportLogic /games returned old fixtures for current date params"
    return result


def _diagnosis_note(item: dict[str, Any]) -> str:
    stage = str(item.get("failure_stage") or "")
    provider = str(item.get("provider") or "")
    if stage == "no_team_pair_overlap_with_odds_inventory":
        return f"{provider}: события в актуальном окне есть, но таких пар нет даже в unified odds inventory. Это покрытие линий, не алиасы."
    if stage == "stale_provider_rows_date_filter_ignored":
        return f"{provider}: API отдал старые матчи при date params. Нужен фильтр stale rows и/или другой параметр даты endpoint-а."
    return _ORIGINAL_DIAGNOSIS_NOTE(item)


async def _fetch_unified_odds_inventory(client: Any) -> dict[str, Any]:
    primary = await _ORIGINAL_FETCH_ODDS(client)
    try:
        from app.services import provider_smoke_odds_inventory_extensions as ext
        unified = await ext.build_unified_inventory(client, primary)
        return unified
    except Exception as exc:
        primary["unified_inventory_error"] = f"{type(exc).__name__}: {exc}"
        return primary


def install() -> None:
    base._inventory_window = _future_inventory_window
    base._match_provider_to_inventory = _patched_match_provider_to_inventory
    base._diagnosis_note = _diagnosis_note
    base._fetch_odds_inventory = _fetch_unified_odds_inventory


def _append_text(path: Any, marker: str, text: str) -> None:
    try:
        current = path.read_text(encoding="utf-8") if path.exists() else ""
        if marker in current:
            return
        path.write_text(current.rstrip() + "\n\n---\n\n" + text.strip() + "\n", encoding="utf-8")
    except Exception:
        pass


def _rewrite_json(payload: dict[str, Any]) -> None:
    try:
        base.MATCH_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


async def run(timeout_seconds: float | None = None) -> dict[str, Any]:
    install()
    payload = await base.run(timeout_seconds=timeout_seconds)
    try:
        from app.services import provider_smoke_odds_inventory_extensions as ext
        odds_ext = await ext.run_odds_extension_probe()
        payload["odds_inventory_extensions"] = odds_ext
        _rewrite_json(payload)
        if ext.ODDS_EXT_TXT.exists():
            _append_text(base.MATCH_TXT, "🎯 Extended odds inventory / odds probes", ext.ODDS_EXT_TXT.read_text(encoding="utf-8"))
    except Exception as exc:
        payload["odds_inventory_extensions_error"] = f"{type(exc).__name__}: {exc}"
        _rewrite_json(payload)
    return payload
