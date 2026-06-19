from __future__ import annotations

import atexit
import os
import sys


def _run_bzzoiro_offer_bridge_after_cli() -> None:
    raw = str(os.getenv('HARIZON_BZZOIRO_OFFER_OVERLAP_BRIDGE_ENABLED', 'true')).strip().lower()
    if raw not in {'1', 'true', 'yes', 'on', 'force'}:
        return
    try:
        from scripts.bridge_bzzoiro_offer_overlap import main as bridge_main
        bridge_main()
    except Exception:
        pass


_argv = ' '.join(str(x) for x in sys.argv).lower()
if 'run-once' in _argv and ('app.cli' in _argv or 'cli.py' in _argv or '-m' in _argv):
    atexit.register(_run_bzzoiro_offer_bridge_after_cli)
