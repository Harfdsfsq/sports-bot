from __future__ import annotations

from typing import Any

_INSTALLED = False
_ORIGINAL_AS_DICT = None


def _strict_as_dict(self: Any) -> dict[str, Any]:
    assert callable(_ORIGINAL_AS_DICT)
    row = dict(_ORIGINAL_AS_DICT(self))
    odds = int(row.get("odds_source_count") or 0)
    contexts = int(row.get("context_source_count") or 0)
    books = int(row.get("bookmaker_count") or 0)
    row["ready_for_model"] = odds >= 1 and contexts >= 1
    row["ready_for_publish"] = odds >= 2 and contexts >= 2 and books >= 2
    row["strict_2plus_odds_2plus_context_2plus_books"] = row["ready_for_publish"]
    return row


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_AS_DICT
    if _INSTALLED:
        return {"status": "already_installed"}
    try:
        from app.services.coverage_planner import MatchCoverageRow
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
    current = MatchCoverageRow.as_dict
    if getattr(current, "_harizon_strict_coverage_metrics", False):
        _INSTALLED = True
        return {"status": "already_patched"}
    _ORIGINAL_AS_DICT = current
    _strict_as_dict._harizon_strict_coverage_metrics = True
    MatchCoverageRow.as_dict = _strict_as_dict
    _INSTALLED = True
    return {
        "status": "installed",
        "ready_for_publish": "2 independent odds sources + 2 contexts + 2 bookmakers",
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
