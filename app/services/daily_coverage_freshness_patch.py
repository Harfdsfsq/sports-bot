from __future__ import annotations

from typing import Any

from app.schemas import MatchContext, Offer

_INSTALLED = False
_ORIGINAL_CACHED_PROVIDER_DATA = None


def _cached_provider_data(
    provider_name: str, method_name: str, matches: list[Any]
) -> dict[str, Any]:
    assert callable(_ORIGINAL_CACHED_PROVIDER_DATA)
    result = _ORIGINAL_CACHED_PROVIDER_DATA(provider_name, method_name, matches)
    if not isinstance(result, dict):
        return {}
    for value in result.values():
        if isinstance(value, MatchContext):
            details = dict(getattr(value, "details", {}) or {})
            details["daily_coverage_cache_reused"] = True
            evidence_time = (
                details.get("effective_at")
                or details.get("observed_at")
                or details.get("as_of")
            )
            if evidence_time:
                details.setdefault("effective_at", evidence_time)
            value.details = details
            continue
        if not isinstance(value, list):
            continue
        for offer in value:
            if not isinstance(offer, Offer):
                continue
            metadata = dict(getattr(offer, "metadata", {}) or {})
            metadata["daily_coverage_cache_reused"] = True
            offer.metadata = metadata
    return result


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_CACHED_PROVIDER_DATA
    if _INSTALLED:
        return {"status": "already_installed"}
    try:
        from app.services import daily_coverage_ledger
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
    current = daily_coverage_ledger.cached_provider_data
    if getattr(current, "_harizon_daily_coverage_freshness", False):
        _INSTALLED = True
        return {"status": "already_patched"}
    _ORIGINAL_CACHED_PROVIDER_DATA = current
    _cached_provider_data._harizon_daily_coverage_freshness = True
    daily_coverage_ledger.cached_provider_data = _cached_provider_data
    _INSTALLED = True
    return {
        "status": "installed",
        "cache_reuse_marked": True,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
