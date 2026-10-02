"""Stable compatibility entrypoint for the current report sender."""
from __future__ import annotations

from scripts.send_harizon_telegram_run_report_v13 import main

if __name__ == "__main__":
    raise SystemExit(main())
