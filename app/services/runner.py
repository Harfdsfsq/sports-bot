from __future__ import annotations

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
from app.services.telegram import TelegramPublisher
from app.state import JsonStateStore
from app.utils import ensure_utc


class PredictionRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.provider_status: dict[str, dict[str, Any]] = {}
        self.provider_runtime_errors: dict[str, list[str]] = defaultdict(list)
        self.bookies_bootstrap = self._safe_provider('app.providers.bookies_bootstrap', 'BookiesBootstrapProvider')
        self.odds_api_io = self._safe_provider('app.providers.odds_api_io', 'OddsApiIoProvider')
        self.bookies_api = self._safe_provider('app.providers.bookies_api', 'BookiesApiProvider')
        self.sstats = self._safe_provider('app.providers.sstats', 'SStatsContextProvider')
        self.api_football = self._safe_provider('app.providers.api_football', 'ApiFootballContextProvider')
        self.espn = self._safe_provider('app.providers.espn', 'EspnContextProvider')
        self.thesportsdb = self._safe_provider('app.providers.thesportsdb', 'TheSportsDbContextProvider')
        self.factory = CandidateFactory(settings)
        self.market_monitor = MarketMonitor(settings) if getattr(settings, 'market_monitor_enabled', True) else None
        self.telegram = TelegramPublisher(settings)
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

            bootstrap_matches, bootstrap_meta = await self._fetch_matches()
            deduped_matches = self._dedupe_matches(bootstrap_matches)
            bootstrap_provider = str(bootstrap_meta.get('provider') or 'none')
            bootstrap_attempts = dict(bootstrap_meta.get('attempts') or {})
            bootstrap_stats = dict(bootstrap_meta.get('stats') or {})
            bootstrap_preview = dict(bootstrap_meta.get('preview') or {})
            filtered_matches, filtering = self._filter_matches(deduped_matches, now_utc)

            odds_api_io_offers, odds_io_stats, odds_io_preview = await self._fetch_provider(
                self.odds_api_io,
                'fetch_offers',
                filtered_matches,
                empty_data={},
            )
            bookies_api_offers, bookies_stats, bookies_preview = await self._fetch_provider(
                self.bookies_api,
                'fetch_offers',
                filtered_matches,
                empty_data={},
            )

            offer_maps = {
                'odds_api_io': odds_api_io_offers,
                'bookies_api': bookies_api_offers,
            }
            merged_offers = self._merge_offers(*offer_maps.values())
            context_target_matches, context_enrichment = self._select_context_enrichment_matches(
                filtered_matches,
                merged_offers,
                now_utc,
            )

            sstats_contexts, sstats_stats, sstats_preview = await self._fetch_provider(
                self.sstats,
                'fetch_context',
                context_target_matches,
                empty_data={},
            )
            api_football_contexts, api_football_stats, api_football_preview = await self._fetch_provider(
                self.api_football,
                'fetch_context',
                context_target_matches,
                empty_data={},
            )
            espn_contexts, espn_stats, espn_preview = await self._fetch_provider(
                self.espn,
                'fetch_context',
                context_target_matches,
                empty_data={},
            )
            thesportsdb_contexts, thesportsdb_stats, thesportsdb_preview = await self._fetch_provider(
                self.thesportsdb,
                'fetch_context',
                context_target_matches,
                empty_data={},
            )

            context_maps = {
                'sstats': sstats_contexts,
                'api_football': api_football_contexts,
                'espn': espn_contexts,
                'thesportsdb': thesportsdb_contexts,
            }
            contexts = self._merge_context_maps(*context_maps.values())

            market_signals: dict[str, dict[str, Any]] = {}
            market_monitor_stats: dict[str, Any] = {'enabled': False}
            market_monitor_preview: dict[str, Any] = {}
            if self.market_monitor is not None:
                market_signals, market_monitor_stats, market_monitor_preview = self.market_monitor.build_signals(filtered_matches, merged_offers, now_utc)
            raw_candidates, rejections, model_debug = self.factory.build_candidates(filtered_matches, merged_offers, contexts, market_signals)

            seen_fingerprints = self._load_seen_candidate_fingerprints()
            candidates = []
            skipped_already_in_state = 0
            for candidate in raw_candidates:
                fingerprint = self._candidate_fingerprint(candidate)
                if fingerprint and fingerprint in seen_fingerprints:
                    skipped_already_in_state += 1
                    continue
                candidates.append(candidate)

            sent_messages, telegram_payloads = await self.telegram.publish(candidates)
            published_count = self.state.store_candidates(candidates, telegram_sent=sent_messages > 0)
            telegram_picks_sent = len(candidates) if sent_messages > 0 else 0
            clv_record_stats = self.market_monitor.record_published_candidates(candidates, now_utc) if self.market_monitor is not None else {'tracked': 0}
            export_paths = self.state.export_payloads(self.settings.storage_export_dir, filtered_matches, candidates)

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
                'sstats': sstats_stats,
                'api_football': api_football_stats,
                'espn': espn_stats,
                'thesportsdb': thesportsdb_stats,
                'market_monitor': market_monitor_stats,
            }
            mode_counts: dict[str, int] = defaultdict(int)
            for candidate in candidates:
                mode_counts[str(candidate.model_mode)] += 1

            provider_diagnostics = self._build_provider_diagnostics(
                filtered_matches=filtered_matches,
                offer_maps=offer_maps,
                context_maps=context_maps,
                merged_contexts=contexts,
                raw_candidates=raw_candidates,
                published_candidates=candidates,
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
                'contexts_built': len(contexts),
                'candidates': len(candidates),
                'candidates_raw': len(raw_candidates),
                'skipped_already_in_state': skipped_already_in_state,
                'published': telegram_picks_sent,
                'published_to_telegram': telegram_picks_sent,
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
                    'api_football_exact': api_football_stats.get('matched_exact', 0),
                    'api_football_loose': api_football_stats.get('matched_loose', 0),
                    'api_football_fuzzy': api_football_stats.get('matched_fuzzy', 0),
                    'espn_exact': espn_stats.get('matched_exact', 0),
                    'espn_loose': espn_stats.get('matched_loose', 0),
                    'espn_fuzzy': espn_stats.get('matched_fuzzy', 0),
                    'thesportsdb_contexts': thesportsdb_stats.get('contexts_built', 0),
                },
                'rejections': rejections,
                'candidate_modes': dict(mode_counts),
                'provider_diagnostics': provider_diagnostics['summary'],
                'market_monitor': {
                    **market_monitor_stats,
                    'clv_tracked_now': clv_record_stats.get('tracked', 0),
                },
                'exports': export_paths,
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
                        'sstats': sstats_preview,
                        'api_football': api_football_preview,
                        'espn': espn_preview,
                        'thesportsdb': thesportsdb_preview,
                        'market_monitor': market_monitor_preview,
                    },
                    'sample_matches': [self._serialize_match(item) for item in filtered_matches[:25]],
                    'sample_offers': self._serialize_offers(merged_offers, limit=25),
                    'sample_contexts': [self._serialize_context(item) for item in list(contexts.values())[:25]],
                    'provider_diagnostics': provider_diagnostics if self.settings.enable_provider_diagnostics else {'enabled': False},
                    'model_debug': model_debug,
                    'candidates': [self._serialize_candidate(item) for item in candidates[:25]],
                    'telegram_messages': telegram_payloads,
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
        filtered: list[Match] = []
        skipped_started = 0
        skipped_too_soon = 0
        skipped_outside_window = 0
        horizon = now_utc + timedelta(hours=self.settings.publish_window_hours)
        min_lead = timedelta(minutes=self.settings.min_kickoff_lead_minutes)

        for match in matches:
            commence = ensure_utc(match.commence_time)
            if commence <= now_utc:
                skipped_started += 1
                continue
            if commence - now_utc < min_lead:
                skipped_too_soon += 1
                continue
            if commence > horizon:
                skipped_outside_window += 1
                continue
            filtered.append(match)

        filtering = {
            'total_before': len(matches),
            'total_after': len(filtered),
            'skipped_started': skipped_started,
            'skipped_too_soon': skipped_too_soon,
            'skipped_outside_window': skipped_outside_window,
            'publish_window_hours': self.settings.publish_window_hours,
            'min_kickoff_lead_minutes': self.settings.min_kickoff_lead_minutes,
            'now_utc': now_utc.isoformat(),
            'now_local': now_utc.astimezone(self.settings.tzinfo).isoformat(),
        }
        return filtered, filtering

    def _select_context_enrichment_matches(
        self,
        matches: list[Match],
        offers_by_match: dict[str, list[Offer]],
        now_utc: datetime,
    ) -> tuple[list[Match], dict[str, Any]]:
        requires_offers = bool(getattr(self.settings, 'context_enrichment_requires_offers', True))
        limit = int(getattr(self.settings, 'context_enrichment_match_limit', 0) or 0)
        target_bookmakers = {str(name).strip().lower() for name in (self.settings.target_bookmakers or []) if str(name).strip()}

        ranked: list[tuple[tuple[float, ...], Match]] = []
        skipped_without_offers = 0
        for match in matches:
            offers = list(offers_by_match.get(match.match_key) or [])
            if requires_offers and not offers:
                skipped_without_offers += 1
                continue
            unique_books = {str(item.bookmaker or '').strip().lower() for item in offers if str(item.bookmaker or '').strip()}
            supported_books = len(unique_books & target_bookmakers) if target_bookmakers else len(unique_books)
            families = {str(item.family or '').strip().lower() for item in offers if str(item.family or '').strip()}
            kickoff_delta = max((ensure_utc(match.commence_time) - now_utc).total_seconds(), 0.0)
            rank_key = (
                float(supported_books),
                float(len(unique_books)),
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

        summary = {
            'requires_offers': requires_offers,
            'limit': limit,
            'eligible_matches': len(ranked),
            'selected_matches': len(selected),
            'trimmed_matches': trimmed,
            'skipped_without_offers': skipped_without_offers,
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

        match_key = get('match_key')
        family = get('family')
        selection = get('selection')
        if not match_key or not family or not selection:
            return None

        point = get('point')
        if isinstance(point, float):
            point = round(point, 4)
        commence_time = get('commence_time')
        if hasattr(commence_time, 'isoformat'):
            commence_time = commence_time.isoformat()
        if commence_time is None:
            commence_time = ''

        return '|'.join(str(part) for part in (match_key, family, selection, point, commence_time))

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

        merged_sources = []
        for source in (str(base.source or ''), str(new.source or '')):
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

        return MatchContext(
            source='ensemble' if len(merged_sources) > 1 else (merged_sources[0] if merged_sources else str(base.source or new.source or 'unknown')),
            payload=merged_payload,
            expected_home=blend(base.expected_home, new.expected_home),
            expected_away=blend(base.expected_away, new.expected_away),
            home_win_probability=blend(base.home_win_probability, new.home_win_probability),
            away_win_probability=blend(base.away_win_probability, new.away_win_probability),
            home_starting=int(round(blend(base.home_starting, new.home_starting))) if blend(base.home_starting, new.home_starting) is not None else (new.home_starting or base.home_starting),
            away_starting=int(round(blend(base.away_starting, new.away_starting))) if blend(base.away_starting, new.away_starting) is not None else (new.away_starting or base.away_starting),
            confidence=min(84.0, max(base_weight, new_weight) + (4.0 if len(merged_sources) > 1 else 0.0)),
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
        for item in raw_candidates:
            key = str(item.match_key)
            mode = str(item.model_mode)
            if mode not in raw_modes_by_match[key]:
                raw_modes_by_match[key].append(mode)
        published_modes_by_match: dict[str, list[str]] = defaultdict(list)
        for item in published_candidates:
            key = str(item.match_key)
            mode = str(item.model_mode)
            if mode not in published_modes_by_match[key]:
                published_modes_by_match[key].append(mode)

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
            'context_source_combinations': dict(context_combo_counter),
            'offer_source_combinations': dict(offer_combo_counter),
        }

        return {'enabled': True, 'summary': summary, 'matches': matches_payload}
