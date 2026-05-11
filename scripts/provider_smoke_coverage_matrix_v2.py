from __future__ import annotations

"""Compatibility entrypoint for provider-smoke.

The workflow still calls this v2 script. The implementation delegates to v3,
which keeps v2 provider status/queue behavior and adds SStats crosswalk
projection from latest-sstats-crosswalk.json.
"""


def main() -> int:
    from scripts import provider_smoke_coverage_matrix_v3

    return provider_smoke_coverage_matrix_v3.main()


if __name__ == "__main__":
    raise SystemExit(main())
