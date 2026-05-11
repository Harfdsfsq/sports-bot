from __future__ import annotations

"""Coverage matrix v6.

Applies discovery-first canonical pool to the day inventory before SStats actual
deep enrichment and matrix construction. This makes provider-day source_ids
visible to coverage/enrichment instead of keeping discovery as a report only.
"""

import asyncio

from scripts import apply_provider_day_discovery_to_inventory
from scripts import apply_sstats_deep_inventory_enrichment_v4
from scripts import provider_smoke_coverage_matrix as base
from scripts import provider_smoke_coverage_matrix_v3
from scripts.provider_smoke_coverage_matrix_v5 import _patched_source_count, _ORIG_SOURCE_COUNT


def main() -> int:
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
