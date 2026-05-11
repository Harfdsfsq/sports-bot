from __future__ import annotations

"""Compatibility entrypoint for provider-smoke.

The workflow still calls this v2 script. It now delegates to v6, which first
merges the discovery-first canonical pool into day_inventory, then applies
SStats actual deep enrichment, then builds the source-aware coverage matrix.
"""


def main() -> int:
    from scripts import provider_smoke_coverage_matrix_v6

    return provider_smoke_coverage_matrix_v6.main()


if __name__ == "__main__":
    raise SystemExit(main())
