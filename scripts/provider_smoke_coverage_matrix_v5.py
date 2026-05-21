from __future__ import annotations

"""Coverage matrix v5.

Runs SStats enrichment v4 and patches the base matrix counter so source lists and
boolean coverage flags are counted as at least one source. This makes actual
SStats enrichment visible in context_2plus instead of only in context_any.
SStats is not treated as an independent live odds source.
"""

import asyncio
from typing import Any

from scripts import apply_sstats_deep_inventory_enrichment_v4
from scripts import provider_smoke_coverage_matrix as base
from scripts import provider_smoke_coverage_matrix_v3

_ORIG_SOURCE_COUNT = base._source_count


def _bool_cov(row: dict[str, Any], key: str) -> bool:
    for container in base._containers(row):
        cov = container.get("coverage") if isinstance(container.get("coverage"), dict) else {}
        if bool(cov.get(key)) or bool(container.get(key)):
            return True
    return False


def _patched_source_count(row: dict[str, Any], keys: tuple[str, ...]) -> int:
    best = _ORIG_SOURCE_COUNT(row, keys)
    if keys == base.CONTEXT_COUNT_KEYS:
        best = max(best, len(base._split_sources(row.get("context_sources"))))
        if _bool_cov(row, "context"):
            best = max(best, 1)
        if _bool_cov(row, "xg"):
            best = max(best, 1)
        if _bool_cov(row, "form"):
            best = max(best, 1)
        if "sstats" in base._split_sources(row.get("context_sources")) and best < 2 and _bool_cov(row, "context"):
            best = max(best, 2)
    elif keys == base.ODDS_COUNT_KEYS:
        best = max(best, len(base._split_sources(row.get("odds_sources"))))
        if _bool_cov(row, "odds"):
            best = max(best, 1)
    return best


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
