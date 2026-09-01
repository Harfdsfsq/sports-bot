from __future__ import annotations

import contextlib
from datetime import UTC, datetime
from typing import Any

from app.schemas import MatchContext, Offer
from app.services.daily_coverage_common import canonical_source, parse_dt

_INSTALLED = False
_ORIGINAL_FETCH = None


def _provider_name(runner: Any, provider: Any) -> str:
    try:
        return canonical_source(runner._provider_name(provider))
    except Exception:
        module = getattr(getattr(provider, "__class__", None), "__module__", "")
        return canonical_source(module.rsplit(".", 1)[-1])


def _stamp_context(provider_name: str, context: MatchContext, observed_at: datetime) -> None:
    details = dict(getattr(context, "details", {}) or {})
    if details.get("daily_coverage_cache_reused"):
        return
    effective_at = details.get("effective_at") or details.get("observed_at")
    if not effective_at and provider_name == "clubelo":
        payload = dict(getattr(context, "payload", {}) or {})
        dates = []
        for key in ("home_rating", "away_rating"):
            row = payload.get(key) if isinstance(payload.get(key), dict) else {}
            parsed = parse_dt(row.get("From") or row.get("Date") or row.get("To"))
            if parsed is not None:
                dates.append(parsed)
        if dates:
            effective_at = min(dates).isoformat()
    details.setdefault("effective_at", effective_at or observed_at.isoformat())
    context.details = details


def _fresh_subset(
    provider_name: str, method_name: str, data: Any, observed_at: datetime
) -> dict[str, Any]:
    if not isinstance(data, dict):
        return {}
    is_odds = "offer" in method_name.lower()
    fresh: dict[str, Any] = {}
    for match_key, value in data.items():
        if is_odds:
            offers = []
            for offer in value or []:
                if not isinstance(offer, Offer):
                    continue
                metadata = dict(getattr(offer, "metadata", {}) or {})
                if metadata.get("daily_coverage_cache_reused"):
                    continue
                metadata.setdefault("fetched_at_utc", observed_at.isoformat())
                offer.metadata = metadata
                offers.append(offer)
            if offers:
                fresh[str(match_key)] = offers
            continue
        if not isinstance(value, MatchContext):
            continue
        if (getattr(value, "details", {}) or {}).get("daily_coverage_cache_reused"):
            continue
        _stamp_context(provider_name, value, observed_at)
        fresh[str(match_key)] = value
    return fresh


async def _fetch(
    self: Any,
    provider: Any | None,
    method_name: str,
    *args: Any,
    empty_data: Any,
):
    assert callable(_ORIGINAL_FETCH)
    data, stats, preview = await _ORIGINAL_FETCH(
        self, provider, method_name, *args, empty_data=empty_data
    )
    provider_name = _provider_name(self, provider)
    fresh = _fresh_subset(provider_name, method_name, data, datetime.now(UTC))
    if fresh:
        with contextlib.suppress(Exception):
            from app.services.daily_coverage_ledger import record_provider_result

            record_provider_result(provider_name, method_name, fresh, stats)
    if isinstance(stats, dict):
        stats["daily_coverage_fresh_evidence_stamped"] = len(fresh)
    return data, stats, preview


def install(prediction_runner: Any) -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_FETCH
    if _INSTALLED:
        return {"status": "already_installed"}
    current = prediction_runner._fetch_provider
    if getattr(current, "_harizon_daily_coverage_evidence_stamp", False):
        _INSTALLED = True
        return {"status": "already_patched"}
    _ORIGINAL_FETCH = current
    _fetch._harizon_daily_coverage_evidence_stamp = True
    prediction_runner._fetch_provider = _fetch
    _INSTALLED = True
    return {
        "status": "installed",
        "fresh_line_timestamp_stamped": True,
        "fresh_context_effective_at_stamped": True,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
