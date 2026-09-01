from __future__ import annotations

from typing import Any


def install(base: Any) -> None:
    if getattr(base, "_current_run_pick_gate_installed", False):
        return

    def gate() -> bool:
        try:
            debug = base.load_json(".logs/debug-last-run.json", {})
            summary = debug.get("summary") if isinstance(debug, dict) else {}
            count = max(
                base.as_int(summary.get("published_to_telegram"), 0),
                base.as_int(summary.get("telegram_picks_sent"), 0),
            )
            return bool(count > 0 and base.env_bool("CONTROLLED_FALLBACK_SKIP_IF_INTERNAL_PUBLISHED", True))
        except Exception:
            return False

    base.already_has_picks = gate
    base._current_run_pick_gate_installed = True
