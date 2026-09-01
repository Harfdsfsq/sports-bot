from __future__ import annotations

"""Compatibility entrypoint for the detailed Telegram report.

Keep the workflow command stable, but route execution to the independent direct
sender that always uses a plain HTTPS Telegram URL and always writes a fresh
send-status artifact.
"""

from scripts.send_detailed_report_direct import main


if __name__ == "__main__":
    raise SystemExit(main())
