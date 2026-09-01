from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
import json
import os
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
UTC = timezone.utc
from pathlib import Path
from importlib import import_module
import logging
import math
from typing import Any

import httpx

from app.config import Settings
from app.providers.weather_common import WeatherContextEnricher
from app.schemas import CandidateBet, Match, MatchContext, Offer
from app.services.market_monitor import MarketMonitor
from app.services.model import CandidateFactory
from app.services.coverage_contract import evaluate_publish_candidate, sync_candidate_publish_coverage
from app.services.coverage_planner import CoveragePlanner
from app.services.evidence import (
    build_consensus_lines,
    build_context_bundles,
    build_line_snapshots,
    build_match_serving,
    serialize_dataclass,
)
from app.services.publication_lifecycle import (
    append_sent_candidate_index,
    candidate_dedupe_keys,
    collect_sent_candidate_keys,
    is_sent_pick_row,
    load_sent_candidate_keys,
    mark_candidate_lifecycle,
)
from app.services.publication_tiers import classify_publication_tier
from app.services.quality import PredictionQualityService
from app.services.sheet_export import SheetExportService
from app.services.telegram import TelegramPublisher
from app.services.settlement import SettlementService
from app.state import JsonStateStore, collect_run_archive_paths, resolve_run_history_roots, resolve_run_logs_dir
from app.utils import candidate_selection_key, canonicalize_league_name, canonicalize_team_name, clamp, ensure_utc, parse_datetime

logger = logging.getLogger(__name__)


class PredictionRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.provider_status: dict[str, dict[str, Any]] = {}
        self.provider_runtime_errors: dict[str, list[str]] = defaultdict(list)
        self.bookies_bootstrap = self._safe_provider('app.providers.bookies_bootstrap', 'BookiesBootstrapProvider')
        self.odds_api_io = self._safe_provider('app.providers.odds_api_io', 'OddsApiIoProvider')
        self.bookies_api = self._safe_provider('app.providers.bookies_api', 'BookiesApiProvider')
        self.oddspapi = self._safe_provider('app.providers.oddspapi', 'OddsPapiProvider')
        self.allsportsapi = self._safe_provider('app.providers.allsportsapi', 'AllSportsApiOddsProvider')
        self.sportlogic = self._safe_provider('app.providers.sportlogic_provider', 'SportLogicProvider')
        self.futrixmetrics = self._safe_provider('app.providers.futrixmetrics', 'FutrixMetricsContextProvider')
        self.sstats = self._safe_provider('app.providers.sstats', 'SStatsContextProvider')
        self.bzzoiro = self._safe_provider('app.providers.bzzoiro_v2', 'BzzoiroContextProvider')
        self.api_football = self._safe_provider('app.providers.api_football', 'ApiFootballContextProvider')
        self.espn = self._safe_provider('app.providers.espn', 'EspnContextProvider')
        self.thesportsdb = self._safe_provider('app.providers.thesportsdb', 'TheSportsDbContextProvider')
        self.football_data = self._safe_provider('app.providers.football_data', 'FootballDataContextProvider')
        self.openligadb = self._safe_provider('app.providers.openligadb', 'OpenLigaDbContextProvider')
        self.openfootball = self._safe_provider('app.providers.openfootball', 'OpenFootballContextProvider')
        self.newsapi = self._safe_provider('app.providers.newsapi', 'NewsApiContextProvider')
        self.gnews = self._safe_provider('app.providers.gnews', 'GNewsContextProvider')
        self.weather = WeatherContextEnricher(settings)
        self.factory = CandidateFactory(settings)
        self.quality = PredictionQualityService(settings)
        self.market_monitor = MarketMonitor(settings) if getattr(settings, 'market_monitor_enabled', True) else None
        self.sheet_export = SheetExportService(settings)
        self.telegram = TelegramPublisher(settings)
        self.settlement = SettlementService(settings)
        self.state = JsonStateStore(settings.state_path, settings.debug_path)
        self._seen_published_fingerprints: set[str] = set()

    @staticmethod
    def _provider_name_from_module(module_name: str) -> str:
        if module_name.endswith('sportlogic_provider'):
            return 'sportlogic'
        if module_name.endswith('bzzoiro_v2') or module_name.endswith('bzzoiro'):
            return 'bzzoiro'
        return module_name.rsplit('.', 1)[-1]

    @staticmethod
    def _format_exception(exc: Exception) -> str:
        return f'{type(exc).__name__}: {exc}'

    def _mark_provider_status(self, provider_name: str, **payload: Any) -> None:
        current = dict(self.provider_status.get(provider_name, {}))
        current.update(payload)
        self.provider_status[provider_name] = current

    def _provider_instance_by_key(self, provider_key: str) -> Any | None:
        return {
            'sstats': self.sstats,
            'bzzoiro': self.bzzoiro,
            'api_football': self.api_football,
            'espn': self.espn,
            'thesportsdb': self.thesportsdb,
            'football_data': self.football_data,
            'openligadb': self.openligadb,
            'futrixmetrics': self.futrixmetrics,
            'openfootball': self.openfootball,
            'newsapi': self.newsapi,
            'gnews': self.gnews,
            'odds_api_io': self.odds_api_io,
            'bookies_api': self.bookies_api,
            'oddspapi': self.oddspapi,
            'allsportsapi': self.allsportsapi,
            'sportlogic': self.sportlogic,
            'bookies_bootstrap': self.bookies_bootstrap,
        }.get(str(provider_key or '').strip().lower())

    def _provider_has_required_auth(self, provider_key: str) -> bool:
        key = str(provider_key or '').strip().lower()
        if key == 'api_football':
            return bool(getattr(self.settings, 'api_football_key', None))
        if key == 'football_data':
            return bool(getattr(self.settings, 'football_data_api_key', None))
        if key == 'newsapi':
            return bool(getattr(self.settings, 'newsapi_key', None) or getattr(self.settings, 'currents_key', None))
        if key == 'gnews':
            return bool(getattr(self.settings, 'gnews_key', None))
        if key == 'sstats':
            return bool(getattr(self.settings, 'sstats_api_key', None))
        if key == 'bzzoiro':
            return bool(getattr(self.settings, 'bzzoiro_api_key', None))
        if key == 'futrixmetrics':
            return bool(getattr(self.settings, 'futrixmetrics_api_key', None))
        if key == 'oddspapi':
            return bool(getattr(self.settings, 'oddspapi_api_key', None))
        if key == 'allsportsapi':
            return bool(getattr(self.settings, 'allsportsapi_api_key', None))
        if key == 'sportlogic':
            return bool(os.getenv('SPORTLOGIC_API_KEY') or os.getenv('SPORTLOGIC_KEY') or os.getenv('SPORTLOGIC_TOKEN'))
        return True

    def _provider_cooldown_until(self, provider_key: str) -> datetime | None:
        provider = self._provider_instance_by_key(provider_key)
        if provider is None:
            return None
        checker = getattr(provider, '_cooldown_until', None)
        if not callable(checker):
            return None
        try:
            cooldown_until = checker()
        except Exception as exc:
            self.provider_runtime_errors[str(provider_key or 'unknown')].append(self._format_exception(exc))
            return None
        return cooldown_until if isinstance(cooldown_until, datetime) else None

    def _provider_availability_multiplier(self, provider_key: str) -> float:
        key = str(provider_key or '').strip().lower()
        provider = self._provider_instance_by_key(key)
        if provider is None:
            return 0.0
        status = dict(self.provider_status.get(key, {}))
        if status.get('enabled') is False or status.get('loaded') is False:
            return 0.0
        if not self._provider_has_required_auth(key):
            self._mark_provider_status(key, api_key_present=False, targeting_ready=False)
            return 0.0
        cooldown_until = self._provider_cooldown_until(key)
        if cooldown_until is not None:
            self._mark_provider_status(
                key,
                rate_limited=True,
                cooldown_until=cooldown_until.isoformat(),
                targeting_ready=False,
            )
            return 0.0
        if status.get('runtime_error'):
            self._mark_provider_status(key, targeting_ready=True, degraded=True)
            return 0.25
        if status.get('rate_limited'):
            self._mark_provider_status(key, targeting_ready=True, degraded=True)
            return 0.15
        self._mark_provider_status(key, targeting_ready=True)
        return 1.0

    def _provider_name(self, provider: Any | None) -> str:
        if provider is None:
            return 'unknown'
        module_name = getattr(provider.__class__, '__module__', '')
        if module_name:
            return self._provider_name_from_module(module_name)
        return provider.__class__.__name__.lower()

    def _provider_enabled(self, provider_name: str, default: bool = True) -> bool:
        if provider_name == 'bookies_api':
            explicit = bool(getattr(self.settings, 'bookies_api_enabled', default))
            credentials_ready = bool(
                getattr(self.settings, 'bookies_api_login', None)
                and (
                    getattr(self.settings, 'bookies_api_token', None)
                    or getattr(self.settings, 'bookies_api_key', None)
                )
            )
            if explicit:
                return True
            return credentials_ready
        if provider_name == 'oddspapi':
            return bool(getattr(self.settings, 'enable_oddspapi', default))
        if provider_name == 'allsportsapi':
            return bool(getattr(self.settings, 'enable_allsportsapi', default))
        if provider_name == 'odds_api_io':
            return bool(getattr(self.settings, 'enable_odds_api_io', default))
        if provider_name == 'sstats':
            return bool(getattr(self.settings, 'sstats_enabled', default)) and bool(getattr(self.settings, 'enable_sstats_context', default))
        if provider_name == 'api_football':
            return bool(getattr(self.settings, 'api_football_enabled', default))
        if provider_name == 'football_data':
            return bool(getattr(self.settings, 'enable_football_data_context', default))
        if provider_name == 'newsapi':
            return bool(getattr(self.settings, 'enable_newsapi_context', default))
        if provider_name == 'gnews':
            return bool(getattr(self.settings, 'enable_gnews_context', default))
        return bool(default)

    def _is_report_only_mode(self, now_utc: datetime) -> bool:
        local_now = now_utc.astimezone(self.settings.tzinfo)
        report_hour_local = min(23, max(0, int(getattr(self.settings, 'daily_report_hour_local', 22) or 22)))
        return bool(getattr(self.settings, 'nightly_review_report_only_enabled', True)) and bool(
            getattr(self.settings, 'daily_report_enabled', True)
        ) and (
            not bool(getattr(self.settings, 'prediction_publication_enabled', True))
            or int(local_now.hour) >= report_hour_local
        )

    def _safe_provider(self, module_name: str, class_name: str) -> Any | None:
        provider_name = self._provider_name_from_module(module_name)
        if module_name.endswith('bookies_bootstrap') and not getattr(self.settings, 'bookies_bootstrap_enabled', True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('odds_api_io') and not self._provider_enabled('odds_api_io', default=True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('bookies_api') and not self._provider_enabled('bookies_api', default=False):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('oddspapi') and not self._provider_enabled('oddspapi', default=False):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('allsportsapi') and not self._provider_enabled('allsportsapi', default=False):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('futrixmetrics') and not getattr(self.settings, 'enable_futrixmetrics_context', False):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if (module_name.endswith('bzzoiro') or module_name.endswith('bzzoiro_v2')) and not getattr(self.settings, 'enable_bzzoiro_context', True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('sstats') and not self._provider_enabled('sstats', default=True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('api_football') and not self._provider_enabled('api_football', default=True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('espn') and not getattr(self.settings, 'enable_espn_context', True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('thesportsdb') and not getattr(self.settings, 'enable_thesportsdb_context', True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('football_data') and not self._provider_enabled('football_data', default=True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('newsapi') and not self._provider_enabled('newsapi', default=True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('gnews') and not self._provider_enabled('gnews', default=True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        try:
            module = import_module(module_name)
            cls = getattr(module, class_name)
            instance = cls(self.settings)
            self._mark_provider_status(provider_name, enabled=True, loaded=True, class_name=class_name)
            return instance
        except Exception as exc:
            self._mark_provider_status(
                provider_name,
                enabled=True,
                loaded=False,
                class_name=class_name,
                error=self._format_exception(exc),
            )
            return None

    async def run_once(self) -> dict[str, Any]:
        started_at = datetime.now(UTC).isoformat()
        logger.info('Starting prediction run at %s', started_at)
        self.state.save_run('running', summary={'started_at': started_at})
        try:
            now_utc = datetime.now(UTC)
            now_local = now_utc.astimezone(self.settings.tzinfo)
            recent_learning_state = self.state.learning_state_snapshot()
            report_only_mode = self._is_report_only_mode(now_utc)

            settlement_probe = await self.settlement.settle_pending_bets(self.state.pending_bets(include_shadow=True), now_utc)
            settlement_attempts_recorded = self.state.record_settlement_attempts(settlement_probe)
            settlement_summary = self.state.apply_settlements(list(settlement_probe.get('items') or []), self.settings)
            bankroll_summary = self.state.bankroll_summary(self.settings)
            quality_clv_rows = self.market_monitor.resolved_clv_rows() if self.market_monitor is not None else []
            quality_report = self.quality.build_quality_report(
                self.state.prediction_ledger(self.settings, include_shadow=True),
                quality_clv_rows,
            )
            quality_report['recent_learning_state'] = recent_learning_state
            quality_export_paths = self.quality.export_quality_report(self.settings.storage_export_dir, quality_report)
            daily_report_due = False
            daily_report_date = ''
            daily_report_skip_reason: str | None = None
            daily_report = None
            daily_report_refresh_reason: str | None = None
            daily_report_messages_sent = 0
            daily_report_payloads: list[str] = []

            bootstrap_matches, bootstrap_meta = await self._fetch_matches()
            bootstrap_matches, bootstrap_meta = self._merge_day_inventory_matches(
                list(bootstrap_matches or []),
                dict(bootstrap_meta or {}),
                now_utc,
            )
            deduped_matches = self._dedupe_matches(bootstrap_matches)
            bootstrap_provider = str(bootstrap_meta.get('provider') or 'none')
            bootstrap_attempts = dict(bootstrap_meta.get('attempts') or {})
            bootstrap_stats = dict(bootstrap_meta.get('stats') or {})
            bootstrap_preview = dict(bootstrap_meta.get('preview') or {})
            filtered_matches, filtering = self._filter_matches(deduped_matches, now_utc)

            (
                (odds_api_io_offers, odds_io_stats, odds_io_preview),
                (bookies_api_offers, bookies_stats, bookies_preview),
                (oddspapi_offers, oddspapi_stats, oddspapi_preview),
                (allsportsapi_offers, allsportsapi_stats, allsportsapi_preview),
                (sportlogic_offers, sportlogic_stats, sportlogic_preview),
                (bzzoiro_offers, bzzoiro_odds_stats, bzzoiro_odds_preview),
            ) = await asyncio.gather(
                self._fetch_provider(
                    self.odds_api_io,
                    'fetch_offers',
                    filtered_matches,
                    empty_data={},
                ),
                self._fetch_provider(
                    self.bookies_api,
                    'fetch_offers',
                    filtered_matches,
                    empty_data={},
                ),
                self._fetch_provider(
                    self.oddspapi,
                    'fetch_offers',
                    filtered_matches,
                    empty_data={},
                ),
                self._fetch_provider(
                    self.allsportsapi,
                    'fetch_offers',
                    filtered_matches,
                    empty_data={},
                ),
                self._fetch_provider(
                    self.sportlogic,
                    'fetch_offers',
                    filtered_matches,
                    empty_data={},
                ),
                self._fetch_provider(
                    self.bzzoiro,
                    'fetch_offers',
                    filtered_matches,
                    empty_data={},
                ),
            )

            offer_maps = {
                'odds_api_io': odds_api_io_offers,
                'bookies_api': bookies_api_offers,
                'oddspapi': oddspapi_offers,
                'allsportsapi': allsportsapi_offers,
                'sportlogic': sportlogic_offers,
                'bzzoiro': bzzoiro_offers,
            }
            merged_offers = self._merge_offers(*offer_maps.values())
            market_signals: dict[str, dict[str, Any]] = {}
            market_monitor_stats: dict[str, Any] = {'enabled': False}
            market_monitor_preview: dict[str, Any] = {}
            if self.market_monitor is not None:
                market_signals, market_monitor_stats, market_monitor_preview = self.market_monitor.build_signals(filtered_matches, merged_offers, now_utc)

            coverage_planner = CoveragePlanner(self.settings)
            context_target_matches, context_enrichment = coverage_planner.select_context_targets(
                filtered_matches,
                merged_offers,
                now_utc,
                market_signals,
            )
            provider_targets = {
                'sstats': self._select_provider_context_matches(context_target_matches, 'sstats', fallback_matches=filtered_matches, offers_by_match=merged_offers),
                'bzzoiro': self._select_provider_context_matches(context_target_matches, 'bzzoiro', fallback_matches=filtered_matches, offers_by_match=merged_offers),
                'api_football': self._select_provider_context_matches(context_target_matches, 'api_football', fallback_matches=filtered_matches, offers_by_match=merged_offers),
                'espn': self._select_provider_context_matches(context_target_matches, 'espn', fallback_matches=filtered_matches, offers_by_match=merged_offers),
                'thesportsdb': self._select_provider_context_matches(context_target_matches, 'thesportsdb', fallback_matches=filtered_matches, offers_by_match=merged_offers),
                'football_data': self._select_provider_context_matches(context_target_matches, 'football_data', fallback_matches=filtered_matches, offers_by_match=merged_offers),
                'openligadb': self._select_provider_context_matches(context_target_matches, 'openligadb', fallback_matches=filtered_matches, offers_by_match=merged_offers),
                'futrixmetrics': self._select_provider_context_matches(context_target_matches, 'futrixmetrics', fallback_matches=filtered_matches, offers_by_match=merged_offers),
                'openfootball': self._select_provider_context_matches(context_target_matches, 'openfootball', fallback_matches=filtered_matches, offers_by_match=merged_offers),
                'newsapi': self._select_provider_context_matches(context_target_matches, 'newsapi', fallback_matches=filtered_matches, offers_by_match=merged_offers),
                'gnews': self._select_provider_context_matches(context_target_matches, 'gnews', fallback_matches=filtered_matches, offers_by_match=merged_offers),
                'sportlogic': self._select_provider_context_matches(context_target_matches, 'sportlogic', fallback_matches=filtered_matches, offers_by_match=merged_offers),
            }
            provider_targets = {
                name: coverage_planner.provider_targets(name, items, merged_offers)
                for name, items in provider_targets.items()
            }
            provider_target_counts = {name: len(items) for name, items in provider_targets.items()}
            provider_target_counts['weather'] = min(
                len(context_target_matches),
                max(0, int(getattr(self.settings, 'weather_context_match_limit', 0) or 0)),
            )

            (
                (sstats_contexts, sstats_stats, sstats_preview),
                (bzzoiro_contexts, bzzoiro_stats, bzzoiro_preview),
                (api_football_contexts, api_football_stats, api_football_preview),
                (espn_contexts, espn_stats, espn_preview),
                (thesportsdb_contexts, thesportsdb_stats, thesportsdb_preview),
                (football_data_contexts, football_data_stats, football_data_preview),
                (openligadb_contexts, openligadb_stats, openligadb_preview),
                (futrixmetrics_contexts, futrixmetrics_stats, futrixmetrics_preview),
                (openfootball_contexts, openfootball_stats, openfootball_preview),
                (newsapi_contexts, newsapi_stats, newsapi_preview),
                (gnews_contexts, gnews_stats, gnews_preview),
                (sportlogic_contexts, sportlogic_context_stats, sportlogic_context_preview),
            ) = await asyncio.gather(
                self._fetch_provider(self.sstats, 'fetch_context', provider_targets['sstats'], empty_data={}),
                self._fetch_provider(self.bzzoiro, 'fetch_context', provider_targets['bzzoiro'], empty_data={}),
                self._fetch_provider(self.api_football, 'fetch_context', provider_targets['api_football'], empty_data={}),
                self._fetch_provider(self.espn, 'fetch_context', provider_targets['espn'], empty_data={}),
                self._fetch_provider(self.thesportsdb, 'fetch_context', provider_targets['thesportsdb'], empty_data={}),
                self._fetch_provider(self.football_data, 'fetch_context', provider_targets['football_data'], empty_data={}),
                self._fetch_provider(self.openligadb, 'fetch_context', provider_targets['openligadb'], empty_data={}),
                self._fetch_provider(self.futrixmetrics, 'fetch_context', provider_targets['futrixmetrics'], empty_data={}),
                self._fetch_provider(self.openfootball, 'fetch_context', provider_targets['openfootball'], empty_data={}),
                self._fetch_provider(self.newsapi, 'fetch_context', provider_targets['newsapi'], empty_data={}),
                self._fetch_provider(self.gnews, 'fetch_context', provider_targets['gnews'], empty_data={}),
                self._fetch_provider(self.sportlogic, 'fetch_context', provider_targets['sportlogic'], empty_data={}),
            )

            context_maps = {
                'futrixmetrics': futrixmetrics_contexts,
                'sstats': sstats_contexts,
                'bzzoiro': bzzoiro_contexts,
                'api_football': api_football_contexts,
                'espn': espn_contexts,
                'thesportsdb': thesportsdb_contexts,
                'football_data': football_data_contexts,
                'openligadb': openligadb_contexts,
                'openfootball': openfootball_contexts,
                'newsapi': newsapi_contexts,
                'gnews': gnews_contexts,
                'sportlogic': sportlogic_contexts,
            }
            self_history_contexts, self_history_stats, self_history_preview = self._build_self_history_contexts(filtered_matches, now_utc)
            context_maps['self_history'] = self_history_contexts
            contexts = self._merge_context_maps(*context_maps.values())
            weather_contexts, weather_stats, weather_preview = await self._fetch_weather_contexts(
                context_target_matches,
                contexts,
            )
            context_maps['weather'] = weather_contexts
            if weather_contexts:
                contexts.update(weather_contexts)

            context_bundles = build_context_bundles(context_maps, contexts, now_utc)
            context_input = context_bundles if bool(getattr(self.settings, 'context_bundle_model_input_enabled', True)) else contexts
            match_serving = build_match_serving(filtered_matches, merged_offers, context_bundles, market_signals, now_utc)
            line_snapshots = build_line_snapshots(merged_offers, now_utc)
            consensus_lines = build_consensus_lines(merged_offers, market_signals)

            raw_candidates, rejections, model_debug = self.factory.build_candidates(filtered_matches, merged_offers, context_input, market_signals)
            candidates_before_quality = list(raw_candidates)
            raw_candidates, quality_rejections, quality_debug = self.quality.apply_to_candidates(raw_candidates, quality_report, now_utc)
            for reason, count in quality_rejections.items():
                rejections[f'quality_{reason}'] = rejections.get(f'quality_{reason}', 0) + count

            seen_fingerprints = self._load_seen_candidate_fingerprints()
            self._seen_published_fingerprints = set(seen_fingerprints)
            candidates: list[CandidateBet] = []
            reused_candidates: list[CandidateBet] = []
            reused_already_in_state = 0
            for candidate in raw_candidates:
                candidate_keys = candidate_dedupe_keys(candidate)
                if candidate_keys and candidate_keys.intersection(seen_fingerprints):
                    reused_already_in_state += 1
                    candidate.already_used = True
                    candidate.reasons.append('blocked=already_telegram_sent_semantic_dedupe')
                    candidate.source_summary['publication_blocked_reason'] = 'already_telegram_sent_semantic_dedupe'
                    candidate.source_summary['publication_dedupe_keys_matched'] = sorted(candidate_keys.intersection(seen_fingerprints))[:6]
                    reused_candidates.append(candidate)
                    continue
                candidates.append(candidate)
            # Do not resend an already Telegram-confirmed pick just because the current run
            # has no fresh alternatives.  The previous behavior republished the same candidate
            # from ``reused_candidates`` when the fresh pool was empty, which caused duplicate
            # Telegram posts during frequent external/manual runs.  Keep an explicit escape hatch
            # for local diagnostics, but default and workflow settings keep it disabled.
            if not candidates and reused_candidates:
                republish_seen_enabled = bool(
                    getattr(self.settings, 'republish_seen_candidates_when_empty', False)
                )
                if republish_seen_enabled:
                    republish_limit = max(1, int(getattr(self.settings, 'republish_seen_candidates_limit', 1) or 1))
                    reused_candidates.sort(
                        key=lambda item: (
                            float(getattr(item, 'publication_score', 0.0) or 0.0),
                            float(getattr(item, 'ev_pct', 0.0) or 0.0),
                            float(getattr(item, 'edge_pct', 0.0) or 0.0),
                        ),
                        reverse=True,
                    )
                    republished_candidates = reused_candidates[:republish_limit]
                    for candidate in republished_candidates:
                        candidate.reasons.append('republish=seen_candidate_pool_empty_explicit_override')
                        candidate.source_summary['republish_reason'] = 'seen_candidate_pool_empty_explicit_override'
                    candidates = republished_candidates
                else:
                    for candidate in reused_candidates:
                        candidate.reasons.append('blocked=already_telegram_sent')
                        candidate.source_summary['publication_blocked_reason'] = 'already_telegram_sent'

            candidates = self.state.annotate_candidates_with_stakes(candidates, self.settings)
            zero_stake_candidates = [
                candidate for candidate in candidates
                if float(getattr(candidate, 'stake_amount', 0.0) or 0.0) <= 0.0
            ]
            decision_now_utc = datetime.now(UTC)
            decision_now_local = decision_now_utc.astimezone(self.settings.tzinfo)
            report_only_mode = self._is_report_only_mode(decision_now_utc)
            daily_report_due, daily_report_date, daily_report_skip_reason = self.state.daily_report_due(self.settings, decision_now_utc)
            daily_report = self.state.build_daily_report(self.settings, daily_report_date) if daily_report_due else None
            if (
                daily_report is None
                and daily_report_skip_reason == 'already_sent'
                and bool(getattr(self.settings, 'daily_report_resend_on_change', True))
            ):
                candidate_report = self.state.build_daily_report(self.settings, daily_report_date)
                refresh_due, refresh_reason = self.state.daily_report_refresh_due(daily_report_date, candidate_report)
                daily_report_refresh_reason = refresh_reason
                if refresh_due:
                    daily_report_due = True
                    daily_report_skip_reason = 'refresh_after_settlement'
                    daily_report = candidate_report
                    daily_report['is_revision'] = True
                    daily_report['refresh_reason'] = refresh_reason
            if daily_report is not None:
                daily_report['quality_analysis'] = self.quality.analyze_daily_report(daily_report)
                daily_report['next_day_adjustments'] = self.quality.build_next_day_adjustments(daily_report)
                daily_total_bets = int((daily_report.get('summary') or {}).get('total_bets') or 0)
                daily_min_bets = max(0, int(getattr(self.settings, 'daily_report_min_bets', 1) or 1))
                if daily_total_bets >= daily_min_bets:
                    daily_report_messages_sent, daily_report_payloads = await self.telegram.publish_daily_report(daily_report)
                    self.state.mark_daily_report_sent(
                        daily_report_date,
                        daily_report,
                        telegram_sent=daily_report_messages_sent > 0,
                    )
                else:
                    daily_report_skip_reason = 'not_enough_bets'
                    self.state.mark_daily_report_sent(
                        daily_report_date,
                        daily_report,
                        skipped_reason=daily_report_skip_reason,
                    )
            publishable_pool = self._filter_publishable_candidates(candidates)
            publishable_candidates = self._select_publishable_candidates(publishable_pool)
            prediction_publication_enabled = bool(getattr(self.settings, 'prediction_publication_enabled', True)) and not report_only_mode
            if not prediction_publication_enabled:
                publishable_candidates = []
            shadow_candidates = self._select_shadow_candidates(
                candidates_before_quality=candidates_before_quality,
                passed_candidates=candidates,
                publishable_candidates=publishable_candidates,
                reused_candidates=reused_candidates,
                zero_stake_candidates=zero_stake_candidates,
                quality_decisions=quality_debug.get('decisions', []),
            )
            bankroll_preview = self._project_bankroll_summary(publishable_candidates)
            settlement_messages_sent, settlement_payloads = await self.telegram.publish_settlement_summary(settlement_summary)
            prediction_messages_sent, telegram_payloads = await self.telegram.publish(publishable_candidates, bankroll_summary=bankroll_preview)
            prediction_telegram_sent = prediction_messages_sent > 0
            for candidate in publishable_candidates:
                mark_candidate_lifecycle(
                    candidate,
                    telegram_sent=prediction_telegram_sent,
                    failure_reason=None if prediction_telegram_sent else 'telegram_send_not_confirmed',
                )
            if prediction_telegram_sent and publishable_candidates:
                try:
                    append_sent_candidate_index(Path('.data') / 'published-candidate-index.json', publishable_candidates)
                    append_sent_candidate_index(Path('.data') / 'fallback-sent-index.json', publishable_candidates)
                except Exception as exc:
                    self.provider_runtime_errors['publication_dedupe_index'].append(self._format_exception(exc))
            published_count = self.state.store_candidates(publishable_candidates, telegram_sent=prediction_telegram_sent)
            shadow_tracked_count = self.state.store_shadow_candidates(shadow_candidates, tracking_reason='shadow_learning')
            telegram_picks_sent = len(publishable_candidates) if prediction_telegram_sent else 0
            sent_messages = prediction_messages_sent + settlement_messages_sent + daily_report_messages_sent
            telegram_payloads = list(settlement_payloads) + list(daily_report_payloads) + list(telegram_payloads)
            clv_record_stats = self.market_monitor.record_published_candidates(publishable_candidates, decision_now_utc) if self.market_monitor is not None else {'tracked': 0}
            derived_market_candidates_before_quality = sum(
                1
                for item in candidates_before_quality
                if bool((getattr(item, 'source_summary', {}) or {}).get('market_signal_derived'))
            )
            derived_market_publishable = sum(
                1
                for item in publishable_candidates
                if bool((getattr(item, 'source_summary', {}) or {}).get('market_signal_derived'))
            )
            forecast_rows = self._forecast_rows_for_export(
                model_debug,
                candidates=candidates,
                publishable_candidates=publishable_candidates,
                zero_stake_candidates=zero_stake_candidates,
                reused_candidates=reused_candidates,
                quality_decisions=quality_debug.get('decisions', []),
            )
            rescue_export_paths: dict[str, str] = {}
            try:
                rescue_export_paths = self._export_rescue_candidates(
                    candidates_before_quality=candidates_before_quality,
                    passed_candidates=candidates,
                    publishable_candidates=publishable_candidates,
                    zero_stake_candidates=zero_stake_candidates,
                    reused_candidates=reused_candidates,
                    quality_decisions=quality_debug.get('decisions', []),
                    reference_run_utc=decision_now_utc,
                )
            except Exception as exc:
                self.provider_runtime_errors['rescue_candidate_export'].append(self._format_exception(exc))
            export_paths = self.state.export_payloads(
                self.settings.storage_export_dir,
                filtered_matches,
                publishable_candidates,
                forecast_rows=forecast_rows,
                match_serving_rows=[serialize_dataclass(item) for item in match_serving.values()],
                context_observation_rows=[
                    serialize_dataclass(observation)
                    for bundle in context_bundles.values()
                    for observation in bundle.contexts
                ],
                line_snapshot_rows=[serialize_dataclass(item) for item in line_snapshots],
                consensus_line_rows=[serialize_dataclass(item) for item in consensus_lines],
                settings=self.settings,
            )
            export_paths.update(rescue_export_paths)
            daily_report_export_paths = self.state.export_daily_report(self.settings.storage_export_dir, daily_report) if daily_report is not None else {}
            export_paths.update(daily_report_export_paths)
            export_paths.update(quality_export_paths)
            bet_ledger_rows = self.state.prediction_ledger(self.settings)
            bankroll_summary = self.state.bankroll_summary(self.settings)

            source_stats = {
                'match_bootstrap': {
                    'provider': bootstrap_provider,
                    'stats': bootstrap_stats,
                    'attempts': {name: (payload.get('stats') or {}) for name, payload in bootstrap_attempts.items()},
                },
                'bookies_bootstrap': (bootstrap_attempts.get('bookies_bootstrap', {}).get('stats') or {'enabled': False}),
                'odds_api_io': odds_io_stats,
                'odds_api_io_bootstrap': (bootstrap_attempts.get('odds_api_io', {}).get('stats') or {'enabled': False}),
                'bookies_api': bookies_stats,
                'oddspapi': oddspapi_stats,
                'allsportsapi': allsportsapi_stats,
                'sportlogic': sportlogic_stats,
                'bzzoiro_odds': bzzoiro_odds_stats,
                'sportlogic_context': sportlogic_context_stats,
                'futrixmetrics': futrixmetrics_stats,
                'sstats': sstats_stats,
                'bzzoiro': bzzoiro_stats,
                'api_football': api_football_stats,
                'espn': espn_stats,
                'thesportsdb': thesportsdb_stats,
                'football_data': football_data_stats,
                'openligadb': openligadb_stats,
                'openfootball': openfootball_stats,
                'newsapi': newsapi_stats,
                'gnews': gnews_stats,
                'weather': weather_stats,
                'self_history': self_history_stats,
                'market_monitor': market_monitor_stats,
            }
            mode_counts: dict[str, int] = defaultdict(int)
            for candidate in publishable_candidates:
                mode_counts[str(candidate.model_mode)] += 1

            provider_diagnostics = self._build_provider_diagnostics(
                filtered_matches=filtered_matches,
                offer_maps=offer_maps,
                context_maps=context_maps,
                merged_contexts=contexts,
                raw_candidates=raw_candidates,
                published_candidates=publishable_candidates,
                source_stats=source_stats,
            )

            summary = {
                'current_time_utc': decision_now_utc.isoformat(),
                'current_time_local': decision_now_local.isoformat(),
                'started_time_utc': now_utc.isoformat(),
                'started_time_local': now_local.isoformat(),
                'app_timezone': self.settings.app_timezone,
                'schedule_mode': 'nightly_review' if report_only_mode else 'forecast',
                'matches_seen': len(filtered_matches),
                'matches_before_publish_window': len(deduped_matches),
                'matches_with_offers': sum(1 for match in filtered_matches if merged_offers.get(match.match_key)),
                'context_matches_requested': len(context_target_matches),
                'context_enrichment': context_enrichment,
                'provider_context_targets': provider_target_counts,
                'contexts_built': len(contexts),
                'context_observations_built': sum(len(bundle.contexts) for bundle in context_bundles.values()),
                'matches_with_2plus_context_sources': sum(1 for item in match_serving.values() if item.context_source_count >= 2),
                'line_snapshots_built': len(line_snapshots),
                'consensus_lines_built': len(consensus_lines),
                'matches_with_2plus_line_sources': sum(1 for item in match_serving.values() if item.line_source_count >= 2),
                'matches_ready_2plus_context_and_lines': sum(
                    1
                    for item in match_serving.values()
                    if item.context_source_count >= 2 and item.line_source_count >= 2
                ),
                'self_history_contexts_built': len(self_history_contexts),
                'candidates': len(candidates),
                'candidates_publishable': len(publishable_candidates),
                'candidates_zero_stake': len(zero_stake_candidates),
                'candidates_raw': len(raw_candidates),
                'candidates_before_quality': len(candidates_before_quality),
                'candidates_rejected_by_quality': max(0, len(candidates_before_quality) - len(raw_candidates)),
                'shadow_candidates_tracked': shadow_tracked_count,
                'candidates_before_quality_with_derived_market_signal': derived_market_candidates_before_quality,
                'publishable_with_derived_market_signal': derived_market_publishable,
                'skipped_already_in_state': reused_already_in_state,
                'reused_already_in_state': reused_already_in_state,
                'published': telegram_picks_sent,
                'published_to_telegram': telegram_picks_sent,
                'prediction_publication_enabled': prediction_publication_enabled,
                'telegram_messages_sent': sent_messages,
                'published_unique_state': published_count,
                'dry_run': self.settings.publish_dry_run,
                'state_path': self.settings.state_path,
                'debug_path': self.settings.debug_path,
                'run_logs_dir': str(resolve_run_logs_dir(self.settings)),
                'storage_export_dir': self.settings.storage_export_dir,
                'filtering': filtering,
                'bootstrap_provider': bootstrap_provider,
                'source_stats': source_stats,
                'mapping': {
                    'matched_exact': odds_io_stats.get('matched_exact', 0) + bookies_stats.get('matched_exact', 0),
                    'matched_loose': odds_io_stats.get('matched_loose', 0) + bookies_stats.get('matched_loose', 0),
                    'matched_fuzzy': odds_io_stats.get('matched_fuzzy', 0) + bookies_stats.get('matched_fuzzy', 0),
                    'unmatched_offer_events': odds_io_stats.get('unmatched_offer_events', 0) + bookies_stats.get('unmatched_offer_events', 0),
                    'sstats_exact': sstats_stats.get('matched_exact', 0),
                    'sstats_loose': sstats_stats.get('matched_loose', 0),
                    'sstats_fuzzy': sstats_stats.get('matched_fuzzy', 0),
                    'sstats_unmatched_rows': sstats_stats.get('unmatched_rows', 0),
                    'bzzoiro_exact': bzzoiro_stats.get('matched_exact', 0),
                    'bzzoiro_loose': bzzoiro_stats.get('matched_loose', 0),
                    'bzzoiro_fuzzy': bzzoiro_stats.get('matched_fuzzy', 0),
                    'bzzoiro_contexts': bzzoiro_stats.get('contexts_built', 0),
                    'api_football_exact': api_football_stats.get('matched_exact', 0),
                    'api_football_loose': api_football_stats.get('matched_loose', 0),
                    'api_football_fuzzy': api_football_stats.get('matched_fuzzy', 0),
                    'espn_exact': espn_stats.get('matched_exact', 0),
                    'espn_loose': espn_stats.get('matched_loose', 0),
                    'espn_fuzzy': espn_stats.get('matched_fuzzy', 0),
                    'thesportsdb_contexts': thesportsdb_stats.get('contexts_built', 0),
                    'openligadb_contexts': openligadb_stats.get('contexts_built', 0),
                },
                'rejections': rejections,
                'candidate_modes': dict(mode_counts),
                'provider_diagnostics': provider_diagnostics['summary'],
                'market_monitor': {
                    **market_monitor_stats,
                    'clv_tracked_now': clv_record_stats.get('tracked', 0),
                },
                'settlement': {
                    'checked': settlement_probe.get('checked', 0),
                    'settled_count': settlement_summary.get('settled_count', 0),
                    'shadow_settled_count': settlement_summary.get('shadow_settled_count', 0),
                    'rows_fetched': settlement_probe.get('rows_fetched', 0),
                    'rows_by_source': settlement_probe.get('rows_by_source', {}),
                    'manual_overrides_loaded': settlement_probe.get('manual_overrides_loaded', 0),
                    'manual_overrides_valid': settlement_probe.get('manual_overrides_valid', 0),
                    'manual_overrides_disabled': settlement_probe.get('manual_overrides_disabled', 0),
                    'manual_overrides_invalid': settlement_probe.get('manual_overrides_invalid', 0),
                    'manual_overrides_matched': settlement_probe.get('manual_overrides_matched', 0),
                    'reasons': settlement_probe.get('reasons', {}),
                    'unsettled_count': settlement_probe.get('unsettled_count', 0),
                    'attempts_recorded': settlement_attempts_recorded,
                    'sample': settlement_probe.get('sample', []),
                },
                'daily_report': {
                    'due': daily_report_due,
                    'report_date': daily_report_date,
                    'skip_reason': daily_report_skip_reason,
                    'refresh_reason': daily_report_refresh_reason,
                    'is_revision': bool((daily_report or {}).get('is_revision')) if daily_report is not None else False,
                    'telegram_messages_sent': daily_report_messages_sent,
                    'summary': (daily_report or {}).get('summary') if daily_report is not None else {},
                    'quality_analysis': (daily_report or {}).get('quality_analysis') if daily_report is not None else {},
                    'next_day_adjustments': (daily_report or {}).get('next_day_adjustments') if daily_report is not None else {},
                    'exports': daily_report_export_paths,
                },
                'quality': {
                    'summary': quality_report.get('summary', {}),
                    'backtest': quality_report.get('backtest', {}),
                    'error_analysis': quality_report.get('error_analysis', {}),
                    'decisions': {
                        'passed': quality_debug.get('passed', 0),
                        'rejected': quality_debug.get('rejected', 0),
                        'enough_history': quality_debug.get('enough_history', False),
                    },
                    'exports': quality_export_paths,
                },
                'bankroll': bankroll_summary,
                'exports': export_paths,
            }

            run_report_messages_sent = 0
            run_report_payloads: list[str] = []
            if not report_only_mode:
                run_report_messages_sent, run_report_payloads = await self.telegram.publish_run_report(summary)
            summary['run_report'] = {
                'enabled': bool(getattr(self.settings, 'run_report_enabled', True)) and not report_only_mode,
                'only_when_no_predictions': bool(getattr(self.settings, 'run_report_only_when_no_predictions', True)),
                'telegram_messages_sent': run_report_messages_sent,
            }
            sent_messages += run_report_messages_sent
            telegram_payloads = list(telegram_payloads) + list(run_report_payloads)
            summary['telegram_messages_sent'] = sent_messages

            sheet_export_result = self.sheet_export.write(
                publishable_candidates,
                matches=filtered_matches,
                forecast_rows=forecast_rows,
                bet_rows=bet_ledger_rows,
                daily_report=daily_report,
                quality_report=quality_report,
                summary=summary,
            )
            summary['sheet_export'] = sheet_export_result

            run_created_at = datetime.now(UTC).isoformat()
            base_run_payload = {
                'created_at': run_created_at,
                'summary': summary,
                'settings': {
                    'run_sports': self.settings.run_sports,
                    'run_days_ahead': self.settings.run_days_ahead,
                    'target_bookmakers': self.settings.target_bookmakers,
                    'consensus_bookmakers': self.settings.consensus_bookmakers,
                    'publish_dry_run': self.settings.publish_dry_run,
                    'app_timezone': self.settings.app_timezone,
                    'match_bootstrap_provider': self.settings.match_bootstrap_provider,
                    'bootstrap_fallback_to_bookies': self.settings.bootstrap_fallback_to_bookies,
                    'bootstrap_fallback_to_context': bool(getattr(self.settings, 'bootstrap_fallback_to_context', True)),
                    'context_enrichment_match_limit': self.settings.context_enrichment_match_limit,
                    'context_enrichment_requires_offers': self.settings.context_enrichment_requires_offers,
                    'max_picks_per_run': self.settings.max_picks_per_run,
                    'prediction_publication_enabled': bool(getattr(self.settings, 'prediction_publication_enabled', True)),
                    'report_only_mode': report_only_mode,
                },
                'source_previews': {
                    'match_bootstrap': bootstrap_preview,
                    'bookies_bootstrap': (bootstrap_attempts.get('bookies_bootstrap', {}).get('preview') or {}),
                    'odds_api_io_bootstrap': (bootstrap_attempts.get('odds_api_io', {}).get('preview') or {}),
                    'odds_api_io': odds_io_preview,
                    'bookies_api': bookies_preview,
                    'oddspapi': oddspapi_preview,
                    'allsportsapi': allsportsapi_preview,
                    'sportlogic': sportlogic_preview,
                    'sportlogic_context': sportlogic_context_preview,
                    'futrixmetrics': futrixmetrics_preview,
                    'sstats': sstats_preview,
                    'bzzoiro': bzzoiro_preview,
                    'api_football': api_football_preview,
                    'espn': espn_preview,
                    'thesportsdb': thesportsdb_preview,
                    'football_data': football_data_preview,
                    'openligadb': openligadb_preview,
                    'openfootball': openfootball_preview,
                    'newsapi': newsapi_preview,
                    'gnews': gnews_preview,
                    'weather': weather_preview,
                    'self_history': self_history_preview,
                    'market_monitor': market_monitor_preview,
                },
                'provider_diagnostics': provider_diagnostics if self.settings.enable_provider_diagnostics else {'enabled': False},
                'model_debug': model_debug,
                'quality_report': quality_report,
                'quality_debug': quality_debug,
                'telegram_messages': telegram_payloads,
                'settlement': {
                    'probe': settlement_probe,
                    'summary': settlement_summary,
                },
                'daily_report': daily_report or {},
                'bankroll': bankroll_summary,
                'sheet_export': sheet_export_result,
                'exports': export_paths,
            }
            self.state.write_debug(
                {
                    **base_run_payload,
                    'sample_matches': [self._serialize_match(item) for item in filtered_matches[:25]],
                    'sample_offers': self._serialize_offers(merged_offers, limit=25),
                    'sample_contexts': [self._serialize_context(item) for item in list(contexts.values())[:25]],
                    'forecast_rows': forecast_rows[:200],
                    'candidates': [self._serialize_candidate(item) for item in publishable_candidates[:25]],
                    'candidates_before_quality': [self._serialize_candidate(item) for item in candidates_before_quality[:25]],
                    'candidates_zero_stake': [self._serialize_candidate(item) for item in zero_stake_candidates[:25]],
                    'reused_candidates': [self._serialize_candidate(item) for item in reused_candidates[:25]],
                    'shadow_candidates': [self._serialize_candidate(item) for item in shadow_candidates[:25]],
                    'bet_ledger_sample': bet_ledger_rows[:25],
                }
            )
            history_result = self.state.archive_run_payload(
                {
                    **base_run_payload,
                    'matches': [self._serialize_match(item) for item in filtered_matches],
                    'offers': self._serialize_offers(merged_offers, limit=max(200, len(filtered_matches) * 8)),
                    'contexts': [self._serialize_context(item) for item in list(contexts.values())],
                    'forecast_rows': forecast_rows,
                    'candidates': [self._serialize_candidate(item) for item in publishable_candidates],
                    'candidates_before_quality': [self._serialize_candidate(item) for item in candidates_before_quality],
                    'candidates_zero_stake': [self._serialize_candidate(item) for item in zero_stake_candidates],
                    'reused_candidates': [self._serialize_candidate(item) for item in reused_candidates],
                    'shadow_candidates': [self._serialize_candidate(item) for item in shadow_candidates],
                    'bet_ledger': bet_ledger_rows,
                },
                settings=self.settings,
            )
            summary['history'] = history_result
            self.state.save_run('ok', summary=summary)
            logger.info(
                'Prediction run finished: matches=%s candidates=%s publishable=%s telegram=%s',
                summary.get('matches_seen'),
                summary.get('candidates_raw'),
                summary.get('candidates_publishable'),
                summary.get('published_to_telegram'),
            )
            return summary
        except Exception as exc:
            error_text = f'{type(exc).__name__}: {exc}'
            logger.exception('Prediction run failed: %s', error_text)
            self.state.save_run('error', error_text=error_text)
            error_payload = {'created_at': datetime.now(UTC).isoformat(), 'error': error_text}
            self.state.write_debug(error_payload)
            self.state.archive_run_payload(error_payload, settings=self.settings)
            raise

    async def _fetch_matches(self) -> tuple[list[Match], dict[str, Any]]:
        strategy = str(getattr(self.settings, 'match_bootstrap_provider', 'odds_api_io') or 'odds_api_io').strip().lower()
        allow_fallback = bool(getattr(self.settings, 'bootstrap_fallback_to_bookies', True))
        allow_context_fallback = bool(getattr(self.settings, 'bootstrap_fallback_to_context', True))

        provider_order: list[tuple[str, Any]]
        if strategy == 'bookies_bootstrap':
            provider_order = [('bookies_bootstrap', self.bookies_bootstrap)]
            if allow_fallback:
                provider_order.append(('odds_api_io', self.odds_api_io))
        elif strategy == 'auto':
            provider_order = [('odds_api_io', self.odds_api_io), ('bookies_bootstrap', self.bookies_bootstrap)]
        else:
            provider_order = [('odds_api_io', self.odds_api_io)]
            if allow_fallback:
                provider_order.append(('bookies_bootstrap', self.bookies_bootstrap))
        if allow_context_fallback:
            for provider_name, provider in (
                ('openfootball', self.openfootball),
                ('openligadb', self.openligadb),
            ):
                if any(existing_name == provider_name for existing_name, _ in provider_order):
                    continue
                if provider is None or not hasattr(provider, 'fetch_matches'):
                    continue
                provider_order.append((provider_name, provider))

        attempts: dict[str, dict[str, Any]] = {}
        for provider_name, provider in provider_order:
            matches, stats, preview = await self._fetch_provider(
                provider,
                'fetch_matches',
                empty_data=[],
            )
            attempts[provider_name] = {
                'matches': len(matches or []),
                'stats': stats or {},
                'preview': preview or {},
            }
            if matches:
                return list(matches or []), {
                    'provider': provider_name,
                    'stats': stats or {},
                    'preview': preview or {},
                    'attempts': attempts,
                }

        return [], {
            'provider': 'none',
            'stats': {},
            'preview': {},
            'attempts': attempts,
        }

    @staticmethod
    def _env_bool(name: str, default: bool = False) -> bool:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == '':
            return default
        return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}

    def _day_inventory_paths(self, now_utc: datetime) -> list[Path]:
        local_date = now_utc.astimezone(self.settings.tzinfo).date().isoformat()
        base = Path('.data') / 'day_inventory'
        paths = [
            base / f'{local_date}.json',
            base / 'today.json',
            base / 'current.json',
            Path(self.settings.storage_export_dir) / 'latest-day-inventory.json',
        ]
        unique: list[Path] = []
        seen: set[str] = set()
        for path in paths:
            key = str(path)
            if key in seen:
                continue
            seen.add(key)
            unique.append(path)
        return unique

    def _load_day_inventory_matches(self, now_utc: datetime) -> tuple[list[Match], dict[str, Any]]:
        if not self._env_bool('DAY_INVENTORY_USE_FOR_RUN', True):
            return [], {'enabled': False, 'reason': 'disabled'}

        target_local_date = now_utc.astimezone(self.settings.tzinfo).date().isoformat()
        try:
            limit = max(0, int(float(os.getenv('DAY_INVENTORY_RUN_MATCH_LIMIT', '400') or '400')))
        except Exception:
            limit = 400
        stats: dict[str, Any] = {
            'enabled': True,
            'target_local_date': target_local_date,
            'path': '',
            'rows_seen': 0,
            'loaded_matches': 0,
            'skipped_wrong_date': 0,
            'skipped_invalid': 0,
            'skipped_limit': 0,
        }

        payload: dict[str, Any] = {}
        for path in self._day_inventory_paths(now_utc):
            if not path.exists() or not path.is_file():
                continue
            try:
                candidate = json.loads(path.read_text(encoding='utf-8'))
            except Exception as exc:
                stats.setdefault('read_errors', []).append(f'{path}:{type(exc).__name__}:{exc}')
                continue
            if isinstance(candidate, dict):
                payload = candidate
                stats['path'] = str(path)
                break
        if not payload:
            stats['reason'] = 'inventory_file_missing'
            return [], stats

        rows = payload.get('matches') if isinstance(payload.get('matches'), list) else []
        stats['rows_seen'] = len(rows)
        matches: list[Match] = []
        for row in rows:
            if limit > 0 and len(matches) >= limit:
                stats['skipped_limit'] += 1
                continue
            if not isinstance(row, dict):
                stats['skipped_invalid'] += 1
                continue
            kickoff_raw = row.get('kickoff_utc') or row.get('commence_time') or row.get('start_time') or row.get('kickoff')
            try:
                commence_time = parse_datetime(kickoff_raw)
            except Exception:
                stats['skipped_invalid'] += 1
                continue
            if commence_time.astimezone(self.settings.tzinfo).date().isoformat() != target_local_date:
                stats['skipped_wrong_date'] += 1
                continue
            home = str(row.get('home_team') or '').strip()
            away = str(row.get('away_team') or '').strip()
            league = str(row.get('league_name') or row.get('competition') or '').strip()
            if not home or not away or not league:
                stats['skipped_invalid'] += 1
                continue
            source_ids = row.get('source_ids') if isinstance(row.get('source_ids'), dict) else {}
            metadata = dict(row.get('metadata') or {})
            metadata.update({
                'day_inventory': True,
                'day_inventory_path': stats['path'],
                'day_inventory_coverage': row.get('coverage') or {},
                'day_inventory_refresh': row.get('refresh') or {},
                'day_inventory_source_ids': source_ids,
            })
            source_event_id = (
                str(row.get('source_event_id') or '').strip()
                or str(source_ids.get('odds_api_io') or source_ids.get('football_data') or source_ids.get('thesportsdb') or '').strip()
                or str(metadata.get('odds_api_io_id') or row.get('match_key') or '').strip()
            )
            matches.append(
                Match(
                    source='day_inventory',
                    source_event_id=source_event_id,
                    sport_key=str(row.get('sport_key') or 'soccer'),  # type: ignore[arg-type]
                    league_name=league,
                    home_team=home,
                    away_team=away,
                    commence_time=commence_time,
                    home_team_norm=str(row.get('home_team_norm') or canonicalize_team_name(home)),
                    away_team_norm=str(row.get('away_team_norm') or canonicalize_team_name(away)),
                    league_key=str(row.get('league_key') or canonicalize_league_name(league)),
                    tier=str(row.get('tier') or 'mid'),
                    metadata=metadata,
                )
            )
        stats['loaded_matches'] = len(matches)
        return matches, stats

    def _merge_day_inventory_matches(
        self,
        bootstrap_matches: list[Match],
        bootstrap_meta: dict[str, Any],
        now_utc: datetime,
    ) -> tuple[list[Match], dict[str, Any]]:
        inventory_matches, inventory_stats = self._load_day_inventory_matches(now_utc)
        bootstrap_meta['day_inventory_bridge'] = inventory_stats
        if not inventory_matches:
            return bootstrap_matches, bootstrap_meta

        existing_keys = {match.match_key for match in bootstrap_matches}
        added = [match for match in inventory_matches if match.match_key not in existing_keys]
        bootstrap_meta['day_inventory_bridge'] = {
            **inventory_stats,
            'deduped_added_to_run': len(added),
            'duplicates_with_bootstrap': len(inventory_matches) - len(added),
        }
        if added and str(bootstrap_meta.get('provider') or 'none') == 'none':
            bootstrap_meta['provider'] = 'day_inventory'
        elif added:
            bootstrap_meta['provider'] = f"{bootstrap_meta.get('provider') or 'unknown'}+day_inventory"
        return bootstrap_matches + added, bootstrap_meta

    async def _fetch_weather_contexts(
        self,
        matches: list[Match],
        base_contexts: dict[str, MatchContext],
    ) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            'enabled': bool(getattr(self.settings, 'weather_context_enabled', True)),
            'api_key_present': bool(
                getattr(self.settings, 'weatherapi_key', None)
                or getattr(self.settings, 'openweathermap_api_key', None)
            ),
            'requests': 0,
            'response_errors': 0,
            'contexts_built': 0,
            'matches_considered': 0,
            'matches_with_base_context': 0,
            'weatherapi_requests': 0,
            'openweathermap_requests': 0,
            'weatherapi_enriched': 0,
            'openweathermap_enriched': 0,
            'cache_hits': 0,
            'missing_location': 0,
            'no_weather_payload': 0,
            'max_http_requests_per_run': f"{getattr(self.weather, 'weatherapi_per_run_max', 0)}+{getattr(self.weather, 'openweather_per_run_max', 0)}",
            'budget_exhausted': False,
        }
        preview: dict[str, Any] = {'sample_weather': []}
        contexts: dict[str, MatchContext] = {}
        if not stats['enabled']:
            self._mark_provider_status('weather', enabled=False, loaded=False, reason='disabled_by_config')
            return contexts, stats, preview
        if not stats['api_key_present']:
            self._mark_provider_status('weather', enabled=False, loaded=False, reason='missing_api_key')
            return contexts, stats, preview
        self._mark_provider_status('weather', enabled=True, loaded=True, class_name='WeatherContextEnricher')

        limit = max(0, int(getattr(self.settings, 'weather_context_match_limit', 0) or 0))
        if limit <= 0:
            stats['budget_exhausted'] = True
            self._record_provider_fetch_status('weather', 'fetch_context', stats)
            return contexts, stats, preview

        selected = [match for match in matches if base_contexts.get(match.match_key) is not None]
        stats['matches_with_base_context'] = len(selected)
        selected = selected[:limit]
        stats['matches_considered'] = len(selected)

        async with httpx.AsyncClient(timeout=float(getattr(self.settings, 'weather_timeout_seconds', 8.0) or 8.0)) as client:
            for match in selected:
                base_context = base_contexts.get(match.match_key)
                if base_context is None:
                    continue
                try:
                    updated, weather_stats = await self.weather.enrich_context(client, match, {}, base_context)
                except Exception as exc:
                    stats['response_errors'] += 1
                    stats['last_error'] = self._format_exception(exc)
                    continue
                stats['requests'] += int(weather_stats.get('requests', 0) or 0)
                stats['response_errors'] += int(weather_stats.get('response_errors', 0) or 0)
                stats['weatherapi_requests'] += int(weather_stats.get('weatherapi_requests', 0) or 0)
                stats['openweathermap_requests'] += int(weather_stats.get('openweathermap_requests', 0) or 0)
                if bool(weather_stats.get('cache_hit')):
                    stats['cache_hits'] += 1
                if bool(weather_stats.get('budget_exhausted')):
                    stats['budget_exhausted'] = True
                reason = str(weather_stats.get('reason') or '').strip()
                if reason == 'missing_location':
                    stats['missing_location'] += 1
                elif reason == 'no_weather_payload':
                    stats['no_weather_payload'] += 1
                if not bool(weather_stats.get('enriched')):
                    continue
                provider_name = str(weather_stats.get('provider') or 'unknown').strip().lower()
                if provider_name == 'weatherapi':
                    stats['weatherapi_enriched'] += 1
                elif provider_name == 'openweathermap':
                    stats['openweathermap_enriched'] += 1
                details = dict(getattr(updated, 'details', {}) or {})
                details['merged_sources'] = self._context_source_names(base_context)
                details['weather_context_applied'] = True
                contexts[match.match_key] = MatchContext(
                    source=getattr(base_context, 'source', None) or getattr(updated, 'source', None) or 'weather',
                    payload=dict(getattr(updated, 'payload', {}) or {}),
                    expected_home=getattr(updated, 'expected_home', None),
                    expected_away=getattr(updated, 'expected_away', None),
                    home_win_probability=getattr(updated, 'home_win_probability', None),
                    away_win_probability=getattr(updated, 'away_win_probability', None),
                    home_starting=getattr(updated, 'home_starting', None),
                    away_starting=getattr(updated, 'away_starting', None),
                    confidence=getattr(updated, 'confidence', 58.0),
                    profits=dict(getattr(updated, 'profits', {}) or {}),
                    details=details,
                )
                stats['contexts_built'] += 1
                if len(preview['sample_weather']) < 5:
                    preview['sample_weather'].append(
                        {
                            'match_key': match.match_key,
                            'provider': provider_name,
                            'condition': details.get('weather_condition'),
                            'temp_c': details.get('weather_temp_c'),
                            'wind_kph': details.get('weather_wind_kph'),
                            'precip_mm': details.get('weather_precip_mm'),
                            'factor': details.get('weather_total_factor'),
                        }
                    )
                if stats['budget_exhausted']:
                    break

        self._record_provider_fetch_status('weather', 'fetch_context', stats)
        return contexts, stats, preview

    async def _fetch_provider(self, provider: Any | None, method_name: str, *args: Any, empty_data: Any) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        if provider is None or not hasattr(provider, method_name):
            return empty_data, {'enabled': False}, {}
        provider_name = self._provider_name(provider)
        try:
            result = await getattr(provider, method_name)(*args)
        except Exception as exc:
            error_text = self._format_exception(exc)
            self.provider_runtime_errors[provider_name].append(error_text)
            self._mark_provider_status(provider_name, runtime_error=error_text)
            return empty_data, {'enabled': True, 'runtime_error': error_text}, {'error': error_text}
        if isinstance(result, tuple):
            if len(result) == 3:
                stats = result[1] or {}
                preview = result[2] or {}
                self._record_provider_fetch_status(provider_name, method_name, stats)
                return result[0], stats, preview
            if len(result) == 2:
                stats = result[1] or {}
                self._record_provider_fetch_status(provider_name, method_name, stats)
                return result[0], stats, {}
            if len(result) == 1:
                self._record_provider_fetch_status(provider_name, method_name, {})
                return result[0], {}, {}
        self._record_provider_fetch_status(provider_name, method_name, {})
        return result, {}, {}

    def _record_provider_fetch_status(self, provider_name: str, method_name: str, stats: dict[str, Any]) -> None:
        if not isinstance(stats, dict):
            self._mark_provider_status(provider_name, last_method=method_name)
            return
        payload: dict[str, Any] = {
            'last_method': method_name,
            'last_fetch_enabled': stats.get('enabled'),
            'api_key_present': stats.get('api_key_present'),
            'rate_limited': bool(stats.get('rate_limited')) if 'rate_limited' in stats else self.provider_status.get(provider_name, {}).get('rate_limited'),
            'cooldown_until': stats.get('cooldown_until') or self.provider_status.get(provider_name, {}).get('cooldown_until'),
            'requests': stats.get('requests'),
            'response_errors': stats.get('response_errors'),
            'max_http_requests_per_run': stats.get('max_http_requests_per_run'),
            'budget_exhausted': stats.get('budget_exhausted'),
        }
        for key in (
            'contexts_built',
            'matches_built',
            'fixtures_fetched',
            'datasets_loaded',
            'articles_seen',
            'scoreboard_requests',
            'probability_requests',
            'summary_requests',
            'weatherapi_requests',
            'openweathermap_requests',
            'weatherapi_enriched',
            'openweathermap_enriched',
            'cache_hits',
        ):
            if key in stats:
                payload[key] = stats.get(key)
        self._mark_provider_status(provider_name, **payload)

    def _filter_matches(self, matches: list[Match], now_utc: datetime) -> tuple[list[Match], dict[str, Any]]:
        def apply_filter(min_lead: timedelta) -> tuple[list[Match], int, int, int]:
            local_filtered: list[Match] = []
            local_skipped_started = 0
            local_skipped_too_soon = 0
            local_skipped_outside_window = 0
            for match in matches:
                commence = ensure_utc(match.commence_time)
                if commence <= now_utc:
                    local_skipped_started += 1
                    continue
                if commence - now_utc < min_lead:
                    local_skipped_too_soon += 1
                    continue
                if commence > horizon:
                    local_skipped_outside_window += 1
                    continue
                local_filtered.append(match)
            return local_filtered, local_skipped_started, local_skipped_too_soon, local_skipped_outside_window

        horizon = now_utc + timedelta(hours=self.settings.publish_window_hours)
        configured_min_lead_minutes = max(0, int(self.settings.min_kickoff_lead_minutes or 0))
        adaptive_min_lead_minutes = min(
            configured_min_lead_minutes,
            max(0, int(getattr(self.settings, 'adaptive_min_kickoff_lead_minutes', configured_min_lead_minutes) or 0)),
        )
        manual_late_mode_applied = False
        if bool(getattr(self.settings, 'manual_late_mode_enabled', False)):
            configured_min_lead_minutes = min(
                configured_min_lead_minutes,
                max(0, int(getattr(self.settings, 'manual_late_min_kickoff_lead_minutes', configured_min_lead_minutes) or 0)),
            )
            adaptive_min_lead_minutes = min(
                configured_min_lead_minutes,
                max(0, int(getattr(self.settings, 'manual_late_adaptive_min_kickoff_lead_minutes', adaptive_min_lead_minutes) or 0)),
            )
            manual_late_mode_applied = True
        min_lead = timedelta(minutes=configured_min_lead_minutes)
        filtered, skipped_started, skipped_too_soon, skipped_outside_window = apply_filter(min_lead)

        fallback_applied = False
        emergency_applied = False
        force_relaxed_applied = False
        effective_min_lead_minutes = configured_min_lead_minutes
        if (
            not filtered
            and skipped_too_soon > 0
            and getattr(self.settings, 'adaptive_min_kickoff_lead_enabled', True)
            and adaptive_min_lead_minutes < configured_min_lead_minutes
        ):
            min_lead = timedelta(minutes=adaptive_min_lead_minutes)
            filtered, skipped_started, skipped_too_soon, skipped_outside_window = apply_filter(min_lead)
            fallback_applied = True
            effective_min_lead_minutes = adaptive_min_lead_minutes

        future_matches_in_window = [
            match for match in matches
            if now_utc < ensure_utc(match.commence_time) <= horizon
        ]
        future_lead_minutes = sorted(
            round((ensure_utc(match.commence_time) - now_utc).total_seconds() / 60.0, 1)
            for match in future_matches_in_window
        )
        too_soon_share = (
            (skipped_too_soon / len(future_matches_in_window))
            if future_matches_in_window
            else 0.0
        )
        emergency_min_lead_minutes = min(
            effective_min_lead_minutes,
            max(0, int(getattr(self.settings, 'emergency_min_kickoff_lead_minutes', effective_min_lead_minutes) or 0)),
        )
        if (
            not filtered
            and future_matches_in_window
            and getattr(self.settings, 'emergency_min_kickoff_lead_enabled', True)
            and too_soon_share >= float(getattr(self.settings, 'emergency_min_kickoff_activation_ratio', 0.85) or 0.85)
            and emergency_min_lead_minutes < effective_min_lead_minutes
        ):
            min_lead = timedelta(minutes=emergency_min_lead_minutes)
            filtered, skipped_started, skipped_too_soon, skipped_outside_window = apply_filter(min_lead)
            emergency_applied = True
            effective_min_lead_minutes = emergency_min_lead_minutes

        force_relaxed_min_lead_minutes = min(
            effective_min_lead_minutes,
            max(0, int(getattr(self.settings, 'force_relaxed_min_kickoff_lead_minutes', 10) or 0)),
        )
        if (
            not filtered
            and future_matches_in_window
            and getattr(self.settings, 'force_relaxed_min_kickoff_lead_enabled', True)
            and skipped_too_soon >= len(future_matches_in_window)
            and force_relaxed_min_lead_minutes < effective_min_lead_minutes
        ):
            min_lead = timedelta(minutes=force_relaxed_min_lead_minutes)
            filtered, skipped_started, skipped_too_soon, skipped_outside_window = apply_filter(min_lead)
            force_relaxed_applied = True
            effective_min_lead_minutes = force_relaxed_min_lead_minutes

        lead_time_snapshot = {}
        if future_lead_minutes:
            lead_time_snapshot = {
                'nearest_minutes': future_lead_minutes[0],
                'farthest_minutes': future_lead_minutes[-1],
                'sample_minutes': future_lead_minutes[:8],
            }

        filtering = {
            'total_before': len(matches),
            'total_after': len(filtered),
            'skipped_started': skipped_started,
            'skipped_too_soon': skipped_too_soon,
            'skipped_outside_window': skipped_outside_window,
            'publish_window_hours': self.settings.publish_window_hours,
            'min_kickoff_lead_minutes': effective_min_lead_minutes,
            'configured_min_kickoff_lead_minutes': max(0, int(self.settings.min_kickoff_lead_minutes or 0)),
            'adaptive_min_kickoff_lead_enabled': getattr(self.settings, 'adaptive_min_kickoff_lead_enabled', True),
            'adaptive_min_kickoff_lead_minutes': adaptive_min_lead_minutes,
            'adaptive_min_kickoff_lead_applied': fallback_applied,
            'emergency_min_kickoff_lead_enabled': getattr(self.settings, 'emergency_min_kickoff_lead_enabled', True),
            'emergency_min_kickoff_lead_minutes': emergency_min_lead_minutes,
            'emergency_min_kickoff_activation_ratio': float(getattr(self.settings, 'emergency_min_kickoff_activation_ratio', 0.85) or 0.85),
            'emergency_min_kickoff_lead_applied': emergency_applied,
            'force_relaxed_min_kickoff_lead_enabled': getattr(self.settings, 'force_relaxed_min_kickoff_lead_enabled', True),
            'force_relaxed_min_kickoff_lead_minutes': force_relaxed_min_lead_minutes,
            'force_relaxed_min_kickoff_lead_applied': force_relaxed_applied,
            'manual_late_mode_enabled': bool(getattr(self.settings, 'manual_late_mode_enabled', False)),
            'manual_late_mode_applied': manual_late_mode_applied,
            'manual_late_min_kickoff_lead_minutes': int(getattr(self.settings, 'manual_late_min_kickoff_lead_minutes', configured_min_lead_minutes) or 0),
            'manual_late_adaptive_min_kickoff_lead_minutes': int(getattr(self.settings, 'manual_late_adaptive_min_kickoff_lead_minutes', adaptive_min_lead_minutes) or 0),
            'future_matches_in_window': {'count': len(future_matches_in_window)},
            'too_soon_matches_in_window': {'count': skipped_too_soon},
            'too_soon_share_of_future_in_window': round(too_soon_share, 4),
            'lead_time_snapshot': lead_time_snapshot,
            'now_utc': now_utc.isoformat(),
            'now_local': now_utc.astimezone(self.settings.tzinfo).isoformat(),
        }
        return filtered, filtering

    def _select_provider_context_matches(
        self,
        matches: list[Match],
        provider_name: str,
        *,
        fallback_matches: list[Match] | None = None,
        offers_by_match: dict[str, list[Offer]] | None = None,
    ) -> list[Match]:
        limit_map = {
            'sstats': 0,
            'bzzoiro': int(getattr(self.settings, 'bzzoiro_context_match_limit', 80) or 80),
            'api_football': int(getattr(self.settings, 'api_football_context_match_limit', 18) or 18),
            'espn': int(getattr(self.settings, 'espn_context_match_limit', 24) or 24),
            'thesportsdb': int(getattr(self.settings, 'thesportsdb_context_match_limit', 80) or 80),
            'football_data': int(getattr(self.settings, 'football_data_context_match_limit', 80) or 80),
            'openligadb': int(getattr(self.settings, 'openligadb_context_match_limit', 24) or 24),
            'futrixmetrics': int(getattr(self.settings, 'futrixmetrics_context_match_limit', 6) or 6),
            'openfootball': int(getattr(self.settings, 'openfootball_context_match_limit', 120) or 120),
            'newsapi': int(getattr(self.settings, 'newsapi_context_match_limit', 12) or 12),
            'gnews': int(getattr(self.settings, 'gnews_context_match_limit', 8) or 8),
            'sportlogic': int(float(os.getenv('SPORTLOGIC_MATCH_LIMIT', '120') or 120)),
        }
        provider_key = str(provider_name or '').strip().lower()
        limit = max(0, int(limit_map.get(provider_key, 0) or 0))

        availability_multiplier = self._provider_availability_multiplier(provider_key)
        if availability_multiplier <= 0.0:
            return []
        if limit <= 0:
            return matches

        staged = bool(getattr(self.settings, 'enable_context_staging', True))
        premium_limit = max(0, int(getattr(self.settings, 'premium_context_shortlist_limit', 18) or 18))
        premium_news_limit = max(0, int(getattr(self.settings, 'premium_news_shortlist_limit', 3) or 3))
        if staged:
            if provider_key in {'api_football'}:
                limit = min(limit, premium_limit)
            elif provider_key in {'newsapi', 'gnews'}:
                limit = min(limit, premium_news_limit)
            elif provider_key in {'espn'}:
                limit = min(limit, max(premium_limit * 2, premium_limit))
            elif provider_key in {'thesportsdb', 'openfootball', 'openligadb'}:
                limit = min(limit, max(premium_limit * 4, premium_limit))
            elif provider_key in {'football_data'}:
                limit = min(limit, max(premium_limit * 3, premium_limit))
            elif provider_key in {'futrixmetrics'}:
                limit = min(limit, max(4, premium_limit // 2))
        if availability_multiplier < 1.0:
            limit = min(limit, max(1, int(round(limit * availability_multiplier))))
        primary_ranked = self._rank_provider_context_matches(matches, provider_key, offers_by_match)
        selected: list[Match] = []
        seen: set[str] = set()
        for match in primary_ranked:
            if match.match_key in seen:
                continue
            selected.append(match)
            seen.add(match.match_key)
            if len(selected) >= limit:
                return selected
        for match in self._rank_provider_context_matches(fallback_matches or [], provider_key, offers_by_match):
            if match.match_key in seen:
                continue
            selected.append(match)
            seen.add(match.match_key)
            if len(selected) >= limit:
                break
        return selected

    def _rank_provider_context_matches(
        self,
        matches: list[Match],
        provider_key: str,
        offers_by_match: dict[str, list[Offer]] | None = None,
    ) -> list[Match]:
        if not matches:
            return []
        offers_by_match = offers_by_match or {}
        requires_offers = bool(getattr(self.settings, 'context_enrichment_requires_offers', True))
        target_bookmakers = {str(name).strip().lower() for name in (self.settings.target_bookmakers or []) if str(name).strip()}
        now_utc = datetime.now(UTC)
        ranked: list[tuple[tuple[float, ...], Match]] = []
        for match in matches:
            if str(getattr(match, 'sport_key', '') or '') != 'soccer':
                continue
            offers = list(offers_by_match.get(match.match_key) or [])
            if requires_offers and not offers:
                continue
            support_score = self._provider_context_support_score(provider_key, match)
            if support_score <= 0.0:
                continue
            unique_books = {str(item.bookmaker or '').strip().lower() for item in offers if str(item.bookmaker or '').strip()}
            supported_books = len(unique_books & target_bookmakers) if target_bookmakers else len(unique_books)
            families = {str(item.family or '').strip().lower() for item in offers if str(item.family or '').strip()}
            rich_offer_bonus = 1.0 if {'h2h', 'totals', 'btts'} & families else 0.0
            league_priority = float(self.settings.league_priority_score(match.league_name))
            low_tier_penalty = -0.75 if self.settings.is_low_tier_league(match.league_name) else 0.0
            kickoff_delta = max((ensure_utc(match.commence_time) - now_utc).total_seconds(), 0.0)
            soon_bucket = 4.0 if kickoff_delta <= 6 * 3600 else 3.0 if kickoff_delta <= 12 * 3600 else 2.0 if kickoff_delta <= 24 * 3600 else 1.0
            rank_key = (
                soon_bucket,
                support_score,
                league_priority,
                float(supported_books),
                rich_offer_bonus,
                float(len(families)),
                float(len(unique_books)),
                float(len(offers)),
                low_tier_penalty,
                -kickoff_delta,
            )
            ranked.append((rank_key, match))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [match for _, match in ranked]

    def _provider_context_support_score(self, provider_key: str, match: Match) -> float:
        provider_key = str(provider_key or '').strip().lower()
        availability_multiplier = self._provider_availability_multiplier(provider_key)
        if availability_multiplier <= 0.0:
            return 0.0
        league_priority = float(self.settings.league_priority_score(match.league_name))
        base_score = 0.0
        if provider_key == 'api_football':
            base_score = 1.0 if not self.settings.is_low_tier_league(match.league_name) else 0.82
        elif provider_key == 'espn':
            checker = getattr(self.espn, 'supports_match', None)
            base_score = 1.02 if callable(checker) and checker(match) else 0.0
        elif provider_key == 'openfootball':
            checker = getattr(self.openfootball, 'supports_match', None)
            base_score = 1.0 if callable(checker) and checker(match) else 0.0
        elif provider_key == 'openligadb':
            checker = getattr(self.openligadb, 'supports_match', None)
            base_score = 0.98 if callable(checker) and checker(match) else 0.0
        elif provider_key == 'football_data':
            checker = getattr(self.football_data, 'supports_match', None)
            base_score = 0.99 if callable(checker) and checker(match) else 0.0
        elif provider_key == 'thesportsdb':
            checker = getattr(self.thesportsdb, 'supports_match', None)
            if callable(checker) and checker(match):
                base_score = 0.9 if league_priority >= 1.0 else 0.7
            else:
                base_score = 0.0
        elif provider_key == 'sstats':
            base_score = 0.92 if league_priority >= 1.0 else 0.74
        elif provider_key == 'bzzoiro':
            base_score = 0.9 if league_priority >= 2.0 else 0.64 if league_priority >= 1.0 else 0.0
        elif provider_key == 'sportlogic':
            checker = getattr(self.sportlogic, 'supports_match', None)
            base_score = 0.86 if callable(checker) and checker(match) else 0.0
        elif provider_key == 'futrixmetrics':
            base_score = 0.84 if league_priority >= 1.0 else 0.58
        elif provider_key == 'newsapi':
            base_score = 0.7 if league_priority >= 2.0 else 0.35 if league_priority >= 1.0 else 0.0
        elif provider_key == 'gnews':
            base_score = 0.66 if league_priority >= 2.0 else 0.32 if league_priority >= 1.0 else 0.0
        else:
            base_score = 0.5
        return base_score * availability_multiplier

    def _select_context_enrichment_matches(
        self,
        matches: list[Match],
        offers_by_match: dict[str, list[Offer]],
        now_utc: datetime,
        market_signals_by_match: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[list[Match], dict[str, Any]]:
        requires_offers = bool(getattr(self.settings, 'context_enrichment_requires_offers', True))
        limit = int(getattr(self.settings, 'context_enrichment_match_limit', 0) or 0)
        target_bookmakers = {str(name).strip().lower() for name in (self.settings.target_bookmakers or []) if str(name).strip()}
        market_signals_by_match = market_signals_by_match or {}
        min_value_hint = float(getattr(self.settings, 'value_hint_min_edge_pct', 1.0) or 1.0)
        near_miss_priority = self._load_near_miss_priority()
        confirmation_counts = self._load_context_confirmation_counts()
        min_confirmations = max(1, int(os.getenv('CONTEXT_TARGET_MIN_CONFIRMATION_SOURCES', '2') or '2'))

        ranked: list[tuple[tuple[float, ...], Match]] = []
        skipped_without_offers = 0
        hints_kept = 0
        confirmation_gap_candidates = 0
        for match in matches:
            offers = list(offers_by_match.get(match.match_key) or [])
            if requires_offers and not offers:
                skipped_without_offers += 1
                continue
            unique_books = {str(item.bookmaker or '').strip().lower() for item in offers if str(item.bookmaker or '').strip()}
            supported_books = len(unique_books & target_bookmakers) if target_bookmakers else len(unique_books)
            families = {str(item.family or '').strip().lower() for item in offers if str(item.family or '').strip()}
            kickoff_delta = max((ensure_utc(match.commence_time) - now_utc).total_seconds(), 0.0)
            league_priority = float(self.settings.league_priority_score(match.league_name))
            rich_offer_bonus = 1.0 if {'h2h', 'totals', 'btts'} & families else 0.0
            signal_pack = market_signals_by_match.get(match.match_key) or {}
            best_hint = 0.0
            steam_hits = 0.0
            for signal in signal_pack.values():
                if not isinstance(signal, dict):
                    continue
                edge = float(signal.get('best_vs_consensus_edge_pct') or 0.0)
                best_hint = max(best_hint, edge)
                if str(signal.get('movement_label') or '') == 'steam':
                    steam_hits += 1.0
            if best_hint >= min_value_hint:
                hints_kept += 1
            value_hint = clamp(best_hint / 2.5, 0.0, 6.0) + min(steam_hits, 2.0) * 0.5
            soon_bucket = 4.0 if kickoff_delta <= 6 * 3600 else 3.0 if kickoff_delta <= 12 * 3600 else 2.0 if kickoff_delta <= 24 * 3600 else 1.0
            odds_backed = 1.0 if offers else 0.0
            queue_priority = float(near_miss_priority.get(match.match_key, 0.0) or 0.0)
            current_confirmations = int(confirmation_counts.get(match.match_key, 0) or 0)
            confirmation_gap = 1.0 if odds_backed and current_confirmations < min_confirmations else 0.0
            if confirmation_gap:
                confirmation_gap_candidates += 1
            rank_key = (
                queue_priority,
                confirmation_gap,
                soon_bucket,
                odds_backed,
                value_hint,
                float(supported_books),
                float(len(unique_books)),
                league_priority,
                rich_offer_bonus,
                float(len(offers)),
                float(len(families)),
                -kickoff_delta,
            )
            ranked.append((rank_key, match))

        ranked.sort(key=lambda item: item[0], reverse=True)
        selected = [match for _, match in ranked]
        trimmed = 0
        if limit > 0 and len(selected) > limit:
            trimmed = len(selected) - limit
            selected = selected[:limit]

        premium_limit = max(0, int(getattr(self.settings, 'premium_context_shortlist_limit', 18) or 18))
        summary = {
            'requires_offers': requires_offers,
            'limit': limit,
            'eligible_matches': len(ranked),
            'selected_matches': len(selected),
            'trimmed_matches': trimmed,
            'skipped_without_offers': skipped_without_offers,
            'premium_shortlist_limit': premium_limit,
            'matches_with_value_hint': hints_kept,
            'near_miss_queue_items': len(near_miss_priority),
            'near_miss_selected': sum(1 for match in selected if match.match_key in near_miss_priority),
            'confirmation_gap_candidates': confirmation_gap_candidates,
            'confirmation_gap_selected': sum(
                1
                for match in selected
                if offers_by_match.get(match.match_key) and int(confirmation_counts.get(match.match_key, 0) or 0) < min_confirmations
            ),
            'min_confirmation_sources': min_confirmations,
        }
        return selected, summary

    def _load_near_miss_priority(self) -> dict[str, float]:
        paths = [
            Path('.data/exports/latest-near-miss-enrichment-queue.json'),
            Path('.data/exports/latest-match-data-near-miss.json'),
            Path('.data/exports/latest-profit-allowed-near-miss.json'),
        ]
        priority: dict[str, float] = {}
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                continue
            rows = payload.get('items') or payload.get('rows') or payload.get('candidates') if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                continue
            for idx, row in enumerate(rows):
                if not isinstance(row, dict):
                    continue
                match_key = str(row.get('match_key') or row.get('key') or '').strip()
                if not match_key:
                    candidate = row.get('candidate') if isinstance(row.get('candidate'), dict) else {}
                    match_key = str(candidate.get('match_key') or '').strip()
                if not match_key:
                    continue
                try:
                    score = float(row.get('priority') or row.get('score') or row.get('ev_pct') or row.get('canonical_ev_pct') or 1.0)
                except Exception:
                    score = 1.0
                priority[match_key] = max(priority.get(match_key, 0.0), score + max(0.0, 0.001 * (len(rows) - idx)))
        return priority

    def _load_context_confirmation_counts(self) -> dict[str, int]:
        paths = [
            Path('.data/exports/latest-context-source-index.json'),
            Path('.data/provider_cache/context-source-index/latest.json'),
        ]
        counts: dict[str, int] = {}
        for path in paths:
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                continue
            by_match = payload.get('by_match') if isinstance(payload, dict) else {}
            if not isinstance(by_match, dict):
                continue
            for key, value in by_match.items():
                match_key = str(key or '').strip()
                if not match_key:
                    continue
                if isinstance(value, list):
                    count = len({str(item).strip().lower() for item in value if str(item).strip()})
                elif isinstance(value, dict):
                    sources = value.get('sources') or value.get('confirmation_sources') or value.get('context_sources') or []
                    if isinstance(sources, list):
                        count = len({str(item).strip().lower() for item in sources if str(item).strip()})
                    else:
                        count = 0
                else:
                    count = 0
                counts[match_key] = max(counts.get(match_key, 0), count)
        return counts

    @staticmethod
    def _dedupe_matches(matches: list[Match]) -> list[Match]:
        unique: dict[str, Match] = {}
        for match in matches:
            unique.setdefault(match.match_key, match)
        return list(unique.values())

    def _load_seen_candidate_fingerprints(self) -> set[str]:
        """Load robust sent-pick dedupe keys from persistent state and committed indices.

        The name is kept for backward compatibility, but the returned set now contains more
        than exact fingerprints: semantic match+market keys are included too. This blocks
        duplicate Telegram sends when exporters use different fingerprint/time formats.
        """
        paths = [Path(self.settings.state_path)]
        export_dir = Path(self.settings.storage_export_dir)
        for extra_path in (
            Path('.data') / 'published-candidate-index.json',
            Path('.data') / 'fallback-sent-index.json',
            Path('.data') / 'candidate-lifecycle-state.json',
            export_dir / 'latest-picks.json',
            export_dir / 'latest-bets.json',
            export_dir / 'latest-pending-bets.json',
        ):
            if extra_path not in paths:
                paths.append(extra_path)

        seen: set[str] = load_sent_candidate_keys(paths)
        # Backward compatibility for older state files that do not have explicit lifecycle fields.
        # JsonStateStore stores actual bets under active-bet statuses, so collect_sent_candidate_keys
        # will still identify them as sent/open rows.
        return seen

    def _project_bankroll_summary(self, candidates: list[CandidateBet]) -> dict[str, Any]:
        summary = self.state.bankroll_summary(self.settings)
        total_new_stakes = round(
            sum(max(0.0, float(getattr(candidate, 'stake_amount', 0.0) or 0.0)) for candidate in candidates),
            2,
        )
        if total_new_stakes <= 0:
            return summary

        projected = dict(summary)
        current_balance = float(projected.get('current_balance') or 0.0)
        open_exposure = float(projected.get('open_exposure') or 0.0) + total_new_stakes
        total_staked = float(projected.get('total_staked') or 0.0) + total_new_stakes
        closed_pnl = float(projected.get('closed_pnl') or 0.0)
        published_with_stake = sum(
            1 for candidate in candidates if float(getattr(candidate, 'stake_amount', 0.0) or 0.0) > 0
        )

        projected['open_exposure'] = round(open_exposure, 2)
        projected['total_staked'] = round(total_staked, 2)
        projected['bets_published'] = int(projected.get('bets_published') or 0) + published_with_stake
        projected['available_balance'] = round(max(0.0, current_balance - open_exposure), 2)
        projected['yield_pct'] = round((closed_pnl / total_staked * 100.0) if total_staked > 0 else 0.0, 2)
        return projected

    def _filter_publishable_candidates(self, candidates: list[CandidateBet]) -> list[CandidateBet]:
        publishable: list[CandidateBet] = []
        for candidate in candidates:
            if getattr(self.settings, 'bankroll_enabled', True) and float(getattr(candidate, 'stake_amount', 0.0) or 0.0) <= 0.0:
                continue
            candidate_keys = candidate_dedupe_keys(candidate)
            seen_keys = getattr(self, '_seen_published_fingerprints', set())
            if candidate_keys and candidate_keys.intersection(seen_keys):
                candidate.reasons.append('publish_blocked=already_telegram_sent_semantic_dedupe')
                candidate.source_summary['publication_blocked_reason'] = 'already_telegram_sent_semantic_dedupe'
                candidate.source_summary['publication_dedupe_keys_matched'] = sorted(candidate_keys.intersection(seen_keys))[:6]
                continue
            coverage_decision = sync_candidate_publish_coverage(candidate, self.settings)
            candidate.diagnostics.setdefault('publish_coverage_contract', coverage_decision.report)
            candidate.source_summary['publish_coverage_contract'] = coverage_decision.report
            if not coverage_decision.passed:
                candidate.source_summary['publish_coverage_reasons'] = list(coverage_decision.reasons)
                candidate.reasons.extend(f'publish_coverage={reason}' for reason in coverage_decision.reasons)
                continue
            tier_decision = classify_publication_tier(candidate, self.settings)
            candidate.diagnostics.setdefault('publication_tier_contract', tier_decision.report)
            candidate.source_summary.update(tier_decision.report)
            if not tier_decision.passed:
                candidate.source_summary['publish_coverage_reasons'] = list(tier_decision.reasons)
                candidate.reasons.extend(f'publication_tier={reason}' for reason in tier_decision.reasons)
                continue
            publishable.append(candidate)
        return publishable

    def _collect_candidate_fingerprints(
        self,
        value: Any,
        seen: set[str],
        cutoff: datetime | None,
    ) -> None:
        if isinstance(value, dict):
            fingerprint = self._candidate_fingerprint(value)
            if fingerprint and self._should_block_fingerprint(value, cutoff):
                seen.add(fingerprint)
            for item in value.values():
                self._collect_candidate_fingerprints(item, seen, cutoff)
            return
        if isinstance(value, list):
            for item in value:
                self._collect_candidate_fingerprints(item, seen, cutoff)

    @staticmethod
    def _should_block_fingerprint(candidate: dict[str, Any], cutoff: datetime | None) -> bool:
        status = str(candidate.get('status') or '').strip().lower()
        if status in {'won', 'lost', 'push', 'void', 'cancelled', 'canceled', 'settled'}:
            return False
        # latest-picks.json can contain generated/exported candidates that never reached Telegram.
        # They are diagnostics, not published picks, and must not block the next run/fallback.
        if not is_sent_pick_row(candidate):
            return False

        event_time_raw = candidate.get('commence_time') or candidate.get('published_at') or candidate.get('created_at')
        if cutoff is None:
            return True
        if not event_time_raw:
            return True
        try:
            event_time = ensure_utc(event_time_raw)
        except Exception:
            return True
        return event_time >= cutoff

    @staticmethod
    def _candidate_fingerprint(candidate: CandidateBet | dict[str, Any]) -> str | None:
        def get(field: str) -> Any:
            if isinstance(candidate, dict):
                return candidate.get(field)
            return getattr(candidate, field, None)

        existing_fingerprint = get('fingerprint')
        match_key = get('match_key')
        family = get('family')
        selection = get('selection')
        point = get('point')
        selection_key = get('selection_key') or candidate_selection_key(
            str(family or ''),
            str(selection or ''),
            point=point,
            team_side=get('team_side'),
            home_team=str(get('home_team') or ''),
            away_team=str(get('away_team') or ''),
        )
        team_side = str(get('team_side') or '').strip().lower()
        commence_time = get('commence_time')
        if match_key and family and selection_key:
            point_text = ''
            if point not in (None, ''):
                try:
                    point_text = f'{float(point):g}'
                except Exception:
                    point_text = str(point)
            commence_text = ''
            if commence_time not in (None, ''):
                try:
                    commence_text = ensure_utc(commence_time).isoformat()
                except Exception:
                    commence_text = str(commence_time)
            return '|'.join(
                (
                    str(match_key),
                    str(family),
                    str(selection_key),
                    team_side,
                    point_text,
                    commence_text,
                )
            )
        if existing_fingerprint:
            return str(existing_fingerprint)
        return None

    @staticmethod
    def _merge_offers(*maps: dict[str, list[Offer]]) -> dict[str, list[Offer]]:
        merged: dict[str, list[Offer]] = defaultdict(list)
        seen: set[tuple[Any, ...]] = set()
        for mapping in maps:
            for match_key, offers in (mapping or {}).items():
                for offer in offers:
                    key = (
                        match_key,
                        offer.source,
                        offer.bookmaker,
                        offer.family,
                        offer.selection,
                        offer.price,
                        offer.point,
                        offer.team_side,
                    )
                    if key in seen:
                        continue
                    seen.add(key)
                    merged[match_key].append(offer)
        return dict(merged)

    def _select_publishable_candidates(self, candidates: list[CandidateBet]) -> list[CandidateBet]:
        if not candidates:
            return []
        limit = max(1, int(getattr(self.settings, 'max_picks_per_run', 2) or 2))
        selected: list[CandidateBet] = []
        used_matches: set[str] = set()
        for candidate in candidates:
            if candidate.match_key in used_matches:
                continue
            selected.append(candidate)
            used_matches.add(candidate.match_key)
            if len(selected) >= limit:
                break
        return selected

    def _select_shadow_candidates(
        self,
        *,
        candidates_before_quality: list[CandidateBet],
        passed_candidates: list[CandidateBet],
        publishable_candidates: list[CandidateBet],
        reused_candidates: list[CandidateBet],
        zero_stake_candidates: list[CandidateBet],
        quality_decisions: list[dict[str, Any]] | None = None,
    ) -> list[CandidateBet]:
        if not bool(getattr(self.settings, 'shadow_tracking_enabled', True)):
            return []
        max_count = max(0, int(getattr(self.settings, 'shadow_tracking_max_per_run', 6) or 6))
        if max_count <= 0:
            return []
        published_fingerprints = {self._candidate_fingerprint(item) for item in publishable_candidates}
        published_fingerprints.discard(None)
        passed_fingerprints = {self._candidate_fingerprint(item) for item in passed_candidates}
        passed_fingerprints.discard(None)
        reused_fingerprints = {self._candidate_fingerprint(item) for item in reused_candidates}
        reused_fingerprints.discard(None)
        zero_stake_fingerprints = {self._candidate_fingerprint(item) for item in zero_stake_candidates}
        zero_stake_fingerprints.discard(None)
        decision_by_key = {
            (
                str(item.get('match_key') or ''),
                str(item.get('family') or ''),
                str(item.get('selection_key') or ''),
                item.get('point'),
                str(item.get('team_side') or ''),
            ): item
            for item in (quality_decisions or [])
            if isinstance(item, dict)
        }
        ranked: list[CandidateBet] = []
        seen_fingerprints: set[str] = set()
        min_publication_score = float(getattr(self.settings, 'shadow_tracking_min_publication_score', 12.0) or 12.0)
        min_ev = float(getattr(self.settings, 'shadow_tracking_min_ev_pct', 0.8) or 0.8)
        min_edge = float(getattr(self.settings, 'shadow_tracking_min_edge_pct', 1.2) or 1.2)
        min_conf = float(getattr(self.settings, 'shadow_tracking_min_confidence', 50.0) or 50.0)

        for candidate in candidates_before_quality:
            fingerprint = self._candidate_fingerprint(candidate)
            if not fingerprint or fingerprint in seen_fingerprints or fingerprint in published_fingerprints or fingerprint in reused_fingerprints:
                continue
            seen_fingerprints.add(fingerprint)
            if float(getattr(candidate, 'publication_score', 0.0) or 0.0) < min_publication_score:
                continue
            if float(getattr(candidate, 'ev_pct', 0.0) or 0.0) < min_ev:
                continue
            if float(getattr(candidate, 'edge_pct', 0.0) or 0.0) < min_edge:
                continue
            if float(getattr(candidate, 'confidence', 0.0) or 0.0) < min_conf:
                continue
            reason = 'publish_limit'
            if fingerprint not in passed_fingerprints:
                if not bool(getattr(self.settings, 'shadow_tracking_store_quality_rejections', True)):
                    continue
                reason = 'quality_rejected'
            elif fingerprint in zero_stake_fingerprints:
                reason = 'bankroll_zero_stake'
            decision = decision_by_key.get(
                (
                    str(candidate.match_key or ''),
                    str(candidate.family or ''),
                    str(candidate.selection_key or ''),
                    candidate.point,
                    str(candidate.team_side or ''),
                )
            )
            candidate.source_summary['shadow_tracking_reason'] = reason
            if isinstance(decision, dict) and decision.get('reasons'):
                candidate.source_summary['shadow_quality_reasons'] = list(decision.get('reasons') or [])
            candidate.reasons.append(f'shadow={reason}')
            ranked.append(candidate)

        ranked.sort(
            key=lambda item: (
                float(getattr(item, 'publication_score', 0.0) or 0.0),
                float(getattr(item, 'ev_pct', 0.0) or 0.0),
                float(getattr(item, 'edge_pct', 0.0) or 0.0),
                float(getattr(item, 'confidence', 0.0) or 0.0),
            ),
            reverse=True,
        )
        return ranked[:max_count]

    def _export_rescue_candidates(
        self,
        *,
        candidates_before_quality: list[CandidateBet],
        passed_candidates: list[CandidateBet],
        publishable_candidates: list[CandidateBet],
        zero_stake_candidates: list[CandidateBet],
        reused_candidates: list[CandidateBet],
        quality_decisions: list[dict[str, Any]] | None,
        reference_run_utc: datetime,
    ) -> dict[str, str]:
        """Export fresh near-publish candidates for the controlled fallback publisher.

        The fallback script intentionally runs after the main pipeline. It needs a
        current candidate pool, including high-value candidates that failed only a
        quality/stake/pacing guard. Keeping this export tied to the current run
        prevents stale watchlists from being reused as real Telegram picks.
        """
        root = Path(self.settings.storage_export_dir)
        root.mkdir(parents=True, exist_ok=True)
        out_path = root / 'latest-rescue-candidates.json'

        published = {self._candidate_fingerprint(item) for item in publishable_candidates}
        published.discard(None)
        passed = {self._candidate_fingerprint(item) for item in passed_candidates}
        passed.discard(None)
        zero_stake = {self._candidate_fingerprint(item) for item in zero_stake_candidates}
        zero_stake.discard(None)
        reused = {self._candidate_fingerprint(item) for item in reused_candidates}
        reused.discard(None)
        quality_by_key = {
            (
                str(item.get('match_key') or ''),
                str(item.get('family') or ''),
                str(item.get('selection_key') or ''),
                item.get('point'),
                str(item.get('team_side') or ''),
            ): item
            for item in (quality_decisions or [])
            if isinstance(item, dict)
        }

        now_utc = reference_run_utc
        min_lead = max(0, int(getattr(self.settings, 'min_kickoff_lead_minutes', 30) or 30))
        horizon = now_utc + timedelta(hours=max(1, int(getattr(self.settings, 'publish_window_hours', 12) or 12)))
        earliest = now_utc + timedelta(minutes=min_lead)
        max_count = max(10, int(getattr(self.settings, 'rescue_candidate_export_limit', 80) or 80))

        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        for candidate in candidates_before_quality:
            fingerprint = self._candidate_fingerprint(candidate)
            if not fingerprint or fingerprint in seen or fingerprint in published:
                continue
            seen.add(fingerprint)
            kickoff = ensure_utc(candidate.commence_time)
            if kickoff < earliest or kickoff > horizon:
                continue
            if float(getattr(candidate, 'ev_pct', 0.0) or 0.0) <= 0.0:
                continue
            if float(getattr(candidate, 'edge_pct', 0.0) or 0.0) <= 0.0:
                continue
            if int(getattr(candidate, 'books_count', 0) or 0) <= 0:
                continue

            key = (
                str(candidate.match_key or ''),
                str(candidate.family or ''),
                str(candidate.selection_key or ''),
                candidate.point,
                str(candidate.team_side or ''),
            )
            quality = dict(quality_by_key.get(key) or {})
            status = 'quality_rejected'
            if fingerprint in passed:
                status = 'passed_not_published'
            if fingerprint in zero_stake:
                status = 'zero_stake'
            if fingerprint in reused:
                status = 'reused_already_in_state'

            row = self._serialize_candidate(candidate)
            row['_candidate_source'] = 'latest_rescue_candidates'
            row['rescue_status'] = status
            row['rescue_exported_at'] = now_utc.isoformat()
            row['quality_status'] = quality.get('status') or candidate.source_summary.get('quality_status') or ''
            row['quality_score'] = quality.get('quality_score') or candidate.source_summary.get('quality_score') or 0.0
            row['quality_reasons'] = list(quality.get('reasons') or candidate.source_summary.get('quality_reasons') or [])
            row['diagnostics'] = {
                **dict(row.get('diagnostics') or {}),
                'quality': {
                    **dict((row.get('diagnostics') or {}).get('quality') or {}),
                    **quality,
                } if quality else dict((row.get('diagnostics') or {}).get('quality') or {}),
                'rescue': {
                    'status': status,
                    'fingerprint': fingerprint,
                    'passed_quality': fingerprint in passed,
                    'zero_stake': fingerprint in zero_stake,
                    'reused_already_in_state': fingerprint in reused,
                },
            }
            rows.append(row)

        rows.sort(
            key=lambda item: (
                float(item.get('publication_score') or 0.0),
                float(item.get('ev_pct') or 0.0),
                float(item.get('edge_pct') or 0.0),
                float(item.get('confidence') or 0.0),
            ),
            reverse=True,
        )
        payload = {
            'created_at_utc': datetime.now(UTC).isoformat(),
            'reference_run_utc': now_utc.isoformat(),
            'freshness_minutes': 90,
            'source': 'PredictionRunner._export_rescue_candidates',
            'counts': {
                'candidates_before_quality': len(candidates_before_quality),
                'passed_candidates': len(passed_candidates),
                'publishable_candidates': len(publishable_candidates),
                'zero_stake_candidates': len(zero_stake_candidates),
                'reused_candidates': len(reused_candidates),
                'exported': min(len(rows), max_count),
            },
            'candidates': rows[:max_count],
        }
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        return {'latest_rescue_candidates': str(out_path)}

    def _build_self_history_contexts(
        self,
        matches: list[Match],
        now_utc: datetime,
    ) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        if not bool(getattr(self.settings, 'self_history_context_enabled', True)):
            return {}, {'enabled': False, 'reason': 'disabled'}, {}

        history_roots = resolve_run_history_roots(self.settings)
        max_runs = max(1, int(getattr(self.settings, 'self_history_context_max_runs', 48) or 48))
        max_age_days = max(1, int(getattr(self.settings, 'self_history_context_max_age_days', 45) or 45))
        min_team_samples = max(1, int(getattr(self.settings, 'self_history_context_min_team_samples', 2) or 2))
        include_state = bool(getattr(self.settings, 'self_history_context_include_state', True))
        state_max_samples = max(1, int(getattr(self.settings, 'self_history_context_state_max_samples', 160) or 160))
        cross_venue_weight = clamp(float(getattr(self.settings, 'self_history_context_cross_venue_weight', 0.74) or 0.74), 0.35, 0.98)
        state_sample_weight = clamp(float(getattr(self.settings, 'self_history_context_state_sample_weight', 0.88) or 0.88), 0.45, 1.05)
        archive_paths = collect_run_archive_paths(history_roots, newest_first=True)

        team_history: dict[str, dict[str, list[dict[str, Any]]]] = defaultdict(lambda: {'home': [], 'away': [], 'all': []})
        archives_scanned = 0
        history_matches_used = 0
        state_rows_scanned = 0
        state_matches_used = 0
        cutoff_utc = now_utc - timedelta(days=max_age_days)

        def append_sample(team_key: str, venue: str, sample: dict[str, Any]) -> None:
            if not team_key or venue not in {'home', 'away'}:
                return
            team_history[team_key][venue].append(sample)
            cross_sample = dict(sample)
            cross_sample['weight'] = float(cross_sample.get('weight') or 1.0) * cross_venue_weight
            cross_sample['venue_origin'] = venue
            cross_sample['cross_venue'] = True
            team_history[team_key]['all'].append(cross_sample)

        for archive_path in archive_paths:
            if archives_scanned >= max_runs:
                break
            try:
                payload = json.loads(archive_path.read_text(encoding='utf-8'))
            except Exception:
                continue
            created_at_raw = str(payload.get('created_at') or '')
            try:
                created_at = ensure_utc(created_at_raw)
            except Exception:
                created_at = None
            if created_at is not None and created_at < cutoff_utc:
                continue
            matches_payload = list((((payload.get('provider_diagnostics') or {}).get('matches')) or []))
            if not matches_payload:
                continue
            archives_scanned += 1
            for row in matches_payload:
                if not isinstance(row, dict):
                    continue
                try:
                    commence_time = ensure_utc(row.get('commence_time'))
                except Exception:
                    continue
                if commence_time >= now_utc or commence_time < cutoff_utc:
                    continue
                best_context = self._best_self_history_archive_context(row.get('contexts'))
                if best_context is None:
                    continue
                expected_home = self._sanitize_self_history_expected(best_context.get('expected_home'))
                expected_away = self._sanitize_self_history_expected(best_context.get('expected_away'))
                home_win_probability = self._sanitize_self_history_probability(best_context.get('home_win_probability'))
                away_win_probability = self._sanitize_self_history_probability(best_context.get('away_win_probability'))
                probability_only_enabled = bool(getattr(self.settings, 'self_history_probability_only_enabled', True))
                if expected_home is None and expected_away is None:
                    if not probability_only_enabled or (home_win_probability is None and away_win_probability is None):
                        continue
                age_days = max(0.0, (now_utc - commence_time).total_seconds() / 86400.0)
                league_name = str(row.get('league_name') or '')
                home_key = self._team_history_key(str(row.get('home_team') or ''))
                away_key = self._team_history_key(str(row.get('away_team') or ''))
                if not home_key or not away_key:
                    continue
                sample_weight = 1.0 / max(1.0, age_days / 14.0 + 1.0)
                confidence_value = self._to_float_safe(best_context.get('confidence')) or 0.0
                append_sample(home_key, 'home', {
                    'league_name': league_name,
                    'age_days': age_days,
                    'weight': sample_weight,
                    'expected_for': expected_home,
                    'expected_against': expected_away,
                    'win_probability': home_win_probability,
                    'confidence': confidence_value,
                    'sample_source': 'archive',
                })
                append_sample(away_key, 'away', {
                    'league_name': league_name,
                    'age_days': age_days,
                    'weight': sample_weight,
                    'expected_for': expected_away,
                    'expected_against': expected_home,
                    'win_probability': away_win_probability,
                    'confidence': confidence_value,
                    'sample_source': 'archive',
                })
                history_matches_used += 1

        if include_state:
            seen_state: set[str] = set()
            collections = []
            try:
                collections = [
                    *(self.state._state.get('bets') or []),
                    *(self.state._state.get('shadow_bets') or []),
                    *(self.state._state.get('published_candidates') or []),
                ]
            except Exception:
                collections = []
            for row in collections:
                if state_rows_scanned >= state_max_samples:
                    break
                if not isinstance(row, dict):
                    continue
                fingerprint = str(row.get('fingerprint') or '')
                if fingerprint and fingerprint in seen_state:
                    continue
                if fingerprint:
                    seen_state.add(fingerprint)
                state_rows_scanned += 1
                try:
                    commence_time = ensure_utc(row.get('commence_time'))
                except Exception:
                    continue
                if commence_time >= now_utc or commence_time < cutoff_utc:
                    continue
                expected_home = self._sanitize_self_history_expected(row.get('expected_home'))
                expected_away = self._sanitize_self_history_expected(row.get('expected_away'))
                home_win_probability = self._sanitize_self_history_probability(row.get('home_win_probability'))
                away_win_probability = self._sanitize_self_history_probability(row.get('away_win_probability'))
                if expected_home is None and expected_away is None and not bool(getattr(self.settings, 'self_history_probability_only_enabled', True)) and home_win_probability is None and away_win_probability is None:
                    continue
                home_key = self._team_history_key(str(row.get('home_team') or ''))
                away_key = self._team_history_key(str(row.get('away_team') or ''))
                if not home_key or not away_key:
                    continue
                age_days = max(0.0, (now_utc - commence_time).total_seconds() / 86400.0)
                base_weight = (1.0 / max(1.0, age_days / 12.0 + 1.0)) * state_sample_weight
                if str(row.get('tracking_mode') or '').lower() == 'shadow':
                    base_weight *= 0.86
                source_summary = dict(row.get('source_summary') or {})
                context_conf = self._to_float_safe(source_summary.get('context_confidence'))
                confidence_value = self._to_float_safe(row.get('confidence')) or context_conf or 0.0
                append_sample(home_key, 'home', {
                    'league_name': str(row.get('league_name') or ''),
                    'age_days': age_days,
                    'weight': base_weight,
                    'expected_for': expected_home,
                    'expected_against': expected_away,
                    'win_probability': home_win_probability,
                    'confidence': confidence_value,
                    'sample_source': 'state',
                    'sample_family': str(row.get('family') or ''),
                })
                append_sample(away_key, 'away', {
                    'league_name': str(row.get('league_name') or ''),
                    'age_days': age_days,
                    'weight': base_weight,
                    'expected_for': expected_away,
                    'expected_against': expected_home,
                    'win_probability': away_win_probability,
                    'confidence': confidence_value,
                    'sample_source': 'state',
                    'sample_family': str(row.get('family') or ''),
                })
                state_matches_used += 1

        contexts: dict[str, MatchContext] = {}
        preview_matches: list[dict[str, Any]] = []
        base_conf = float(getattr(self.settings, 'self_history_context_confidence_base', 50.0) or 50.0)
        step_conf = float(getattr(self.settings, 'self_history_context_confidence_step', 1.8) or 1.8)
        cap_conf = float(getattr(self.settings, 'self_history_context_confidence_cap', 60.0) or 60.0)

        for match in matches:
            home_key = self._team_history_key(match.home_team)
            away_key = self._team_history_key(match.away_team)
            home_pack = dict(team_history.get(home_key) or {})
            away_pack = dict(team_history.get(away_key) or {})
            strict_home_samples = list(home_pack.get('home') or [])
            strict_away_samples = list(away_pack.get('away') or [])
            home_samples = list(strict_home_samples)
            away_samples = list(strict_away_samples)
            if len(home_samples) < min_team_samples:
                home_samples.extend(list(home_pack.get('all') or []))
            if len(away_samples) < min_team_samples:
                away_samples.extend(list(away_pack.get('all') or []))
            home_samples.sort(key=lambda item: (float(item.get('weight') or 0.0), float(item.get('confidence') or 0.0)), reverse=True)
            away_samples.sort(key=lambda item: (float(item.get('weight') or 0.0), float(item.get('confidence') or 0.0)), reverse=True)
            home_samples = home_samples[: max(4, min_team_samples + 3)]
            away_samples = away_samples[: max(4, min_team_samples + 3)]
            if len(home_samples) < 1 or len(away_samples) < 1:
                continue
            if len(home_samples) + len(away_samples) < max(2, min_team_samples * 2 - 1):
                continue
            expected_home = self._combine_self_history_metric(home_samples, away_samples, 'expected_for', 'expected_against', match.league_name)
            expected_away = self._combine_self_history_metric(away_samples, home_samples, 'expected_for', 'expected_against', match.league_name)
            home_win_probability = self._weighted_team_history_metric(home_samples, 'win_probability', match.league_name)
            away_win_probability = self._weighted_team_history_metric(away_samples, 'win_probability', match.league_name)
            if expected_home is None and expected_away is None and home_win_probability is None and away_win_probability is None:
                continue
            sample_total = len(home_samples) + len(away_samples)
            confidence_values = [
                self._to_float_safe(sample.get('confidence'))
                for sample in [*home_samples, *away_samples]
            ]
            confidence_values = [value for value in confidence_values if value is not None]
            avg_sample_confidence = sum(confidence_values) / len(confidence_values) if confidence_values else 0.0
            cross_venue_count = sum(1 for sample in [*home_samples, *away_samples] if bool(sample.get('cross_venue')))
            confidence = clamp(
                base_conf
                + sample_total * step_conf
                + max(0.0, avg_sample_confidence - 55.0) * 0.08
                - cross_venue_count * 0.15,
                base_conf,
                cap_conf,
            )
            context = MatchContext(
                source='self_history',
                payload={
                    'home_team': match.home_team,
                    'away_team': match.away_team,
                    'home_samples': home_samples[:5],
                    'away_samples': away_samples[:5],
                },
                expected_home=expected_home,
                expected_away=expected_away,
                home_win_probability=home_win_probability,
                away_win_probability=away_win_probability,
                confidence=confidence,
                details={
                    'context_mode': 'self_history',
                    'home_recent_count': len(home_samples),
                    'away_recent_count': len(away_samples),
                    'home_strict_count': len(strict_home_samples),
                    'away_strict_count': len(strict_away_samples),
                    'cross_venue_count': cross_venue_count,
                    'avg_sample_confidence': round(avg_sample_confidence, 2),
                    'history_archives_scanned': archives_scanned,
                    'history_matches_used': history_matches_used,
                    'history_state_rows_scanned': state_rows_scanned,
                    'history_state_matches_used': state_matches_used,
                },
            )
            contexts[match.match_key] = context
            if len(preview_matches) < 8:
                preview_matches.append({
                    'match_key': match.match_key,
                    'home_team': match.home_team,
                    'away_team': match.away_team,
                    'home_samples': len(home_samples),
                    'away_samples': len(away_samples),
                    'home_strict_samples': len(strict_home_samples),
                    'away_strict_samples': len(strict_away_samples),
                    'cross_venue_samples': cross_venue_count,
                    'expected_home': expected_home,
                    'expected_away': expected_away,
                    'home_win_probability': home_win_probability,
                    'away_win_probability': away_win_probability,
                    'confidence': confidence,
                })

        stats = {
            'enabled': True,
            'archives_scanned': archives_scanned,
            'history_matches_used': history_matches_used,
            'state_rows_scanned': state_rows_scanned,
            'state_matches_used': state_matches_used,
            'contexts_built': len(contexts),
        }
        preview = {
            'archives_scanned': archives_scanned,
            'state_rows_scanned': state_rows_scanned,
            'contexts_built': len(contexts),
            'matches': preview_matches,
        }
        return contexts, stats, preview

    @staticmethod
    def _best_self_history_archive_context(raw_contexts: Any) -> dict[str, Any] | None:
        rows = [dict(item) for item in (raw_contexts or []) if isinstance(item, dict)]
        if not rows:
            return None

        def _rank(item: dict[str, Any]) -> tuple[int, int, int, float]:
            expected_home = item.get('expected_home')
            expected_away = item.get('expected_away')
            has_expected = expected_home is not None or expected_away is not None
            negative_expected = False
            for value in (expected_home, expected_away):
                try:
                    if value is not None and float(value) < 0:
                        negative_expected = True
                except Exception:
                    continue
            has_probability = item.get('home_win_probability') is not None or item.get('away_win_probability') is not None
            return (
                1 if has_expected and not negative_expected else 0,
                1 if has_probability else 0,
                1 if has_expected else 0,
                float(item.get('confidence') or 0.0),
            )

        rows.sort(key=_rank, reverse=True)
        return rows[0]

    @staticmethod
    def _team_history_key(name: str) -> str:
        cleaned = ''.join(char.lower() if char.isalnum() else ' ' for char in str(name or '').strip())
        tokens = [
            token
            for token in cleaned.split()
            if token not in {'fc', 'sc', 'cf', 'club', 'fk', 'ac', 'de', 'cd'}
        ]
        return ' '.join(tokens)

    def _sanitize_self_history_expected(self, value: Any) -> float | None:
        numeric = self._to_float_safe(value)
        if numeric is None:
            return None
        if bool(getattr(self.settings, 'self_history_sanitize_negative_expected_goals', True)) and numeric < 0:
            return None
        min_value = float(getattr(self.settings, 'min_expected_goals_value', 0.35) or 0.35)
        max_value = float(getattr(self.settings, 'max_expected_goals_value', 4.8) or 4.8)
        return clamp(numeric, min_value, max_value)

    def _sanitize_self_history_probability(self, value: Any) -> float | None:
        numeric = self._to_float_safe(value)
        if numeric is None:
            return None
        if not bool(getattr(self.settings, 'self_history_sanitize_probability_enabled', True)):
            return numeric
        if bool(getattr(self.settings, 'self_history_probability_percent_scale_enabled', True)) and numeric > 1.0 and numeric <= 100.0:
            numeric /= 100.0
        if numeric < 0.0 or numeric > 1.0:
            return None
        return clamp(numeric, 0.0, 1.0)

    def _weighted_team_history_metric(
        self,
        samples: list[dict[str, Any]],
        key: str,
        league_name: str,
    ) -> float | None:
        weighted_total = 0.0
        total_weight = 0.0
        target_league = str(league_name or '').strip().lower()
        for sample in samples:
            value = self._to_float_safe(sample.get(key))
            if key.startswith('expected'):
                value = self._sanitize_self_history_expected(value)
            elif key.endswith('probability') or key == 'win_probability':
                value = self._sanitize_self_history_probability(value)
            if value is None:
                continue
            weight = float(sample.get('weight') or 1.0)
            sample_confidence = clamp(float(sample.get('confidence') or 0.0), 0.0, 100.0)
            if sample_confidence > 0.0:
                weight *= 0.7 + (sample_confidence / 100.0) * 0.6
            if target_league and str(sample.get('league_name') or '').strip().lower() == target_league:
                weight *= 1.2
            weighted_total += value * weight
            total_weight += weight
        if total_weight <= 0:
            return None
        return weighted_total / total_weight

    def _combine_self_history_metric(
        self,
        primary_samples: list[dict[str, Any]],
        opponent_samples: list[dict[str, Any]],
        primary_key: str,
        opponent_key: str,
        league_name: str,
    ) -> float | None:
        primary_value = self._weighted_team_history_metric(primary_samples, primary_key, league_name)
        opponent_value = self._weighted_team_history_metric(opponent_samples, opponent_key, league_name)
        if primary_value is None and opponent_value is None:
            return None
        if primary_value is None:
            return opponent_value
        if opponent_value is None:
            return primary_value
        return (primary_value * 0.58) + (opponent_value * 0.42)

    @staticmethod
    def _to_float_safe(value: Any) -> float | None:
        try:
            if value in (None, ''):
                return None
            return float(value)
        except Exception:
            return None

    def _merge_context_maps(self, *maps: dict[str, Any]) -> dict[str, MatchContext]:
        merged: dict[str, MatchContext] = {}
        for mapping in maps:
            for match_key, raw_context in (mapping or {}).items():
                context = self._coerce_context(raw_context)
                if context is None:
                    continue
                existing = merged.get(match_key)
                merged[match_key] = context if existing is None else self._blend_contexts(existing, context)
        return merged

    @staticmethod
    def _coerce_context(value: Any) -> MatchContext | None:
        if value is None:
            return None
        if isinstance(value, MatchContext):
            return value
        if isinstance(value, dict):
            return MatchContext(
                source=str(value.get('source', 'unknown')),
                payload=value.get('payload', value),
                expected_home=value.get('expected_home'),
                expected_away=value.get('expected_away'),
                home_win_probability=value.get('home_win_probability'),
                away_win_probability=value.get('away_win_probability'),
                home_starting=value.get('home_starting'),
                away_starting=value.get('away_starting'),
                confidence=float(value.get('confidence', 58.0) or 58.0),
                profits=value.get('profits', {}),
                details=value.get('details', {}),
            )
        return None

    @staticmethod
    def _context_source_names(context: MatchContext) -> list[str]:
        details = dict(getattr(context, 'details', {}) or {})
        raw_sources = details.get('merged_sources') or []
        names: list[str] = []
        if isinstance(raw_sources, (list, tuple, set)):
            for item in raw_sources:
                text = str(item or '').strip()
                if text and text not in names:
                    names.append(text)
        if names:
            return names
        source = str(getattr(context, 'source', '') or '').strip()
        return [source] if source else []

    def _blend_contexts(self, base: MatchContext, new: MatchContext) -> MatchContext:
        base_weight = max(float(getattr(base, 'confidence', 0.0) or 0.0), 1.0) * self._context_blend_weight(base)
        new_weight = max(float(getattr(new, 'confidence', 0.0) or 0.0), 1.0) * self._context_blend_weight(new)

        def blend(a: Any, b: Any) -> float | None:
            pairs: list[tuple[float, float]] = []
            try:
                if a is not None:
                    pairs.append((float(a), base_weight))
            except Exception:
                pass
            try:
                if b is not None:
                    pairs.append((float(b), new_weight))
            except Exception:
                pass
            if not pairs:
                return None
            total_weight = sum(weight for _, weight in pairs)
            if total_weight <= 0:
                return None
            return sum(value * weight for value, weight in pairs) / total_weight

        base_sources = self._context_source_names(base)
        new_sources = self._context_source_names(new)
        merged_sources: list[str] = []
        for source in [*base_sources, *new_sources]:
            if source and source not in merged_sources:
                merged_sources.append(source)

        merged_payload = {
            'sources': {
                str(base.source or 'base'): base.payload,
                str(new.source or 'new'): new.payload,
            }
        }
        merged_details = dict(base.details or {})
        merged_details.update(dict(new.details or {}))
        merged_details['merged_sources'] = merged_sources
        merged_details['context_mode'] = 'ensemble' if len(merged_sources) > 1 else merged_sources[0] if merged_sources else 'unknown'
        merged_details['blend_weights'] = {
            str(base.source or 'base'): round(base_weight, 3),
            str(new.source or 'new'): round(new_weight, 3),
        }

        source_count = max(len(base_sources), 1) + max(len(new_sources), 1)
        weighted_confidence = ((base_weight * max(len(base_sources), 1)) + (new_weight * max(len(new_sources), 1))) / max(source_count, 1)
        merged_confidence = weighted_confidence + min(2.5, max(0, len(merged_sources) - 1) * 0.75)
        structural_sources = {'api_football', 'espn', 'football_data', 'thesportsdb', 'openfootball'}
        predictive_sources = structural_sources | {'sstats', 'sstats_form', 'bzzoiro', 'futrixmetrics', 'self_history'}
        news_sources = {'newsapi', 'gnews'}
        normalized_sources = {str(source or '').strip().lower() for source in merged_sources if str(source or '').strip()}
        if normalized_sources and normalized_sources.issubset(news_sources):
            confidence_cap = 62.0
        elif normalized_sources & structural_sources:
            confidence_cap = 76.0
        elif normalized_sources & predictive_sources:
            confidence_cap = 68.0
        else:
            confidence_cap = 64.0

        base_expected_home = self._sanitize_expected_goal_value(base.expected_home)
        new_expected_home = self._sanitize_expected_goal_value(new.expected_home)
        base_expected_away = self._sanitize_expected_goal_value(base.expected_away)
        new_expected_away = self._sanitize_expected_goal_value(new.expected_away)
        blended_home_starting = blend(base.home_starting, new.home_starting)
        blended_away_starting = blend(base.away_starting, new.away_starting)

        return MatchContext(
            source='ensemble' if len(merged_sources) > 1 else (merged_sources[0] if merged_sources else str(base.source or new.source or 'unknown')),
            payload=merged_payload,
            expected_home=blend(base_expected_home, new_expected_home),
            expected_away=blend(base_expected_away, new_expected_away),
            home_win_probability=blend(base.home_win_probability, new.home_win_probability),
            away_win_probability=blend(base.away_win_probability, new.away_win_probability),
            home_starting=int(round(blended_home_starting)) if blended_home_starting is not None else (new.home_starting or base.home_starting),
            away_starting=int(round(blended_away_starting)) if blended_away_starting is not None else (new.away_starting or base.away_starting),
            confidence=clamp(merged_confidence, 50.0, confidence_cap),
            profits={**dict(base.profits or {}), **dict(new.profits or {})},
            details=merged_details,
        )

    def _sanitize_expected_goal_value(self, value: Any) -> float | None:
        try:
            if value in (None, ''):
                return None
            number = float(value)
        except Exception:
            return None
        if not math.isfinite(number):
            return None
        min_goal = float(getattr(self.settings, 'min_expected_goals_value', 0.15) or 0.15)
        max_goal = float(getattr(self.settings, 'max_expected_goals_value', 4.8) or 4.8)
        if number < min_goal or number > max_goal:
            return None
        return number

    def _context_blend_weight(self, context: MatchContext) -> float:
        source_name = str(getattr(context, 'source', '') or '').strip().lower()
        reliability = self._context_source_weight(source_name)
        completeness = 0.78
        if getattr(context, 'expected_home', None) is not None or getattr(context, 'expected_away', None) is not None:
            completeness += 0.18
        if getattr(context, 'home_win_probability', None) is not None or getattr(context, 'away_win_probability', None) is not None:
            completeness += 0.16
        if getattr(context, 'home_starting', None) is not None or getattr(context, 'away_starting', None) is not None:
            completeness += 0.06
        details = dict(getattr(context, 'details', {}) or {})
        if details.get('home_recent_count') or details.get('away_recent_count'):
            completeness += 0.05
        if details.get('history_matches_used'):
            completeness += 0.04
        if source_name in {'newsapi', 'gnews'}:
            completeness = min(completeness, 0.92)
        return reliability * completeness

    def _context_source_weight(self, source_name: str) -> float:
        key = str(source_name or '').strip().lower()
        mapped = {
            'api_football': 1.06,
            'espn': 1.05,
            'football_data': 1.05,
            'openfootball': 1.02,
            'openligadb': 1.01,
            'thesportsdb': 0.96,
            'sstats': 0.92,
            'sstats_form': 0.88,
            'bzzoiro': 0.95,
            'bzzoiro_predictions': 0.95,
            'futrixmetrics': 0.94,
            'self_history': 0.98,
            'newsapi': 0.58,
            'gnews': 0.56,
        }
        if key in mapped:
            return mapped[key]
        return clamp(float(self.settings.source_weight(key)), 0.55, 1.08)

    @staticmethod
    def _serialize_candidate(item: CandidateBet) -> dict[str, Any]:
        row = asdict(item)
        row['commence_time'] = item.commence_time.isoformat()
        return row

    @staticmethod
    def _serialize_match(match: Match) -> dict[str, Any]:
        return {
            'match_key': match.match_key,
            'source': match.source,
            'source_event_id': match.source_event_id,
            'sport_key': match.sport_key,
            'league_name': match.league_name,
            'home_team': match.home_team,
            'away_team': match.away_team,
            'commence_time': match.commence_time.isoformat(),
            'tier': match.tier,
            'metadata': match.metadata,
        }

    @staticmethod
    def _serialize_context(item: MatchContext) -> dict[str, Any]:
        return {
            'source': item.source,
            'expected_home': item.expected_home,
            'expected_away': item.expected_away,
            'home_win_probability': item.home_win_probability,
            'away_win_probability': item.away_win_probability,
            'confidence': item.confidence,
            'details': item.details,
        }

    def _forecast_rows_for_export(
        self,
        model_debug: dict[str, Any],
        *,
        candidates: list[CandidateBet],
        publishable_candidates: list[CandidateBet],
        zero_stake_candidates: list[CandidateBet],
        reused_candidates: list[CandidateBet],
        quality_decisions: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        rows = [dict(item) for item in (model_debug.get('matches') or []) if isinstance(item, dict)]
        status_by_match: dict[str, str] = {}
        quality_by_key = {
            (
                item.get('match_key'),
                item.get('family'),
                item.get('selection_key'),
                item.get('point'),
                item.get('team_side'),
            ): item
            for item in (quality_decisions or [])
            if isinstance(item, dict)
        }

        for candidate in candidates:
            status_by_match[str(candidate.match_key)] = 'publishable'
        for candidate in zero_stake_candidates:
            status_by_match[str(candidate.match_key)] = 'zero_stake'
        for candidate in reused_candidates:
            status_by_match[str(candidate.match_key)] = 'reused_already_in_state'

        published_status = 'publishable_dry_run' if self.settings.publish_dry_run else 'published'
        for candidate in publishable_candidates:
            status_by_match[str(candidate.match_key)] = published_status

        for row in rows:
            match_key = str(row.get('match_key') or '')
            key = (
                row.get('match_key'),
                row.get('family'),
                row.get('selection_key'),
                row.get('point'),
                row.get('team_side'),
            )
            quality = quality_by_key.get(key)
            if quality:
                row['quality_status'] = quality.get('status') or ''
                row['quality_score'] = quality.get('quality_score') or ''
                row['quality_reasons'] = quality.get('reasons') or []
                row['quality_calibration'] = quality.get('calibration') or {}
                if quality.get('status') in {'rejected_by_quality_filters', 'quarantined_shadow'}:
                    row['forecast_status'] = quality.get('status')
                    continue
            row['forecast_status'] = status_by_match.get(match_key) or row.get('model_filter_status') or ''
        return rows

    @staticmethod
    def _serialize_offers(offers_by_match: dict[str, list[Offer]], limit: int = 25) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for match_key, offers in offers_by_match.items():
            for offer in offers:
                rows.append(
                    {
                        'match_key': match_key,
                        'source': offer.source,
                        'bookmaker': offer.bookmaker,
                        'family': offer.family,
                        'selection': offer.selection,
                        'price': offer.price,
                        'point': offer.point,
                        'team_side': offer.team_side,
                        'market_name': offer.market_name,
                        'market_key': offer.market_key,
                    }
                )
                if len(rows) >= limit:
                    return rows
        return rows


    def _build_provider_diagnostics(
        self,
        *,
        filtered_matches: list[Match],
        offer_maps: dict[str, dict[str, list[Offer]]],
        context_maps: dict[str, dict[str, MatchContext]],
        merged_contexts: dict[str, MatchContext],
        raw_candidates: list[CandidateBet],
        published_candidates: list[CandidateBet],
        source_stats: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        if not getattr(self.settings, 'enable_provider_diagnostics', True):
            return {'enabled': False, 'summary': {}, 'matches': []}

        raw_by_match = Counter(str(item.match_key) for item in raw_candidates)
        published_by_match = Counter(str(item.match_key) for item in published_candidates)
        raw_modes_by_match: dict[str, list[str]] = defaultdict(list)
        raw_derived_by_match: Counter[str] = Counter()
        for item in raw_candidates:
            key = str(item.match_key)
            mode = str(item.model_mode)
            if mode not in raw_modes_by_match[key]:
                raw_modes_by_match[key].append(mode)
            if bool((getattr(item, 'source_summary', {}) or {}).get('market_signal_derived')):
                raw_derived_by_match[key] += 1
        published_modes_by_match: dict[str, list[str]] = defaultdict(list)
        published_derived_by_match: Counter[str] = Counter()
        for item in published_candidates:
            key = str(item.match_key)
            mode = str(item.model_mode)
            if mode not in published_modes_by_match[key]:
                published_modes_by_match[key].append(mode)
            if bool((getattr(item, 'source_summary', {}) or {}).get('market_signal_derived')):
                published_derived_by_match[key] += 1

        matches_payload: list[dict[str, Any]] = []
        context_combo_counter: Counter[str] = Counter()
        offer_combo_counter: Counter[str] = Counter()

        limit = max(int(getattr(self.settings, 'diagnostics_match_limit', 150) or 150), 0)
        for match in filtered_matches[:limit]:
            context_sources = [name for name, mapping in context_maps.items() if mapping.get(match.match_key) is not None]
            offer_sources = [name for name, mapping in offer_maps.items() if mapping.get(match.match_key)]
            context_combo_key = '+'.join(context_sources) if context_sources else 'none'
            offer_combo_key = '+'.join(offer_sources) if offer_sources else 'none'
            context_combo_counter[context_combo_key] += 1
            offer_combo_counter[offer_combo_key] += 1

            offers = []
            for source_name, mapping in offer_maps.items():
                source_offers = list(mapping.get(match.match_key) or [])
                if not source_offers:
                    continue
                families = Counter(str(item.family) for item in source_offers)
                bookmakers = Counter(str(item.bookmaker) for item in source_offers)
                offers.append({
                    'source': source_name,
                    'offers_count': len(source_offers),
                    'families': dict(families),
                    'top_bookmakers': [name for name, _ in bookmakers.most_common(5)],
                })

            contexts = []
            for source_name, mapping in context_maps.items():
                context = mapping.get(match.match_key)
                if context is None:
                    continue
                details = getattr(context, 'details', None) or {}
                contexts.append({
                    'source': source_name,
                    'confidence': getattr(context, 'confidence', None),
                    'expected_home': getattr(context, 'expected_home', None),
                    'expected_away': getattr(context, 'expected_away', None),
                    'home_win_probability': getattr(context, 'home_win_probability', None),
                    'away_win_probability': getattr(context, 'away_win_probability', None),
                    'details_keys': sorted(list(details.keys()))[:12],
                })

            merged_context = merged_contexts.get(match.match_key)
            merged_sources = []
            if merged_context is not None:
                merged_sources = list(((getattr(merged_context, 'details', None) or {}).get('merged_sources')) or [])

            matches_payload.append({
                'match_key': match.match_key,
                'sport_key': match.sport_key,
                'league_name': match.league_name,
                'home_team': match.home_team,
                'away_team': match.away_team,
                'commence_time': match.commence_time.isoformat(),
                'offer_sources': offer_sources,
                'context_sources': context_sources,
                'offers': offers,
                'contexts': contexts,
                'has_merged_context': merged_context is not None,
                'merged_context_source': getattr(merged_context, 'source', None) if merged_context is not None else None,
                'merged_context_sources': merged_sources,
                'raw_candidate_count': raw_by_match.get(match.match_key, 0),
                'published_candidate_count': published_by_match.get(match.match_key, 0),
                'raw_candidate_modes': raw_modes_by_match.get(match.match_key, []),
                'published_candidate_modes': published_modes_by_match.get(match.match_key, []),
                'raw_candidate_market_signal_derived_count': raw_derived_by_match.get(match.match_key, 0),
                'published_candidate_market_signal_derived_count': published_derived_by_match.get(match.match_key, 0),
            })

        provider_summary: dict[str, Any] = {}
        for source_name, mapping in offer_maps.items():
            provider_summary[source_name] = {
                'type': 'offers',
                'matches_with_data': sum(1 for _match_key, offers in (mapping or {}).items() if offers),
                'items_total': sum(len(offers or []) for offers in (mapping or {}).values()),
                'stats': source_stats.get(source_name, {}),
            }
        for source_name, mapping in context_maps.items():
            provider_summary[source_name] = {
                'type': 'context',
                'matches_with_data': sum(1 for _match_key, context in (mapping or {}).items() if context is not None),
                'items_total': sum(1 for _match_key, context in (mapping or {}).items() if context is not None),
                'stats': source_stats.get(source_name, {}),
            }

        runtime_errors = {name: list(errors) for name, errors in self.provider_runtime_errors.items()}
        rate_limit_providers = {
            name: len([msg for msg in errors if '429' in str(msg) or 'rate limit' in str(msg).lower()])
            for name, errors in runtime_errors.items()
            if any('429' in str(msg) or 'rate limit' in str(msg).lower() for msg in errors)
        }
        summary = {
            'providers': provider_summary,
            'provider_status': dict(self.provider_status),
            'provider_runtime_errors': runtime_errors,
            'provider_rate_limits': rate_limit_providers,
            'matches_with_any_offer_source': sum(1 for match in filtered_matches if any((mapping.get(match.match_key) or []) for mapping in offer_maps.values())),
            'matches_with_any_context_source': sum(1 for match in filtered_matches if any(mapping.get(match.match_key) is not None for mapping in context_maps.values())),
            'matches_with_merged_context': sum(1 for match in filtered_matches if merged_contexts.get(match.match_key) is not None),
            'raw_candidates_with_derived_market_signal': sum(raw_derived_by_match.values()),
            'published_candidates_with_derived_market_signal': sum(published_derived_by_match.values()),
            'matches_with_raw_derived_market_signal': len(raw_derived_by_match),
            'matches_with_published_derived_market_signal': len(published_derived_by_match),
            'published_candidates_single_source_context': sum(
                1
                for item in published_candidates
                if int((getattr(item, 'source_summary', {}) or {}).get('context_sources_count') or getattr(item, 'sources_count', 0) or 0) <= 1
            ),
            'published_candidates_low_book_support': sum(1 for item in published_candidates if int(getattr(item, 'books_count', 0) or 0) <= 1),
            'context_source_combinations': dict(context_combo_counter),
            'offer_source_combinations': dict(offer_combo_counter),
        }

        return {'enabled': True, 'summary': summary, 'matches': matches_payload}
