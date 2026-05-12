from __future__ import annotations

import asyncio
import json
import logging
import os
import re
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


def _setdefault_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ.setdefault(key, value)


def _apply_api_max_runtime_overrides() -> None:
    """Install safe runtime defaults without clobbering workflow/governor policy."""
    _setdefault_env(
        {
            'STRICT_PRICE_INTEGRITY_ENABLED': 'true',
            'STRICT_PRICE_INTEGRITY_MIN_PRICE_SOURCES': '2',
            'STRICT_PRICE_INTEGRITY_MIN_BOOKMAKERS': '2',
            'PUBLISH_REJECT_CONTEXT_AS_PRICE_CONFIRMATION': 'false',
            'PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE': 'true',
            'MIN_BOOKS_FOR_CONSENSUS': '2',
            'MIN_BOOKS_PUBLISH': '2',
            'MIN_SOURCES_PUBLISH': '2',
            'MARKET_DERIVED_MIN_BOOKS': '2',
            'MARKET_DERIVED_MIN_SOURCES': '2',
            'CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM': 'true',
            'CONTROLLED_FALLBACK_REQUIRE_ODDS_SOURCE_DIVERSITY': 'true',
            'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES': '2',
            'TELEGRAM_MIN_ODDS_SOURCES': '2',
            'MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS': '1.45',
            'MATCH_TOTAL_OVER15_MIN_EXACT_BOOKS': '3',
            'MATCH_TOTAL_OVER15_ABSOLUTE_PRICE_GUARD_ENABLED': 'true',
            'MATCH_TOTAL_OVER15_ABSOLUTE_MAX_ODDS': '1.55',
            'ENABLE_QUARTER_TOTAL_LINES': 'true',
            'QUARTER_TOTAL_MIN_BOOKS': '2',
        }
    )
    try:
        from app.services import api_runtime_enhancements
        api_runtime_enhancements.install()
    except Exception:
        pass
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


def _install_prediction_candidate_runtime_patches(stage: str = 'cli') -> None:
    """Re-apply runtime patches after discovery/bootstrap wrappers."""
    results: dict[str, Any] = {}
    try:
        from app.services import sstats_bzzoiro_odds_merge_patch
        results['sstats_bzzoiro_odds_merge'] = sstats_bzzoiro_odds_merge_patch.install()
    except Exception as exc:
        results['sstats_bzzoiro_odds_merge'] = f'{type(exc).__name__}: {exc}'
        logging.getLogger(__name__).warning('odds merge install failed at %s: %s: %s', stage, type(exc).__name__, exc)
    try:
        from app.services import candidate_value_final_reinstall
        results['candidate_value_final_reinstall'] = candidate_value_final_reinstall.install()
    except Exception as exc:
        results['candidate_value_final_reinstall'] = f'{type(exc).__name__}: {exc}'
        logging.getLogger(__name__).warning('candidate value final install failed at %s: %s: %s', stage, type(exc).__name__, exc)
    try:
        export_dir = Path('.data/exports')
        export_dir.mkdir(parents=True, exist_ok=True)
        (export_dir / 'latest-cli-final-runtime-install.json').write_text(
            json.dumps({'stage': stage, 'results': results}, ensure_ascii=False, indent=2) + '\n',
            encoding='utf-8',
        )
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


def _prepare_discovery_first_inventory_for_run_once() -> None:
    if not _parse_bool(os.getenv('RUNBOT_DISCOVERY_FIRST_PREPARE_ENABLED', 'true')):
        return
    if os.getenv('RUNBOT_DISCOVERY_FIRST_PREPARE_RUNNING') == '1':
        return
    os.environ['RUNBOT_DISCOVERY_FIRST_PREPARE_RUNNING'] = '1'
    _setdefault_env(
        {
            'HARIZON_PROVIDER_TIER_STRATEGY_VERSION': 'primary-three-v1-100-per-run',
            'HARIZON_PRIMARY_PROVIDERS': 'odds_api_io,bzzoiro,sstats',
            'HARIZON_SUPPLEMENTAL_API_MODE': 'top_pick_backfill_only',
            'SUPPLEMENTAL_PROVIDERS_REQUIRE_SHORTLIST': 'true',
            'SUPPLEMENTAL_PROVIDERS_REQUIRE_MISSING_ROLE': 'true',
            'SUPPLEMENTAL_BACKFILL_AFTER_PRIMARY_SHORTLIST': 'true',
            'ODDS_API_IO_MAX_REQUESTS_PER_RUN': '200',
            'ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN': '200',
            'ODDS_API_IO_ACCOUNT1_PER_RUN_MAX': '100',
            'ODDS_API_IO_ACCOUNT2_PER_RUN_MAX': '100',
            'BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN': '100',
            'BZZOIRO_MAX_REQUESTS_PER_RUN': '100',
            'BZZOIRO_CONTEXT_MATCH_LIMIT': '300',
            'SSTATS_MAX_HTTP_REQUESTS_PER_RUN': '100',
            'SSTATS_MAX_REQUESTS_PER_RUN': '100',
            'SSTATS_CONTEXT_MATCH_LIMIT': '300',
            'SSTATS_DEEP_ENRICHMENT_ENABLED': 'true',
            'SSTATS_DEEP_DETAIL_LIMIT_PER_RUN': '80',
            'SSTATS_GAME_DETAIL_LIMIT_PER_RUN': '8',
            'SSTATS_ODDS_RESCUE_LIMIT_PER_RUN': '120',
            'SSTATS_ODDS_RESCUE_ONLY_IF_ODDS_SOURCES_LT': '2',
            'PROVIDER_DAY_DISCOVERY_MAX_SECONDS': '120',
            'PROVIDER_DAY_DISCOVERY_TIMEOUT_SECONDS': '16',
            'PROVIDER_DAY_DISCOVERY_CONCURRENCY': '5',
            'PROVIDER_DAY_DISCOVERY_MIN_SCORE': '0.74',
            'SPORTLOGIC_ENABLED': 'false',
            'ENABLE_SPORTLOGIC': 'false',
            'SPORTLOGIC_MAX_REQUESTS_PER_RUN': '0',
        }
    )
    try:
        from scripts import runbot_discovery_first_prepare
        runbot_discovery_first_prepare.main()
    except Exception as exc:
        logging.getLogger(__name__).warning(
            'discovery-first runbot preparation failed; continuing run-once: %s: %s',
            type(exc).__name__,
            exc,
        )
    finally:
        os.environ.pop('RUNBOT_DISCOVERY_FIRST_PREPARE_RUNNING', None)


async def _dispatch_async(command: str, settings: Any) -> tuple[int, dict[str, Any] | None]:
    if command == 'run-once':
        await asyncio.to_thread(_prepare_discovery_first_inventory_for_run_once)
        _install_prediction_candidate_runtime_patches(stage='after_discovery_before_runner')
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
        result = HistoryGuardAuditService(_reporting_path(settings, 'history_guard_audit_path', 'history-guard-audit.json')).build(history_root=history_root)
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

    print('Usage: python -m app.cli run-once | coverage-audit | reporting-sqlite | training-dataset')
    return 1


def main_sync(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_main(argv))


if __name__ == '__main__':
    raise SystemExit(main_sync())
