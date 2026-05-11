from __future__ import annotations

"""Compatibility entrypoint for provider-smoke.

The workflow still calls this v2 script. The implementation now delegates to v3,
which keeps the v2 coverage parser and adds the SStats inventory crosswalk probe.
"""


def main() -> int:
    from scripts import provider_signal_coverage_blueprint_v3

    return provider_signal_coverage_blueprint_v3.main()


if __name__ == "__main__":
    raise SystemExit(main())
