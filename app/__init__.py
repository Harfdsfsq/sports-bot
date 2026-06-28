from __future__ import annotations

import atexit
import os
import sys


def _enabled(name: str, default: str = 'true') -> bool:
    return str(os.getenv(name, default)).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def _is_run_once() -> bool:
    argv = ' '.join(str(x) for x in sys.argv).lower()
    return 'run-once' in argv and ('app.cli' in argv or 'cli.py' in argv or '-m' in argv)


def _is_report_only_run() -> bool:
    return (
        _enabled('DAILY_REPORT_ENABLED', 'false')
        and not _enabled('PREDICTION_PUBLICATION_ENABLED', 'false')
        and not _enabled('CONTROLLED_FALLBACK_ENABLED', 'false')
    )


def _sync_publication_ledger_before_cli() -> None:
    if not _enabled('HARIZON_PUBLICATION_LEDGER_BOOTSTRAP_SYNC_ENABLED'):
        return
    try:
        from scripts.sync_publication_ledger import sync_bets
        sync_bets()
    except Exception:
        pass


def _sync_publication_ledger_after_cli() -> None:
    if not _enabled('HARIZON_PUBLICATION_LEDGER_BOOTSTRAP_SYNC_ENABLED'):
        return
    try:
        from scripts.sync_publication_ledger import main as sync_main
        sync_main()
    except Exception:
        pass


def _send_past_predictions_report_after_cli() -> None:
    # Disabled by default.  The retrospective passability report is intended to
    # be launched from its own manual GitHub Actions workflow, not from every
    # daily/report-only run.  Set PAST_PREDICTIONS_REPORT_AUTOSEND_ENABLED=true
    # only for an explicit temporary override.
    if not _enabled('PAST_PREDICTIONS_REPORT_AUTOSEND_ENABLED', 'false'):
        return
    if not _is_report_only_run():
        return
    try:
        from scripts import send_past_predictions_report
        old_argv = list(sys.argv)
        argv = ['send_past_predictions_report.py', '--all', '--send-telegram', '--force']
        sys.argv = argv
        try:
            send_past_predictions_report.main()
        finally:
            sys.argv = old_argv
    except Exception:
        pass


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
    _sync_publication_ledger_before_cli()
    _install_bzzoiro_v2_source_matrix()
    atexit.register(_run_bzzoiro_offer_bridge_after_cli)
    atexit.register(_sync_publication_ledger_after_cli)
    atexit.register(_send_past_predictions_report_after_cli)
