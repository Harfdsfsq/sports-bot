from __future__ import annotations

"""Coverage matrix v4.

Runs actual prioritized SStats deep inventory enrichment before the existing v3
matrix. This turns the SStats crosswalk from projection into visible
day-inventory coverage and writes both latest/current/today/date inventory
aliases before the matrix reads them.
"""

import asyncio

from scripts import apply_sstats_deep_inventory_enrichment_v3
from scripts import provider_smoke_coverage_matrix_v3


def main() -> int:
    try:
        asyncio.run(apply_sstats_deep_inventory_enrichment_v3.run())
    except Exception as exc:
        print(f"SStats actual enrichment v3 failed; continuing matrix: {type(exc).__name__}: {exc}")
    return provider_smoke_coverage_matrix_v3.main()


if __name__ == "__main__":
    raise SystemExit(main())
