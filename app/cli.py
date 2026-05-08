from __future__ import annotations

import asyncio
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any, Sequence

from app.config import get_settings
from app.reporting import CoverageAuditService, ReportingSQLiteExporter, TrainingDatasetExporter
from app.reporting.history_guard_audit import HistoryGuardAuditService
from app.services.runner import PredictionRunner
from app.state import resolve_run_history_roots

LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)


def _parse_bool(value: str) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def _parse_int(value: str) -> int:
    return int(float(str(value).strip()))


def _setdefault_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ.setdefault(key, value)


def _apply_api_max_runtime_overrides() -> None:
    """Install safe runtime defaults without clobbering workflow/governor policy.

    Older versions overwrote env on every CLI start and re-enabled strict
    Telegram/source-diversity guards. That made GitHub workflow values and the
    quota governor ineffective. This function now sets only compatibility
    defaults when the caller did not already provide a value.
    """
    _setdefault_env(
        {
            'STRICT_PRICE_INTEGRITY_ENABLED': 'true',
            'STRICT_PRICE_INTEGRITY_MIN_PRICE_SOURCES': '2',
            'STRICT_PRICE_INTEGRITY_MIN_BOOKMAKERS': '2',
            'PUBLISH_REJECT_CONTEXT_AS_PRICE_CONFIRMATION': 'false',
            'PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE': 'true',
            'CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM': 'false',
            'CONTROLLED_FALLBACK_REQUIRE_ODDS_SOURCE_DIVERSITY': 'false',
            'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES': '1',
            'TELEGRAM_MIN_ODDS_SOURCES': '1',
            'MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS': '1.65',
            'MATCH_TOTAL_OVER15_MIN_EXACT_BOOKS': '3',
            'ENABLE_QUARTER_TOTAL_LINES': 'true',
            'QUARTER_TOTAL_MIN_BOOKS': '2',
        }
    )
    try:
        from app.services import market_integrity
        market_integrity.install()
    except Exception:
        pass
    try:
        from app.providers import odds_api_io_startup_compat
        odds_api_io_startup_compat.install()
    except Exception:
        pass


def _reporting_path(settings: Any, attr_name: str, default_name: str) -> str:
    value = getattr(settings, attr_name, None)
    if str(value or '').strip():
        return str(value)
    env_value = os.getenv(attr_name.upper())
    if str(env_value or '').strip():
        return str(env_value)
    state_path = Path(getattr(settings, 'state_path', '.data/state.json'))
    export_root = Path(getattr(settings, 'storage_export_dir', state_path.parent / 'exports'))
    export_root.mkdir(parents=True, exist_ok=True)
    return str(export_root / default_name)


def _apply_runtime_env_overrides(settings: Any) -> Any:
    overrides: list[tuple[str, str, Any]] = [
        ('publish_dry_run', 'PUBLISH_DRY_RUN', _parse_bool),
        ('prediction_publication_enabled', 'PREDICTION_PUBLICATION_ENABLED', _parse_bool),
        ('run_report_enabled', 'RUN_REPORT_ENABLED', _parse_bool),
        ('daily_report_enabled', 'DAILY_REPORT_ENABLED', _parse_bool),
        ('daily_report_send_telegram', 'DAILY_REPORT_SEND_TELEGRAM', _parse_bool),
        ('daily_report_hour_local', 'DAILY_REPORT_HOUR_LOCAL', _parse_int),
        ('daily_report_target_offset_days', 'DAILY_REPORT_TARGET_OFFSET_DAYS', _parse_int),
        ('daily_report_min_bets', 'DAILY_REPORT_MIN_BETS', _parse_int),
        ('daily_report_resend_on_change', 'DAILY_REPORT_RESEND_ON_CHANGE', _parse_bool),
        ('publish_window_hours', 'PUBLISH_WINDOW_HOURS', _parse_int),
        ('min_kickoff_lead_minutes', 'MIN_KICKOFF_LEAD_MINUTES', _parse_int),
        ('max_picks_per_run', 'MAX_PICKS_PER_RUN', _parse_int),
    ]
    for attr_name, env_name, parser in overrides:
        raw = os.getenv(env_name)
        if raw is None or str(raw).strip() == '':
            continue
        try:
            value = parser(raw)
        except Exception:
            continue
        object.__setattr__(settings, attr_name, value)
    return settings


async def _dispatch_async(command: str, settings: Any) -> tuple[int, dict[str, Any] | None]:
    if command == 'run-once':
        runner = PredictionRunner(settings)
        summary = await runner.run_once()
        return 0, summary
    return 1, None


def _dispatch_sync(command: str, settings: Any) -> tuple[int, dict[str, Any] | None]:
    if command == 'coverage-audit':
        report = CoverageAuditService(_reporting_path(settings, 'coverage_report_path', 'coverage-audit.json')).build(debug_path=settings.debug_path)
        return 0, report
    if command == 'reporting-sqlite':
        history_root = [str(path) for path in resolve_run_history_roots(settings)]
        result = ReportingSQLiteExporter(_reporting_path(settings, 'reporting_sqlite_path', 'reporting.sqlite')).export(
            state_path=settings.state_path,
            history_root=history_root,
        )
        return 0, result
    if command == 'training-dataset':
        result = TrainingDatasetExporter(_reporting_path(settings, 'training_dataset_path', 'training-dataset.csv')).export(state_path=settings.state_path)
        return 0, result
    if command == 'history-guard-audit':
        history_root = [str(path) for path in resolve_run_history_roots(settings)]
        result = HistoryGuardAuditService(
            _reporting_path(settings, 'history_guard_audit_path', 'history-guard-audit.json')
        ).build(history_root=history_root)
        return 0, result
    return 1, None


async def _main(argv: Sequence[str] | None = None) -> int:
    _apply_api_max_runtime_overrides()
    args = list(argv if argv is not None else sys.argv[1:])
    settings = _apply_runtime_env_overrides(get_settings())
    command = args[0] if args else ''

    exit_code, payload = _dispatch_sync(command, settings)
    if payload is not None:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return exit_code

    exit_code, payload = await _dispatch_async(command, settings)
    if payload is not None:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return exit_code

    print('Usage: python -m app.cli run-once | coverage-audit | reporting-sqlite | training-dataset | history-guard-audit')
    return 1


def main_sync(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_main(argv))


if __name__ == '__main__':
    raise SystemExit(main_sync())
