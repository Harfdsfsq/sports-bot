"""Compatibility package that repairs HARIZON report v14 publication truth.

Python resolves this package before the sibling ``send_harizon_telegram_run_report_v14.py``
module. The original implementation is loaded under a private name and kept intact,
while forecast publication counting is restricted to explicit pick counters. Generic
Telegram messages may be daily or settlement reports and are never forecast evidence.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

_IMPL_PATH = Path(__file__).resolve().parents[1] / "send_harizon_telegram_run_report_v14.py"
_SPEC = importlib.util.spec_from_file_location("harizon_report_v14_file_impl", _IMPL_PATH)
if _SPEC is None or _SPEC.loader is None:
    raise RuntimeError("cannot load HARIZON report v14 file implementation")
_IMPL = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_IMPL)

_DEFAULT_EXPORT = _IMPL.EXPORT
_DEFAULT_DEBUG = _IMPL.DEBUG
_DEFAULT_LIFECYCLE = _IMPL.LIFECYCLE
_DEFAULT_STEP_STATUS = _IMPL.STEP_STATUS
_DEFAULT_RUN_LOG = _IMPL.RUN_LOG

EXPORT = _DEFAULT_EXPORT
DEBUG = _DEFAULT_DEBUG
LIFECYCLE = _DEFAULT_LIFECYCLE
STEP_STATUS = _DEFAULT_STEP_STATUS
RUN_LOG = _DEFAULT_RUN_LOG


def _counter(value: Any) -> int:
    try:
        return max(0, int(float(str(value))))
    except Exception:
        return 0


def _debug_main_publication_count(summary: dict[str, Any]) -> tuple[int, bool]:
    """Count only forecast sends, never daily/settlement Telegram messages."""

    keys = (
        "published_to_telegram",
        "telegram_picks_sent",
        "published_current_run",
        "published",
    )
    declared = any(key in summary for key in keys)
    return max((_counter(summary.get(key)) for key in keys), default=0), declared


_ORIGINAL_REPAIR_PAYLOAD = _IMPL.repair_payload
_IMPL._debug_main_publication_count = _debug_main_publication_count


def _sync_impl_overrides() -> None:
    """Forward compatibility-package monkeypatches to the file implementation."""

    export = Path(EXPORT)
    _IMPL.EXPORT = export
    _IMPL.DEBUG = DEBUG
    _IMPL.LIFECYCLE = (
        export / "latest-main-run-lifecycle.json"
        if export != _DEFAULT_EXPORT and LIFECYCLE == _DEFAULT_LIFECYCLE
        else LIFECYCLE
    )
    _IMPL.STEP_STATUS = (
        export / "latest-run-bot-step-status.json"
        if export != _DEFAULT_EXPORT and STEP_STATUS == _DEFAULT_STEP_STATUS
        else STEP_STATUS
    )
    _IMPL.RUN_LOG = (
        export / "latest-run-bot.log"
        if export != _DEFAULT_EXPORT and RUN_LOG == _DEFAULT_RUN_LOG
        else RUN_LOG
    )
    if "_read_text" in globals():
        _IMPL._read_text = globals()["_read_text"]


def repair_payload(payload: Any, *, now: Any = None) -> dict[str, Any]:
    _sync_impl_overrides()
    repaired = _ORIGINAL_REPAIR_PAYLOAD(payload, now=now)
    try:
        _debug, summary, _error = _IMPL._debug_truth(now)
        total_messages = _counter(summary.get("telegram_messages_sent"))
        pick_messages, _declared = _debug_main_publication_count(summary)
        funnel = repaired.setdefault("funnel", {})
        diagnostics = funnel.setdefault(
            "main_pipeline_publication_counter_diagnostics",
            {},
        )
        diagnostics["debug_total_telegram_messages"] = total_messages
        diagnostics["debug_forecast_picks_sent"] = pick_messages
        diagnostics["debug_non_pick_telegram_messages"] = max(
            0,
            total_messages - pick_messages,
        )
        diagnostics["generic_telegram_message_counted_as_forecast"] = False
    except Exception:
        pass
    return repaired


_IMPL.repair_payload = repair_payload


def main() -> int:
    _sync_impl_overrides()
    return int(_IMPL.main() or 0)


def __getattr__(name: str) -> Any:
    return getattr(_IMPL, name)


__all__ = ["_debug_main_publication_count", "main", "repair_payload"]
