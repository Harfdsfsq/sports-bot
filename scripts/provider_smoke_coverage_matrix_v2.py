from __future__ import annotations

"""Compatibility entrypoint for provider-smoke.

The workflow still calls this v2 script. It now delegates to v4, which first
applies actual SStats deep enrichment into day_inventory and then builds the v3
coverage/projection matrix.
"""


def main() -> int:
    from scripts import provider_smoke_coverage_matrix_v4

    return provider_smoke_coverage_matrix_v4.main()


if __name__ == "__main__":
    raise SystemExit(main())
