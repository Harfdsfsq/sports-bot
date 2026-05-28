from __future__ import annotations

"""HARIZON Telegram run report v9.

Small wrapper around v8 that appends GitHub Actions run metadata to the human
Telegram report. This lets a forwarded Telegram report identify the exact run,
logs and run-bot artifact without manually uploading ZIP files.
"""

import importlib.util
import json
from pathlib import Path
from typing import Any

from app.services.github_actions_context import append_github_run_reference, github_run_context, write_github_run_context

V8_PATH = Path(__file__).with_name("send_harizon_telegram_run_report_v8.py")
EXPORT_DIR = Path(".data/exports")
V9_STATUS_PATH = EXPORT_DIR / "latest-harizon-telegram-run-report-v9-status.json"


def _load_v8() -> Any:
    spec = importlib.util.spec_from_file_location("harizon_report_v8", V8_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {V8_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v8 = _load_v8()
_original_render = v8.render


def _write_status(payload: dict[str, Any]) -> None:
    try:
        V9_STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        V9_STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def render(payload: dict[str, Any]) -> str:
    ctx = write_github_run_context()
    try:
        payload.setdefault("diagnostics", {})["github_actions"] = ctx
    except Exception:
        pass
    return append_github_run_reference(_original_render(payload))


v8.render = render
v8.v7.render = render
v8.v7.v5.render = render
_write_status({"status": "installed", "renderer": "v9-github-run-reference", "github_actions": github_run_context()})


if __name__ == "__main__":
    raise SystemExit(v8.v7.v5.main())
