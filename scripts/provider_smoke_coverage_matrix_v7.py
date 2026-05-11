from __future__ import annotations

"""Coverage matrix v7.

Correct order for discovery-first pipeline:
1. Build/reuse SStats crosswalk first, so provider-day discovery can use cached
   SStats gameIds instead of seeing `sstats: EMPTY`.
2. Merge full provider-day canonical pool into inventory.
3. Apply SStats actual deep enrichment using the same crosswalk cache.
4. Build source-aware coverage matrix.
"""

import asyncio

from scripts import apply_provider_day_discovery_to_inventory
from scripts import apply_sstats_deep_inventory_enrichment_v4
from scripts import provider_smoke_coverage_matrix as base
from scripts import provider_smoke_coverage_matrix_v3
from scripts.provider_smoke_coverage_matrix_v5 import _ORIG_SOURCE_COUNT, _patched_source_count
from scripts import sstats_crosswalk_probe


def main() -> int:
    try:
        asyncio.run(sstats_crosswalk_probe.run())
    except Exception as exc:
        print(f"SStats crosswalk prebuild failed; continuing discovery merge: {type(exc).__name__}: {exc}")
    try:
        asyncio.run(apply_provider_day_discovery_to_inventory.run())
    except Exception as exc:
        print(f"Provider day discovery inventory merge failed; continuing: {type(exc).__name__}: {exc}")
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
