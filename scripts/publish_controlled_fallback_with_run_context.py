from __future__ import annotations

"""Run controlled fallback publisher with GitHub Actions metadata in Telegram.

The original publisher remains untouched. This wrapper patches only its
send_telegram call, so publication logic, guards, dedupe and ledgers are exactly
those of scripts/publish_controlled_fallback.py.
"""

import importlib.util
import json
from pathlib import Path
from typing import Any

from app.services.github_actions_context import append_github_run_reference, github_run_context, write_github_run_context

TARGET = Path(__file__).with_name("publish_controlled_fallback.py")
STATUS_PATH = Path(".data/exports/latest-controlled-fallback-run-context-wrapper.json")


def _load_target() -> Any:
    spec = importlib.util.spec_from_file_location("publish_controlled_fallback_original", TARGET)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TARGET}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_status(payload: dict[str, Any]) -> None:
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


publisher = _load_target()
_original_send_telegram = publisher.send_telegram


def send_telegram_with_run_context(message: Any):
    write_github_run_context()
    return _original_send_telegram(append_github_run_reference(message))


publisher.send_telegram = send_telegram_with_run_context
_write_status({"status": "installed", "wrapper": "controlled-fallback-github-run-reference", "github_actions": github_run_context()})


if __name__ == "__main__":
    raise SystemExit(publisher.main())
