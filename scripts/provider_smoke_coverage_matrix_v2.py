from __future__ import annotations

"""Compatibility entrypoint for provider-smoke.

The workflow still calls this v2 script. It now delegates to v5, which applies
prioritized SStats deep enrichment and counts source lists/bool coverage flags so
actual enrichment is visible in context_2plus and odds_2plus metrics.
"""


def main() -> int:
    from scripts import provider_smoke_coverage_matrix_v5

    return provider_smoke_coverage_matrix_v5.main()


if __name__ == "__main__":
    raise SystemExit(main())
