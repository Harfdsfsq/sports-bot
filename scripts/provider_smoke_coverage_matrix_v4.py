from __future__ import annotations

"""Coverage matrix v4.

Runs actual SStats deep inventory enrichment before the existing v3 matrix. This
turns the SStats crosswalk from projection into visible day-inventory coverage:
provider_source_ids.sstats, context/xG/form counters and odds rescue where
available.
"""

import asyncio

from scripts import apply_sstats_deep_inventory_enrichment_v2
from scripts import provider_smoke_coverage_matrix_v3


def main() -> int:
    try:
        asyncio.run(apply_sstats_deep_inventory_enrichment_v2.run())
    except Exception as exc:
        print(f"SStats actual enrichment failed; continuing matrix: {type(exc).__name__}: {exc}")
    return provider_smoke_coverage_matrix_v3.main()


if __name__ == "__main__":
    raise SystemExit(main())
