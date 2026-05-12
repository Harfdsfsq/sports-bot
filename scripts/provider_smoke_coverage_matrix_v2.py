from __future__ import annotations

"""Compatibility entrypoint for provider-smoke.

The workflow still calls this v2 script. It now delegates to v8:
pre-merge crosswalk for cached SStats discovery, discovery-first inventory merge,
post-merge crosswalk on the full 300-match inventory, SStats ID apply, actual
SStats deep enrichment, then source-aware coverage matrix.
"""


def main() -> int:
    from scripts import provider_smoke_coverage_matrix_v8

    return provider_smoke_coverage_matrix_v8.main()


if __name__ == "__main__":
    raise SystemExit(main())
