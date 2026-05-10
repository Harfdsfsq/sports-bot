from __future__ import annotations

"""Refined wrapper for provider smoke matching diagnostics.

This wrapper patches the base diagnostic module without duplicating the whole
script. It must keep references to the original base functions before patching;
otherwise fallback notes recursively call the patched function.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

UTC = timezone.utc

from app.services import provider_smoke_matching_diagnostics as base

_ORIGINAL_MATCH = getattr(base, "_harizon_original_match_provider_to_inventory", base._match_provider_to_inventory)
_ORIGINAL_DIAGNOSIS_NOTE = getattr(base, "_harizon_original_diagnosis_note", base._diagnosis_note)
setattr(base, "_harizon_original_match_provider_to_inventory", _ORIGINAL_MATCH)
setattr(base, "_harizon_original_diagnosis_note", _ORIGINAL_DIAGNOSIS_NOTE)


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
    best_scores = []
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
        result["diagnostic_note"] = "provider events are in time window but their team pairs are absent from the current odds inventory"

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
        return f"{provider}: события в актуальном окне есть, но таких пар нет в odds inventory. Это не ошибка алиасов; источник покрывает другой пласт матчей/лиг."
    if stage == "stale_provider_rows_date_filter_ignored":
        return f"{provider}: API отдал старые матчи при date params. Нужен фильтр stale rows и/или другой параметр даты endpoint-а."
    return _ORIGINAL_DIAGNOSIS_NOTE(item)


def install() -> None:
    base._inventory_window = _future_inventory_window
    base._match_provider_to_inventory = _patched_match_provider_to_inventory
    base._diagnosis_note = _diagnosis_note


async def run(timeout_seconds: float | None = None) -> dict[str, Any]:
    install()
    return await base.run(timeout_seconds=timeout_seconds)
