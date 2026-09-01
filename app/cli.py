from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.reporting import CoverageAuditService, ReportingSQLiteExporter, TrainingDatasetExporter
from app.reporting.history_guard_audit import HistoryGuardAuditService
from app.services.runner import PredictionRunner
from app.services.runtime_preflight import RuntimePreflight
from app.state import resolve_run_history_roots

LOG_LEVEL = os.getenv('LOG_LEVEL', 'INFO').upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
)

_SECRET_QUERY_RE = re.compile(
    r'(?i)([?&](?:apiKey|apikey|APIkey|api_key|key|appid|token|access_token|auth_token|secret)=)([^&\s\"]+)'
)
_SECRET_HEADER_RE = re.compile(
    r'(?i)((?:authorization|x-auth-token|x-apisports-key|x-rapidapi-key)\s*[:=]\s*)([^,\s\"]+)'
)


def _redact_log_text(value: Any) -> str:
    text = str(value)
    text = _SECRET_QUERY_RE.sub(r'\1***', text)
    text = _SECRET_HEADER_RE.sub(r'\1***', text)
    return text


class _SecretRedactionFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = _redact_log_text(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def _install_log_redaction() -> None:
    redaction_filter = _SecretRedactionFilter()
    root_logger = logging.getLogger()
    root_logger.addFilter(redaction_filter)
    for handler in root_logger.handlers:
        handler.addFilter(redaction_filter)
    for logger_name in ('httpx', 'httpcore'):
        logging.getLogger(logger_name).addFilter(redaction_filter)


_install_log_redaction()


def _parse_bool(value: str) -> bool:
    return str(value or '').strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def _parse_int(value: str) -> int:
    return int(float(str(value).strip()))


def _first_env_value(*names: str) -> str | None:
    for name in names:
        raw = os.getenv(name)
        if raw is not None and str(raw).strip() != '':
            return str(raw).strip()
    return None


def _apply_focused_alpha_policy(summary: dict[str, Any] | None = None) -> None:
    try:
        from app.services.focused_alpha_runtime_policy import apply

        result = apply(force=True)
        if isinstance(summary, dict):
            summary['focused_alpha_runtime_policy'] = result
    except Exception as exc:
        logging.getLogger(__name__).warning(
            'focused alpha runtime policy failed: %s: %s',
            type(exc).__name__,
            exc,
        )
        if isinstance(summary, dict):
            summary['focused_alpha_runtime_policy_error'] = f'{type(exc).__name__}: {exc}'


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

    # Provider coverage has a wider lookahead than publication. Keep the runner's
    # publish window narrow: the full-horizon provider patch uses the collection
    # window independently and must not turn a 36h data horizon into a 36h
    # Telegram publication horizon.
    data_window_raw = _first_env_value(
        'HARIZON_DATA_COLLECTION_WINDOW_HOURS',
        'DATA_COLLECTION_WINDOW_HOURS',
        'RUNTIME_DATA_COLLECTION_WINDOW_HOURS',
        'HARIZON_COVERAGE_UPLIFT_NEAR_WINDOW_HOURS',
        'DAY_INVENTORY_NEAR_WINDOW_HOURS',
    )
    if data_window_raw:
        try:
            data_window_hours = max(
                int(float(data_window_raw)),
                int(getattr(settings, 'publish_window_hours', 0) or 0),
            )
        except Exception:
            data_window_hours = 0
        if data_window_hours > int(getattr(settings, 'publish_window_hours', 0) or 0):
            disable_main_publication = _parse_bool(
                os.getenv('HARIZON_DISABLE_MAIN_PUBLICATION_FOR_DATA_WINDOW', 'false')
            )
            if disable_main_publication:
                object.__setattr__(settings, 'prediction_publication_enabled', False)
            os.environ['HARIZON_EFFECTIVE_DATA_COLLECTION_WINDOW_HOURS'] = str(data_window_hours)
            os.environ['HARIZON_MAIN_PUBLICATION_DISABLED_FOR_DATA_WINDOW'] = (
                'true' if disable_main_publication else 'false'
            )
    return settings


def _install_bzzoiro_v2_source_matrix(summary: dict[str, Any] | None = None) -> None:
    if not _parse_bool(os.getenv('HARIZON_BZZOIRO_V2_SOURCE_MATRIX_BOOTSTRAP_ENABLED', 'true')):
        return
    try:
        from app.services.bzzoiro_v2_source_matrix_runtime_patch import install

        result = install()
        if isinstance(summary, dict):
            summary['bzzoiro_v2_source_matrix_install'] = result
    except Exception as exc:
        logging.getLogger(__name__).warning(
            'bzzoiro v2 source matrix install failed: %s: %s',
            type(exc).__name__,
            exc,
        )
        if isinstance(summary, dict):
            summary['bzzoiro_v2_source_matrix_install_error'] = f'{type(exc).__name__}: {exc}'
        try:
            out = Path('.data/exports/latest-bzzoiro-v2-source-matrix-install.json')
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(
                json.dumps(
                    {'installed': False, 'error': f'{type(exc).__name__}: {exc}'},
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                + '\n',
                encoding='utf-8',
            )
        except Exception:
            pass


def _bridge_runtime_context_coverage(summary: dict[str, Any] | None) -> None:
    if not _parse_bool(os.getenv('HARIZON_RUNTIME_CONTEXT_COVERAGE_BRIDGE_ENABLED', 'true')):
        return
    try:
        from scripts.bridge_runtime_context_coverage import main as bridge_main

        code = bridge_main()
        if isinstance(summary, dict):
            summary['runtime_context_coverage_bridge_exit_code'] = code
    except Exception as exc:
        logging.getLogger(__name__).warning(
            'runtime context coverage bridge failed: %s: %s',
            type(exc).__name__,
            exc,
        )
        if isinstance(summary, dict):
            summary['runtime_context_coverage_bridge_error'] = f'{type(exc).__name__}: {exc}'


def _bridge_bzzoiro_offer_overlap(summary: dict[str, Any] | None) -> None:
    if not _parse_bool(os.getenv('HARIZON_BZZOIRO_OFFER_OVERLAP_BRIDGE_ENABLED', 'true')):
        return
    try:
        from scripts.bridge_bzzoiro_offer_overlap import main as bridge_main

        code = bridge_main()
        if isinstance(summary, dict):
            summary['bzzoiro_offer_overlap_bridge_exit_code'] = code
    except Exception as exc:
        logging.getLogger(__name__).warning(
            'runtime context coverage bridge failed: %s: %s',
            type(exc).__name__,
            exc,
        )
        if isinstance(summary, dict):
            summary['bzzoiro_offer_overlap_bridge_error'] = f'{type(exc).__name__}: {exc}'


async def _dispatch_async(command: str, settings: Any) -> tuple[int, dict[str, Any] | None]:
    if command == 'run-once':
        install_summary: dict[str, Any] = {}
        try:
            from app.services.runbot_discovery_checkpoint_patch import (
                install as install_discovery_checkpoint,
            )

            install_summary['runbot_discovery_checkpoint_install'] = install_discovery_checkpoint()
        except Exception as exc:
            install_summary['runbot_discovery_checkpoint_install_error'] = f'{type(exc).__name__}: {exc}'
        _install_bzzoiro_v2_source_matrix(install_summary)
        await asyncio.to_thread(RuntimePreflight(settings).run_before_prediction)
        # Discovery/preflight compatibility modules still carry the former 300-row
        # accumulation policy. Reassert the decision-focused contract immediately
        # before PredictionRunner starts.
        _apply_focused_alpha_policy(install_summary)
        try:
            from app.services.provider_wall_clock_final_guard import (
                install as install_provider_wall_clock,
            )

            install_summary['provider_wall_clock_final_guard_install'] = install_provider_wall_clock()
        except Exception as exc:
            install_summary['provider_wall_clock_final_guard_install_error'] = f'{type(exc).__name__}: {exc}'
        runner = PredictionRunner(settings)
        summary = await runner.run_once()
        if isinstance(summary, dict):
            summary.update(install_summary)
        _bridge_runtime_context_coverage(summary)
        _bridge_bzzoiro_offer_overlap(summary)
        return 0, summary
    return 1, None


def _dispatch_sync(command: str, settings: Any) -> tuple[int, dict[str, Any] | None]:
    if command == 'coverage-audit':
        report = CoverageAuditService(
            _reporting_path(settings, 'coverage_report_path', 'coverage-audit.json')
        ).build(debug_path=settings.debug_path)
        return 0, report
    if command == 'reporting-sqlite':
        history_root = [str(path) for path in resolve_run_history_roots(settings)]
        result = ReportingSQLiteExporter(
            _reporting_path(settings, 'reporting_sqlite_path', 'reporting.sqlite')
        ).export(
            state_path=settings.state_path,
            history_root=history_root,
        )
        return 0, result
    if command == 'training-dataset':
        result = TrainingDatasetExporter(
            _reporting_path(settings, 'training_dataset_path', 'training-dataset.csv')
        ).export(state_path=settings.state_path)
        return 0, result
    if command == 'history-guard-audit':
        history_root = [str(path) for path in resolve_run_history_roots(settings)]
        result = HistoryGuardAuditService(
            _reporting_path(settings, 'history_guard_audit_path', 'history-guard-audit.json')
        ).build(history_root=history_root)
        return 0, result
    return 1, None


async def _main(argv: Sequence[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    preflight = RuntimePreflight()
    preflight.apply_safe_defaults()
    if args and args[0] == 'run-once':
        preflight.apply_phase_policy()
        # Must run after autonomous phase policy and before Settings is loaded.
        _apply_focused_alpha_policy()
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

    print('Usage: python -m app.cli run-once | coverage-audit | reporting-sqlite | training-dataset')
    return 1


def main_sync(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_main(argv))


if __name__ == '__main__':
    raise SystemExit(main_sync())
