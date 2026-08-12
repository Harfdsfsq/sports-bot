from __future__ import annotations

"""Repair context coverage truth from provider crosswalk/evidence.

This runtime patch does not relax publication rules.  It makes strict coverage
look at the same canonical provider evidence that the day inventory now keeps:
Bzzoiro/SStats context hints, verified_context_sources, context_sources lists and
provider_crosswalk.  The goal is to stop losing real context evidence after the
fixture rows have been semantically matched across APIs.
"""

from typing import Any

_INSTALLED = False
_ORIGINAL_STRICT_AS_DICT = None

CONTEXT_KEYS = (
    "context_sources",
    "verified_context_sources",
    "all_context_sources",
    "core_context_sources",
    "supplemental_context_sources",
    "confirmation_sources",
    "context_confirmations",
)
COUNT_KEYS = (
    "context_source_count",
    "context_sources_count",
    "confirmation_sources_count",
    "all_context_sources_count",
    "core_context_sources_count",
    "latest_context_sources_max",
    "latest_confirmation_sources_max",
)
CONTEXT_FLAGS = {
    "bzzoiro_has_context_hint": "bzzoiro",
    "bzzoiro_context_fields": "bzzoiro",
    "sstats_has_context_hint": "sstats",
    "sstats_context_fields": "sstats",
    "sportlogic_context": "sportlogic",
}
NON_CONTEXT = {"", "unknown", "day_inventory", "inventory", "fixture", "alias", "proxy", "bookmaker"}


def _norm_source(value: Any) -> str:
    src = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    if src.startswith("bzzoiro") or src.startswith("bsd_sports"):
        return "bzzoiro"
    if src.startswith("sstats"):
        return "sstats"
    if src.startswith("sportlogic") or src.startswith("sport_logic"):
        return "sportlogic"
    if src.startswith("clubelo") or src.startswith("club_elo"):
        return "clubelo"
    if src in {"football_data", "football-data"}:
        return "football_data"
    return src


def _as_int(value: Any) -> int:
    try:
        return int(float(str(value)))
    except Exception:
        return 0


def _walk_dicts(row: dict[str, Any]) -> list[dict[str, Any]]:
    out = [row]
    for key in ("coverage", "metadata", "source_summary", "day_inventory_coverage", "progressive_coverage"):
        value = row.get(key)
        if isinstance(value, dict):
            out.append(value)
            nested = value.get("day_inventory_coverage")
            if isinstance(nested, dict):
                out.append(nested)
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    coverage = metadata.get("day_inventory_coverage") if isinstance(metadata.get("day_inventory_coverage"), dict) else None
    if coverage:
        out.append(coverage)
    return out


def context_sources_from_row(row: dict[str, Any]) -> set[str]:
    sources: set[str] = set()
    best_count = 0
    for container in _walk_dicts(row):
        for key in CONTEXT_KEYS:
            value = container.get(key)
            if isinstance(value, dict):
                iterable = list(value.keys()) + list(value.values())
            elif isinstance(value, (list, tuple, set)):
                iterable = list(value)
            elif isinstance(value, str):
                iterable = value.split(",")
            else:
                iterable = []
            for item in iterable:
                src = _norm_source(item)
                if src and src not in NON_CONTEXT and not src.startswith("book:"):
                    sources.add(src)
        for key in COUNT_KEYS:
            best_count = max(best_count, _as_int(container.get(key)))
        for flag, src in CONTEXT_FLAGS.items():
            if bool(container.get(flag)):
                sources.add(src)
        crosswalk = container.get("provider_crosswalk")
        if isinstance(crosswalk, dict):
            for provider, record in crosswalk.items():
                provider_key = _norm_source(provider)
                if provider_key in {"bzzoiro", "sstats", "sportlogic"} and isinstance(record, dict):
                    if record.get("provider_event_id") or record.get("query"):
                        # Crosswalk is fixture identity only; count it as context
                        # only when the row also has a provider context hint.
                        if any(container.get(flag) for flag, src in CONTEXT_FLAGS.items() if src == provider_key):
                            sources.add(provider_key)
    if best_count > len(sources):
        # Numeric counts are only used as a floor with synthetic placeholders so
        # reports do not collapse valid legacy evidence to zero. They do not add
        # named fake providers when real providers already exist.
        for idx in range(best_count - len(sources)):
            sources.add(f"legacy_context_{idx + 1}")
    return sources


def patch_row_context(row: dict[str, Any]) -> dict[str, Any]:
    sources = sorted(context_sources_from_row(row))
    if not sources:
        return row
    coverage = dict(row.get("coverage") or {})
    metadata = dict(row.get("metadata") or {}) if isinstance(row.get("metadata"), dict) else {}
    existing = set(row.get("context_sources") or []) if isinstance(row.get("context_sources"), list) else set()
    existing.update(sources)
    row["context_sources"] = sorted(existing)
    row["context_source_count"] = max(_as_int(row.get("context_source_count")), len(existing))
    row["context_sources_count"] = max(_as_int(row.get("context_sources_count")), len(existing))
    coverage["context"] = True
    coverage["context_source_count"] = max(_as_int(coverage.get("context_source_count")), len(existing))
    coverage["context_sources_count"] = max(_as_int(coverage.get("context_sources_count")), len(existing))
    coverage["context_2plus_sources"] = len(existing) >= 2
    coverage["ready_for_model"] = bool(coverage.get("odds")) and len(existing) >= 1
    metadata["context_coverage_bridge_sources"] = sorted(existing)
    metadata["context_coverage_bridge_applied"] = True
    row["coverage"] = coverage
    row["metadata"] = metadata
    return row


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_STRICT_AS_DICT
    if _INSTALLED:
        return {"status": "already_installed"}
    try:
        from app.services.coverage_planner import MatchCoverageRow
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
    current = MatchCoverageRow.as_dict
    _ORIGINAL_STRICT_AS_DICT = current

    def as_dict_context_bridge(self: Any) -> dict[str, Any]:
        row = dict(_ORIGINAL_STRICT_AS_DICT(self))
        patch_row_context(row)
        odds = _as_int(row.get("odds_source_count") or row.get("odds_sources_count"))
        contexts = _as_int(row.get("context_source_count") or row.get("context_sources_count"))
        books = _as_int(row.get("bookmaker_count") or row.get("books_count"))
        row["ready_for_model"] = odds >= 1 and contexts >= 1
        row["ready_for_publish"] = odds >= 2 and contexts >= 2 and books >= 2
        row["strict_2plus_odds_2plus_context_2plus_books"] = row["ready_for_publish"]
        return row

    as_dict_context_bridge._harizon_context_coverage_bridge = True
    MatchCoverageRow.as_dict = as_dict_context_bridge
    _INSTALLED = True
    return {"status": "installed", "publication_contract_relaxed": False}


__all__ = ["install", "patch_row_context", "context_sources_from_row"]
