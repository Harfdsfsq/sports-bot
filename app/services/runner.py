from __future__ import annotations

import asyncio
from collections import Counter, defaultdict
import json
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from importlib import import_module
from typing import Any

from app.config import Settings
from app.schemas import CandidateBet, Match, MatchContext, Offer
from app.services.market_monitor import MarketMonitor
from app.services.model import CandidateFactory
from app.services.quality import PredictionQualityService
from app.services.sheet_export import SheetExportService
from app.services.telegram import TelegramPublisher
from app.services.settlement import SettlementService
from app.state import JsonStateStore
from app.utils import candidate_selection_key, clamp, ensure_utc


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
        self.futrixmetrics = self._safe_provider('app.providers.futrixmetrics', 'FutrixMetricsContextProvider')
        self.sstats = self._safe_provider('app.providers.sstats', 'SStatsContextProvider')
        self.bzzoiro = self._safe_provider('app.providers.bzzoiro', 'BzzoiroContextProvider')
        self.api_football = self._safe_provider('app.providers.api_football', 'ApiFootballContextProvider')
        self.espn = self._safe_provider('app.providers.espn', 'EspnContextProvider')
        self.thesportsdb = self._safe_provider('app.providers.thesportsdb', 'TheSportsDbContextProvider')
        self.football_data = self._safe_provider('app.providers.football_data', 'FootballDataContextProvider')
        self.openligadb = self._safe_provider('app.providers.openligadb', 'OpenLigaDbContextProvider')
        self.openfootball = self._safe_provider('app.providers.openfootball', 'OpenFootballContextProvider')
        self.newsapi = self._safe_provider('app.providers.newsapi', 'NewsApiContextProvider')
        self.gnews = self._safe_provider('app.providers.gnews', 'GNewsContextProvider')
        self.factory = CandidateFactory(settings)
        self.quality = PredictionQualityService(settings)
        self.market_monitor = MarketMonitor(settings) if getattr(settings, 'market_monitor_enabled', True) else None
        self.sheet_export = SheetExportService(settings)
        self.telegram = TelegramPublisher(settings)
        self.settlement = SettlementService(settings)
        self.state = JsonStateStore(settings.state_path, settings.debug_path)

    @staticmethod
    def _provider_name_from_module(module_name: str) -> str:
        return module_name.rsplit('.', 1)[-1]

    @staticmethod
    def _format_exception(exc: Exception) -> str:
        return f'{type(exc).__name__}: {exc}'

    def _mark_provider_status(self, provider_name: str, **payload: Any) -> None:
        current = dict(self.provider_status.get(provider_name, {}))
        current.update(payload)
        self.provider_status[provider_name] = current

    def _provider_name(self, provider: Any | None) -> str:
        if provider is None:
            return 'unknown'
        module_name = getattr(provider.__class__, '__module__', '')
        if module_name:
            return self._provider_name_from_module(module_name)
        return provider.__class__.__name__.lower()

    def _safe_provider(self, module_name: str, class_name: str) -> Any | None:
        provider_name = self._provider_name_from_module(module_name)
        if module_name.endswith('bookies_bootstrap') and not getattr(self.settings, 'bookies_bootstrap_enabled', True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('odds_api_io') and not getattr(self.settings, 'enable_odds_api_io', True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('bookies_api') and not getattr(self.settings, 'bookies_api_enabled', False):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('oddspapi') and not getattr(self.settings, 'enable_oddspapi', False):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('allsportsapi') and not getattr(self.settings, 'enable_allsportsapi', False):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('futrixmetrics') and not getattr(self.settings, 'enable_futrixmetrics_context', False):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('bzzoiro') and not getattr(self.settings, 'enable_bzzoiro_context', True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('sstats') and (not getattr(self.settings, 'sstats_enabled', True) or not getattr(self.settings, 'enable_sstats_context', True)):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('api_football') and not getattr(self.settings, 'api_football_enabled', True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('espn') and not getattr(self.settings, 'enable_espn_context', True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('thesportsdb') and not getattr(self.settings, 'enable_thesportsdb_context', True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('football_data') and not getattr(self.settings, 'enable_football_data_context', True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('openligadb') and not getattr(self.settings, 'enable_openligadb_context', True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('openfootball') and not getattr(self.settings, 'enable_openfootball_context', True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('newsapi') and not getattr(self.settings, 'enable_newsapi_context', True):
            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')
            return None
        if module_name.endswith('gnews') and not getattr(self.settings, 'enable_gnews_context', True):
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
        self.state.save_run('running', summary={'started_at': started_at})
        try:
            now_utc = datetime.now(UTC)
            now_local = now_utc.astimezone(self.settings.tzinfo)

            settlement_probe = await self.settlement.settle_pending_bets(self.state.pending_bets(), now_utc)
            settlement_attempts_recorded = self.state.record_settlement_attempts(settlement_probe)
            settlement_summary = self.state.apply_settlements(list(settlement_probe.get('items') or []), self.settings)
            bankroll_summary = self.state.bankroll_summary(self.settings)
            quality_clv_rows = self.market_monitor.resolved_clv_rows() if self.market_monitor is not None else []
            quality_report = self.quality.build_quality_report(self.state.prediction_ledger(self.settings), quality_clv_rows)
            quality_export_paths = self.quality.export_quality_report(self.settings.storage_export_dir, quality_report)
            daily_report_due, daily_report_date, daily_report_skip_reason = self.state.daily_report_due(self.settings, now_utc)
            daily_report = self.state.build_daily_report(self.settings, daily_report_date) if daily_report_due else None
            daily_report_refresh_reason: str | None = None
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
            daily_report_messages_sent = 0
            daily_report_payloads: list[str] = []
            if daily_report is not None:
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

            bootstrap_matches, bootstrap_meta = await self._fetch_matches()
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
            )

            offer_maps = {
                'odds_api_io': odds_api_io_offers,
                'bookies_api': bookies_api_offers,
                'oddspapi': oddspapi_offers,
                'allsportsapi': allsportsapi_offers,
            }
            merged_offers = self._merge_offers(*offer_maps.values())
            market_signals: dict[str, dict[str, Any]] = {}
            market_monitor_stats: dict[str, Any] = {'enabled': False}
            market_monitor_preview: dict[str, Any] = {}
            if self.market_monitor is not None:
                market_signals, market_monitor_stats, market_monitor_preview = self.market_monitor.build_signals(filtered_matches, merged_offers, now_utc)

            context_target_matches, context_enrichment = self._select_context_enrichment_matches(
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
            }
            provider_target_counts = {name: len(items) for name, items in provider_targets.items()}

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
            }
            contexts = self._merge_context_maps(*context_maps.values())

            raw_candidates, rejections, model_debug = self.factory.build_candidates(filtered_matches, merged_offers, contexts, market_signals)
            candidates_before_quality = list(raw_candidates)
            raw_candidates, quality_rejections, quality_debug = self.quality.apply_to_candidates(raw_candidates, quality_report, now_utc)
            for reason, count in quality_rejections.items():
                rejections[f'quality_{reason}'] = rejections.get(f'quality_{reason}', 0) + count

            seen_fingerprints = self._load_seen_candidate_fingerprints()
            candidates: list[CandidateBet] = []
            reused_candidates: list[CandidateBet] = []
            reused_already_in_state = 0
            for candidate in raw_candidates:
                fingerprint = self._candidate_fingerprint(candidate)
                if fingerprint and fingerprint in seen_fingerprints:
                    reused_already_in_state += 1
                    candidate.already_used = True
                    reused_candidates.append(candidate)
                    continue
                candidates.append(candidate)

            candidates = self.state.annotate_candidates_with_stakes(candidates, self.settings)
            zero_stake_candidates = [
                candidate for candidate in candidates
                if float(getattr(candidate, 'stake_amount', 0.0) or 0.0) <= 0.0
            ]
            publishable_candidates = self._filter_publishable_candidates(candidates)
            prediction_publication_enabled = bool(getattr(self.settings, 'prediction_publication_enabled', True))
            if not prediction_publication_enabled:
                publishable_candidates = []
            bankroll_preview = self._project_bankroll_summary(publishable_candidates)
            settlement_messages_sent, settlement_payloads = await self.telegram.publish_settlement_summary(settlement_summary)
            sent_messages, telegram_payloads = await self.telegram.publish(publishable_candidates, bankroll_summary=bankroll_preview)
            published_count = self.state.store_candidates(publishable_candidates, telegram_sent=sent_messages > 0)
            telegram_picks_sent = len(publishable_candidates) if sent_messages > 0 else 0
            sent_messages += settlement_messages_sent + daily_report_messages_sent
            telegram_payloads = list(settlement_payloads) + list(daily_report_payloads) + list(telegram_payloads)
            clv_record_stats = self.market_monitor.record_published_candidates(publishable_candidates, now_utc) if self.market_monitor is not None else {'tracked': 0}
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
            export_paths = self.state.export_payloads(
                self.settings.storage_export_dir,
                filtered_matches,
                publishable_candidates,
                forecast_rows=forecast_rows,
                settings=self.settings,
            )
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
                'current_time_utc': now_utc.isoformat(),
                'current_time_local': now_local.isoformat(),
                'app_timezone': self.settings.app_timezone,
                'matches_seen': len(filtered_matches),
                'matches_before_publish_window': len(deduped_matches),
                'matches_with_offers': sum(1 for match in filtered_matches if merged_offers.get(match.match_key)),
                'context_matches_requested': len(context_target_matches),
                'context_enrichment': context_enrichment,
                'provider_context_targets': provider_target_counts,
                'contexts_built': len(contexts),
                'candidates': len(candidates),
                'candidates_publishable': len(publishable_candidates),
                'candidates_zero_stake': len(zero_stake_candidates),
                'candidates_raw': len(raw_candidates),
                'candidates_before_quality': len(candidates_before_quality),
                'candidates_rejected_by_quality': max(0, len(candidates_before_quality) - len(raw_candidates)),
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

            run_report_messages_sent, run_report_payloads = await self.telegram.publish_run_report(summary)
            sent_messages += run_report_messages_sent
            telegram_payloads = list(telegram_payloads) + list(run_report_payloads)
            summary['telegram_messages_sent'] = sent_messages
            summary['run_report'] = {
                'enabled': bool(getattr(self.settings, 'run_report_enabled', True)),
                'telegram_messages_sent': run_report_messages_sent,
                'sent': run_report_messages_sent > 0,
            }

            self.state.write_debug(
                {
                    'created_at': datetime.now(UTC).isoformat(),
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
                        'context_enrichment_match_limit': self.settings.context_enrichment_match_limit,
                        'context_enrichment_requires_offers': self.settings.context_enrichment_requires_offers,
                    },
                    'source_previews': {
                        'match_bootstrap': bootstrap_preview,
                        'bookies_bootstrap': (bootstrap_attempts.get('bookies_bootstrap', {}).get('preview') or {}),
                        'odds_api_io_bootstrap': (bootstrap_attempts.get('odds_api_io', {}).get('preview') or {}),
                        'odds_api_io': odds_io_preview,
                        'bookies_api': bookies_preview,
                        'oddspapi': oddspapi_preview,
                        'allsportsapi': allsportsapi_preview,
                        'futrixmetrics': futrixmetrics_preview,
                        'sstats': sstats_preview,
                        'bzzoiro': bzzoiro_preview,
                        'api_football': api_football_preview,
                        'espn': espn_preview,
                        'thesportsdb': thesportsdb_preview,
                        'football_data': football_data_preview,
                        'openfootball': openfootball_preview,
                        'newsapi': newsapi_preview,
                        'gnews': gnews_preview,
                        'market_monitor': market_monitor_preview,
                    },
                    'sample_matches': [self._serialize_match(item) for item in filtered_matches[:25]],
                    'sample_offers': self._serialize_offers(merged_offers, limit=25),
                    'sample_contexts': [self._serialize_context(item) for item in list(contexts.values())[:25]],
                    'provider_diagnostics': provider_diagnostics if self.settings.enable_provider_diagnostics else {'enabled': False},
                    'forecast_rows': forecast_rows[:200],
                    'model_debug': model_debug,
                    'quality_report': quality_report,
                    'quality_debug': quality_debug,
                    'candidates': [self._serialize_candidate(item) for item in publishable_candidates[:25]],
                    'candidates_before_quality': [self._serialize_candidate(item) for item in candidates_before_quality[:25]],
                    'candidates_zero_stake': [self._serialize_candidate(item) for item in zero_stake_candidates[:25]],
                    'reused_candidates': [self._serialize_candidate(item) for item in reused_candidates[:25]],
                    'telegram_messages': telegram_payloads,
                    'settlement': {
                        'probe': settlement_probe,
                        'summary': settlement_summary,
                    },
                    'daily_report': daily_report or {},
                    'bet_ledger_sample': bet_ledger_rows[:25],
                    'bankroll': bankroll_summary,
                    'sheet_export': sheet_export_result,
                }
            )
            self.state.save_run('ok', summary=summary)
            return summary
        except Exception as exc:
            error_text = f'{type(exc).__name__}: {exc}'
            self.state.save_run('error', error_text=error_text)
            self.state.write_debug({'created_at': datetime.now(UTC).isoformat(), 'error': error_text})
            raise

    async def _fetch_matches(self) -> tuple[list[Match], dict[str, Any]]:
        strategy = str(getattr(self.settings, 'match_bootstrap_provider', 'odds_api_io') or 'odds_api_io').strip().lower()
        allow_fallback = bool(getattr(self.settings, 'bootstrap_fallback_to_bookies', True))

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
                return result[0], result[1] or {}, result[2] or {}
            if len(result) == 2:
                return result[0], result[1] or {}, {}
            if len(result) == 1:
                return result[0], {}, {}
        return result, {}, {}

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

        publish_window_hours = min(12, max(1, int(getattr(self.settings, "publish_window_hours", 12) or 12)))
        horizon = now_utc + timedelta(hours=publish_window_hours)
        configured_min_lead_minutes = max(30, int(getattr(self.settings, "min_kickoff_lead_minutes", 30) or 30))
        adaptive_min_lead_minutes = max(30, int(getattr(self.settings, 'adaptive_min_kickoff_lead_minutes', configured_min_lead_minutes) or configured_min_lead_minutes))
        manual_late_mode_applied = False
        if bool(getattr(self.settings, 'manual_late_mode_enabled', False)):
            configured_min_lead_minutes = max(30, int(getattr(self.settings, 'manual_late_min_kickoff_lead_minutes', configured_min_lead_minutes) or configured_min_lead_minutes))
            adaptive_min_lead_minutes = max(30, int(getattr(self.settings, 'manual_late_adaptive_min_kickoff_lead_minutes', adaptive_min_lead_minutes) or adaptive_min_lead_minutes))
            manual_late_mode_applied = True
        min_lead = timedelta(minutes=configured_min_lead_minutes)
        filtered, skipped_started, skipped_too_soon, skipped_outside_window = apply_filter(min_lead)

        fallback_applied = False
        emergency_applied = False
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
        emergency_min_lead_minutes = max(30, int(getattr(self.settings, 'emergency_min_kickoff_lead_minutes', effective_min_lead_minutes) or effective_min_lead_minutes))
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
            'publish_window_hours': publish_window_hours,
            'min_kickoff_lead_minutes': effective_min_lead_minutes,
            'configured_min_kickoff_lead_minutes': max(30, int(getattr(self.settings, 'min_kickoff_lead_minutes', 30) or 30)),
            'adaptive_min_kickoff_lead_enabled': getattr(self.settings, 'adaptive_min_kickoff_lead_enabled', True),
            'adaptive_min_kickoff_lead_minutes': adaptive_min_lead_minutes,
            'adaptive_min_kickoff_lead_applied': fallback_applied,
            'emergency_min_kickoff_lead_enabled': getattr(self.settings, 'emergency_min_kickoff_lead_enabled', True),
            'emergency_min_kickoff_lead_minutes': emergency_min_lead_minutes,
            'emergency_min_kickoff_activation_ratio': float(getattr(self.settings, 'emergency_min_kickoff_activation_ratio', 0.85) or 0.85),
            'emergency_min_kickoff_lead_applied': emergency_applied,
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
        }
        provider_key = str(provider_name or '').strip().lower()
        limit = max(0, int(limit_map.get(provider_key, 0) or 0))
        if limit <= 0:
            return matches

        staged = bool(getattr(self.settings, 'enable_context_staging', True))
        premium_limit = max(0, int(getattr(self.settings, 'premium_context_shortlist_limit', 18) or 18))
        if staged:
            if provider_key in {'espn', 'api_football', 'newsapi', 'gnews', 'openfootball'}:
                limit = min(limit, premium_limit)
            elif provider_key in {'thesportsdb'}:
                limit = min(limit, max(premium_limit * 2, premium_limit))
            elif provider_key in {'football_data'}:
                limit = min(limit, max(premium_limit * 3, premium_limit))
            elif provider_key in {'openligadb'}:
                limit = min(limit, max(premium_limit * 2, premium_limit))
            elif provider_key in {'futrixmetrics'}:
                limit = min(limit, max(4, premium_limit // 2))
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
            kickoff_weight = -abs(ensure_utc(match.commence_time).timestamp())
            rank_key = (
                support_score,
                league_priority,
                float(supported_books),
                rich_offer_bonus,
                float(len(families)),
                float(len(unique_books)),
                float(len(offers)),
                low_tier_penalty,
                kickoff_weight,
            )
            ranked.append((rank_key, match))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [match for _, match in ranked]

    def _provider_context_support_score(self, provider_key: str, match: Match) -> float:
        provider_key = str(provider_key or '').strip().lower()
        league_priority = float(self.settings.league_priority_score(match.league_name))
        if provider_key == 'api_football':
            return 1.0 if not self.settings.is_low_tier_league(match.league_name) else 0.82
        if provider_key == 'espn':
            checker = getattr(self.espn, 'supports_match', None)
            return 1.0 if callable(checker) and checker(match) else 0.0
        if provider_key == 'openfootball':
            checker = getattr(self.openfootball, 'supports_match', None)
            return 0.96 if callable(checker) and checker(match) else 0.0
        if provider_key == 'openligadb':
            checker = getattr(self.openligadb, 'supports_match', None)
            return 0.95 if callable(checker) and checker(match) else 0.0
        if provider_key == 'football_data':
            checker = getattr(self.football_data, 'supports_match', None)
            return 0.94 if callable(checker) and checker(match) else 0.0
        if provider_key == 'thesportsdb':
            checker = getattr(self.thesportsdb, 'supports_match', None)
            if callable(checker) and checker(match):
                return 0.9 if league_priority >= 1.0 else 0.7
            return 0.0
        if provider_key == 'sstats':
            return 0.92 if league_priority >= 1.0 else 0.74
        if provider_key == 'bzzoiro':
            return 0.88 if league_priority >= 2.0 else 0.62 if league_priority >= 1.0 else 0.0
        if provider_key == 'futrixmetrics':
            return 0.84 if league_priority >= 1.0 else 0.58
        if provider_key == 'newsapi':
            return 1.0 if league_priority >= 2.0 else 0.55 if league_priority >= 1.0 else 0.0
        if provider_key == 'gnews':
            return 1.0 if league_priority >= 2.0 else 0.4 if league_priority >= 1.0 else 0.0
        return 0.5

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

        ranked: list[tuple[tuple[float, ...], Match]] = []
        skipped_without_offers = 0
        hints_kept = 0
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
            rank_key = (
                value_hint,
                league_priority,
                float(supported_books),
                float(len(unique_books)),
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
        }
        return selected, summary

    @staticmethod
    def _dedupe_matches(matches: list[Match]) -> list[Match]:
        unique: dict[str, Match] = {}
        for match in matches:
            unique.setdefault(match.match_key, match)
        return list(unique.values())

    def _load_seen_candidate_fingerprints(self) -> set[str]:
        paths = [Path(self.settings.state_path)]
        latest_picks = Path(self.settings.storage_export_dir) / 'latest-picks.json'
        if latest_picks not in paths:
            paths.append(latest_picks)

        seen: set[str] = set()
        for path in paths:
            if not path.exists() or not path.is_file():
                continue
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                continue
            self._collect_candidate_fingerprints(payload, seen)
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
        if not getattr(self.settings, 'bankroll_enabled', True):
            return candidates
        return [
            candidate for candidate in candidates
            if float(getattr(candidate, 'stake_amount', 0.0) or 0.0) > 0.0
        ]

    def _collect_candidate_fingerprints(self, value: Any, seen: set[str]) -> None:
        if isinstance(value, dict):
            fingerprint = self._candidate_fingerprint(value)
            if fingerprint:
                seen.add(fingerprint)
            for item in value.values():
                self._collect_candidate_fingerprints(item, seen)
            return
        if isinstance(value, list):
            for item in value:
                self._collect_candidate_fingerprints(item, seen)

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
        base_weight = max(float(getattr(base, 'confidence', 0.0) or 0.0), 1.0)
        new_weight = max(float(getattr(new, 'confidence', 0.0) or 0.0), 1.0)

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

        source_count = max(len(base_sources), 1) + max(len(new_sources), 1)
        weighted_confidence = ((base_weight * max(len(base_sources), 1)) + (new_weight * max(len(new_sources), 1))) / max(source_count, 1)
        merged_confidence = weighted_confidence + min(2.5, max(0, len(merged_sources) - 1) * 0.75)
        structural_sources = {'api_football', 'espn', 'football_data', 'thesportsdb', 'openfootball'}
        predictive_sources = structural_sources | {'sstats', 'sstats_form', 'bzzoiro', 'futrixmetrics'}
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

        return MatchContext(
            source='ensemble' if len(merged_sources) > 1 else (merged_sources[0] if merged_sources else str(base.source or new.source or 'unknown')),
            payload=merged_payload,
            expected_home=blend(base.expected_home, new.expected_home),
            expected_away=blend(base.expected_away, new.expected_away),
            home_win_probability=blend(base.home_win_probability, new.home_win_probability),
            away_win_probability=blend(base.away_win_probability, new.away_win_probability),
            home_starting=int(round(blend(base.home_starting, new.home_starting))) if blend(base.home_starting, new.home_starting) is not None else (new.home_starting or base.home_starting),
            away_starting=int(round(blend(base.away_starting, new.away_starting))) if blend(base.away_starting, new.away_starting) is not None else (new.away_starting or base.away_starting),
            confidence=clamp(merged_confidence, 50.0, confidence_cap),
            profits={**dict(base.profits or {}), **dict(new.profits or {})},
            details=merged_details,
        )

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

        summary = {
            'providers': provider_summary,
            'provider_status': dict(self.provider_status),
            'provider_runtime_errors': {name: list(errors) for name, errors in self.provider_runtime_errors.items()},
            'matches_with_any_offer_source': sum(1 for match in filtered_matches if any((mapping.get(match.match_key) or []) for mapping in offer_maps.values())),
            'matches_with_any_context_source': sum(1 for match in filtered_matches if any(mapping.get(match.match_key) is not None for mapping in context_maps.values())),
            'matches_with_merged_context': sum(1 for match in filtered_matches if merged_contexts.get(match.match_key) is not None),
            'raw_candidates_with_derived_market_signal': sum(raw_derived_by_match.values()),
            'published_candidates_with_derived_market_signal': sum(published_derived_by_match.values()),
            'matches_with_raw_derived_market_signal': len(raw_derived_by_match),
            'matches_with_published_derived_market_signal': len(published_derived_by_match),
            'context_source_combinations': dict(context_combo_counter),
            'offer_source_combinations': dict(offer_combo_counter),
        }

        return {'enabled': True, 'summary': summary, 'matches': matches_payload}
