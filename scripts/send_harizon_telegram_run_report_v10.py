from __future__ import annotations

"""Compatibility entrypoint.

The workflow calls v10 first. Keep that path stable, but delegate to v13.
"""

from scripts.send_harizon_telegram_run_report_v13 import build_payload, render, v9  # noqa: F401

v9.v8.v7.v5.build_payload = build_payload
v9.v8.v7.v5.render = render
v9.v8.v7.build_payload = build_payload
v9.v8.v7.render = render


if __name__ == '__main__':
    raise SystemExit(v9.v8.v7.v5.main())
