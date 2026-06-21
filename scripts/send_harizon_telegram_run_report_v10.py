from __future__ import annotations

"""Compatibility entrypoint.

The workflow still calls v10 first. Keep that path stable, but delegate to the
standalone v12 renderer with effective daily-cap blockers and A-tier diagnostics.
"""

from scripts.send_harizon_telegram_run_report_v12 import build_payload, render, v9  # noqa: F401

# Patch the deepest legacy entrypoint used by v9's main().
v9.v8.v7.v5.build_payload = build_payload
v9.v8.v7.v5.render = render
v9.v8.v7.build_payload = build_payload
v9.v8.v7.render = render


if __name__ == '__main__':
    raise SystemExit(v9.v8.v7.v5.main())
