from __future__ import annotations

"""HARIZON workflow bootstrap hooks.

GitHub checkout can contain committed .data/exports/latest-* artifacts from an
older run. Also, if the workflow misses the provider-budget step, the hardcoded
env block keeps old grants (odds_api_io 120, bzzoiro 24, sstats 36). Python
loads sitecustomize automatically, so for the production `app.cli run-once`
entrypoint we enforce two safe startup actions before app settings are loaded:

1. remove volatile latest-run exports so Telegram cannot send stale reports;
2. apply config/provider_runtime_policy.json into environment so the latest
   coverage policy is used even if the workflow yaml is behind.

The hook is intentionally narrow: it runs only for app.cli run-once, not for
report builders or other scripts.
"""

import os
import sys
from pathlib import Path


def _is_run_once() -> bool:
    argv = " ".join(str(x) for x in sys.argv).lower()
    return "run-once" in argv and ("app.cli" in argv or "cli.py" in argv or "-m" in argv)


def _run_cleanup() -> None:
    if str(os.getenv("HARIZON_STARTUP_CLEAR_STALE_EXPORTS", "true")).strip().lower() in {"0", "false", "no", "off"}:
        return
    try:
        from scripts.clear_stale_run_exports import main as cleanup_main
        cleanup_main()
    except Exception as exc:
        try:
            out = Path(".data/exports/latest-run-export-cleanup-error.txt")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        except Exception:
            pass


def _apply_provider_policy() -> None:
    if str(os.getenv("HARIZON_STARTUP_APPLY_PROVIDER_POLICY", "true")).strip().lower() in {"0", "false", "no", "off"}:
        return
    try:
        from scripts.apply_provider_request_budget import main as policy_main
        policy_main()
    except SystemExit:
        # apply_provider_request_budget may use SystemExit(main()). Environment
        # writes have already happened; never abort the production run here.
        pass
    except Exception as exc:
        try:
            out = Path(".data/exports/latest-provider-policy-startup-error.txt")
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        except Exception:
            pass


if _is_run_once():
    _run_cleanup()
    _apply_provider_policy()
