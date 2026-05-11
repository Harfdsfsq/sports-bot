from __future__ import annotations

"""Blueprint v4 shim.

Uses the existing blueprint v3 implementation but swaps provider day discovery to
v2, which reuses latest-sstats-crosswalk.json instead of calling SStats discovery
endpoints again and hitting 429 during provider-smoke.
"""

from scripts import provider_day_discovery_canonical_pool_v2
from scripts import provider_signal_coverage_blueprint_v3


def main() -> int:
    provider_signal_coverage_blueprint_v3.provider_day_discovery_canonical_pool = provider_day_discovery_canonical_pool_v2
    return provider_signal_coverage_blueprint_v3.main()


if __name__ == "__main__":
    raise SystemExit(main())
