from __future__ import annotations

import atexit
import os
import sys


def _enabled(name: str, default: str = 'true') -> bool:
    return str(os.getenv(name, default)).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def _is_run_once() -> bool:
    argv = ' '.join(str(x) for x in sys.argv).lower()
    return 'run-once' in argv and ('app.cli' in argv or 'cli.py' in argv or '-m' in argv)


def _install_bzzoiro_v2_source_matrix() -> None:
    if not _enabled('HARIZON_BZZOIRO_V2_SOURCE_MATRIX_BOOTSTRAP_ENABLED'):
        return
    try:
        from app.services.bzzoiro_v2_source_matrix_runtime_patch import install
        install()
    except Exception:
        pass


def _run_bzzoiro_offer_bridge_after_cli() -> None:
    if not _enabled('HARIZON_BZZOIRO_OFFER_OVERLAP_BRIDGE_ENABLED'):
        return
    try:
        from scripts.bridge_bzzoiro_offer_overlap import main as bridge_main
        bridge_main()
    except Exception:
        pass
    try:
        from scripts.repair_bzzoiro_overlap_inventory_sources import main as repair_main
        repair_main()
    except Exception:
        pass


if _is_run_once():
    _install_bzzoiro_v2_source_matrix()
    atexit.register(_run_bzzoiro_offer_bridge_after_cli)
