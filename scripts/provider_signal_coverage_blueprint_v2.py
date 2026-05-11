from __future__ import annotations

"""Compatibility entrypoint for provider-smoke.

The workflow still calls this v2 script. It delegates to v4, which keeps the
coverage/crosswalk/backfill blueprint and uses cached SStats crosswalk data for
provider-day discovery instead of making extra SStats discovery calls.
"""


def main() -> int:
    from scripts import provider_signal_coverage_blueprint_v4

    return provider_signal_coverage_blueprint_v4.main()


if __name__ == "__main__":
    raise SystemExit(main())
