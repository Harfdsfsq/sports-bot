from __future__ import annotations

from pathlib import Path
import runpy


def _sanitize() -> None:
    try:
        from scripts.sanitize_line_movement_value_waits import main as sanitize_main
        sanitize_main()
    except Exception:
        pass


if __name__ == "__main__":
    _sanitize()
    target = Path(__file__).with_name("send_harizon_" + "telegram_run_report_v12.py")
    runpy.run_path(str(target), run_name="__main__")
