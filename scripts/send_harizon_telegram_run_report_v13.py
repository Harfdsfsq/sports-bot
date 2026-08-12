from __future__ import annotations

"""HARIZON report v13 wrapper: verified coverage + final report repairs."""

import importlib.util
import json
import os
from pathlib import Path
from typing import Any

EXPORT = Path(".data/exports")
OUT_TXT = EXPORT / "latest-harizon-telegram-run-report.txt"
OUT_JSON = EXPORT / "latest-harizon-telegram-run-report.json"


def _load_v5() -> Any:
    path = Path(__file__).with_name("send_harizon_telegram_run_report_v5.py")
    spec = importlib.util.spec_from_file_location("harizon_report_v5_loaded", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load report v5")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _refresh_truth() -> None:
    for module_name, name in (
        ("scripts.repair_day_inventory_blank_rows", "main"),
        ("scripts.bridge_runtime_context_coverage", "main"),
        ("scripts.build_day_inventory_coverage_truth", "main"),
        ("scripts.day_inventory_cumulative_coverage", "main"),
        ("scripts.harizon_learning_report", "main"),
        ("scripts.harizon_line_movement_diagnostics", "main"),
    ):
        try:
            module = __import__(module_name, fromlist=[name])
            fn = getattr(module, name, None)
            if callable(fn):
                fn()
        except SystemExit:
            pass
        except Exception:
            pass


def _write(payload: dict[str, Any], text: str) -> None:
    EXPORT.mkdir(parents=True, exist_ok=True)
    payload["text_length"] = len(text)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    OUT_TXT.write_text(text + "\n", encoding="utf-8")
    (EXPORT / "latest-harizon-telegram-run-report-v5.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (EXPORT / "latest-harizon-telegram-run-report-v5.txt").write_text(text + "\n", encoding="utf-8")


def main() -> int:
    _refresh_truth()
    v5 = _load_v5()
    payload = v5.build_payload()
    text = v5.render(payload)
    try:
        from scripts.harizon_report_runtime_repairs import patch
        payload, text = patch(payload, text)
    except Exception:
        pass
    payload["telegram_sent"] = False
    _write(payload, text)
    if os.getenv("TELEGRAM_CHAT_ID") and (os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")):
        payload["telegram_sent"] = bool(v5.send_telegram(text))
        _write(payload, text)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
