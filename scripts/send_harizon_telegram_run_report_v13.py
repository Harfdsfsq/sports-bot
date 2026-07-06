from __future__ import annotations

from pathlib import Path
import runpy

if __name__ == "__main__":
    target = Path(__file__).with_name("send_harizon_" + "telegram_run_report_v12.py")
    runpy.run_path(str(target), run_name="__main__")
