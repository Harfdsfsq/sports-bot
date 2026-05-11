from __future__ import annotations

"""Compatibility entrypoint for provider-smoke.

The workflow still calls this v2 script. It now delegates to v7:
SStats crosswalk first, then discovery-first inventory merge, then actual SStats
deep enrichment, then source-aware coverage matrix.
"""


def main() -> int:
    from scripts import provider_smoke_coverage_matrix_v7

    return provider_smoke_coverage_matrix_v7.main()


if __name__ == "__main__":
    raise SystemExit(main())
