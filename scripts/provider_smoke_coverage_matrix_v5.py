from __future__ import annotations

"""Coverage matrix v5 with strict provider-source independence.

The smoke matrix is diagnostic, but its 2+ counters must follow the same source
identity rule as final strict coverage.  A bookmaker count, xG/form counter, or a
boolean coverage flag is not a second independent API source.  Explicit verified
source lists are authoritative when present; otherwise explicit provider names may
establish coverage.  Boolean evidence can establish at most one unknown source.

SStats remains one context source.  It is never promoted to two sources merely
because the row also has context/xG/form flags, and it is not treated as an
independent live odds source.
"""

import asyncio
from typing import Any

from scripts import apply_sstats_deep_inventory_enrichment_v4
from scripts import provider_smoke_coverage_matrix as base
from scripts import provider_smoke_coverage_matrix_v3

_ORIG_SOURCE_COUNT = base._source_count

_CONTEXT_VERIFIED_KEYS = ("verified_context_sources",)
_CONTEXT_SOURCE_KEYS = ("context_sources", "independent_context_sources")
_ODDS_VERIFIED_KEYS = ("verified_odds_sources",)
_ODDS_SOURCE_KEYS = ("odds_sources", "line_sources", "independent_odds_sources")


def _bool_cov(row: dict[str, Any], key: str) -> bool:
    for container in base._containers(row):
        cov = container.get("coverage") if isinstance(container.get("coverage"), dict) else {}
        if bool(cov.get(key)) or bool(container.get(key)):
            return True
    return False


def _explicit_source_names(
    row: dict[str, Any],
    *,
    verified_keys: tuple[str, ...],
    source_keys: tuple[str, ...],
) -> tuple[bool, set[str]]:
    """Return ``(verified_field_seen, names)`` for one provider role.

    An explicitly present verified field is authoritative even when empty.  This
    prevents stale top-level aliases/counters from restoring sources that strict
    evidence synchronization already rejected.
    """

    verified_seen = False
    verified: set[str] = set()
    observed: set[str] = set()
    for container in base._containers(row):
        for key in verified_keys:
            if key in container:
                verified_seen = True
                verified.update(base._split_sources(container.get(key)))
        for key in source_keys:
            observed.update(base._split_sources(container.get(key)))
    return verified_seen, verified if verified_seen else observed


def _patched_source_count(row: dict[str, Any], keys: tuple[str, ...]) -> int:
    if keys == base.CONTEXT_COUNT_KEYS:
        verified_seen, names = _explicit_source_names(
            row,
            verified_keys=_CONTEXT_VERIFIED_KEYS,
            source_keys=_CONTEXT_SOURCE_KEYS,
        )
        if verified_seen or names:
            return len(names)
        if _bool_cov(row, "context") or _bool_cov(row, "xg") or _bool_cov(row, "form"):
            return 1
        return 0

    if keys == base.ODDS_COUNT_KEYS:
        verified_seen, names = _explicit_source_names(
            row,
            verified_keys=_ODDS_VERIFIED_KEYS,
            source_keys=_ODDS_SOURCE_KEYS,
        )
        if verified_seen or names:
            return len(names)
        return 1 if _bool_cov(row, "odds") else 0

    return _ORIG_SOURCE_COUNT(row, keys)


def main() -> int:
    try:
        asyncio.run(apply_sstats_deep_inventory_enrichment_v4.run())
    except Exception as exc:
        print(f"SStats actual enrichment v4 failed; continuing matrix: {type(exc).__name__}: {exc}")
    base._source_count = _patched_source_count
    try:
        return provider_smoke_coverage_matrix_v3.main()
    finally:
        base._source_count = _ORIG_SOURCE_COUNT


if __name__ == "__main__":
    raise SystemExit(main())
