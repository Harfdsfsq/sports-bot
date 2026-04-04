from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from importlib import import_module
from typing import Any

from app.config import Settings
from app.schemas import CandidateBet, Match, Offer
from app.services.model import CandidateFactory
from app.services.telegram import TelegramPublisher
from app.state import JsonStateStore
from app.utils import ensure_utc


class PredictionRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bookies_bootstrap = self._safe_provider('app.providers.bookies_bootstrap', 'BookiesBootstrapProvider')
        self.odds_api_io = self._safe_provider('app.providers.odds_api_io', 'OddsApiIoProvider')
        self.bookies_api = self._safe_provider('app.providers.bookies_api', 'BookiesApiProvider')
        self.sstats = self._safe_provider('app.providers.sstats', 'SStatsContextProvider')
        self.factory = CandidateFactory(settings)
        self.telegram = TelegramPublisher(settings)
        self.state = JsonStateStore(settings.state_path, settings.debug_path)

    def _safe_provider(self, module_name: str, class_name: str) -> Any | None:
        try:
            module = import_module(module_name)
            cls = getattr(module, class_name)
            return cls(self.settings)
        except Exception:
            return None

    async def run_once(self) -> dict[str, Any]:
        started_at = datetime.now(UTC).isoformat()
        self.state.save_run('running', summary={'started_at': started_at})
        try:
            now_utc = datetime.now(UTC)
            now_local = now_utc.astimezone(self.settings.tzinfo)

            bootstrap_matches, bootstrap_stats, bootstrap_preview = await self._fetch_matches()
            deduped_matches = self._dedupe_matches(bootstrap_matches)
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
            contexts, sstats_stats, sstats_preview = await self._fetch_provider(
                self.sstats,
                'fetch_context',
                filtered_matches,
                empty_data={},
            )

            merged_offers = self._merge_offers(odds_api_io_offers, bookies_api_offers)
            candidates, rejections, model_debug = self.factory.build_candidates(filtered_matches, merged_offers, contexts)

            sent_messages, telegram_payloads = await self.telegram.publish(candidates)
            published_count = self.state.store_candidates(candidates, telegram_sent=sent_messages > 0)
            export_paths = self.state.export_payloads(self.settings.storage_export_dir, filtered_matches, candidates)

            source_stats = {
                'bookies_bootstrap': bootstrap_stats,
                'odds_api_io': odds_io_stats,
                'bookies_api': bookies_stats,
                'sstats': sstats_stats,
            }
            mode_counts: dict[str, int] = defaultdict(int)
            for candidate in candidates:
                mode_counts[str(candidate.model_mode)] += 1

            summary = {
                'current_time_utc': now_utc.isoformat(),
                'current_time_local': now_local.isoformat(),
                'app_timezone': self.settings.app_timezone,
                'matches_seen': len(filtered_matches),
                'matches_before_publish_window': len(deduped_matches),
                'matches_with_offers': sum(1 for match in filtered_matches if merged_offers.get(match.match_key)),
                'contexts_built': len(contexts),
                'candidates': len(candidates),
                'published': published_count,
                'published_to_telegram': sent_messages,
                'dry_run': self.settings.publish_dry_run,
                'state_path': self.settings.state_path,
                'debug_path': self.settings.debug_path,
                'storage_export_dir': self.settings.storage_export_dir,
                'filtering': filtering,
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
                },
                'rejections': rejections,
                'candidate_modes': dict(mode_counts),
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
                    },
                    'source_previews': {
                        'bookies_bootstrap': bootstrap_preview,
                        'odds_api_io': odds_io_preview,
                        'bookies_api': bookies_preview,
                        'sstats': sstats_preview,
                    },
                    'sample_matches': [self._serialize_match(item) for item in filtered_matches[:25]],
                    'sample_offers': self._serialize_offers(merged_offers, limit=25),
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

    async def _fetch_matches(self) -> tuple[list[Match], dict[str, Any], dict[str, Any]]:
        matches, stats, preview = await self._fetch_provider(
            self.bookies_bootstrap,
            'fetch_matches',
            empty_data=[],
        )
        return list(matches or []), stats, preview

    async def _fetch_provider(self, provider: Any | None, method_name: str, *args: Any, empty_data: Any) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        if provider is None or not hasattr(provider, method_name):
            return empty_data, {'enabled': False}, {}
        result = await getattr(provider, method_name)(*args)
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

    @staticmethod
    def _dedupe_matches(matches: list[Match]) -> list[Match]:
        unique: dict[str, Match] = {}
        for match in matches:
            unique.setdefault(match.match_key, match)
        return list(unique.values())

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

    @staticmethod
    def _serialize_candidate(item: CandidateBet) -> dict[str, Any]:
        row = asdict(item)
        row['commence_time'] = item.commence_time.isoformat()
        return row

    @staticmethod
    def _serialize_match(match: Match) -> dict[str, Any]:
        return {
            'match_key': match.match_key,
            'sport': match.sport_key,
            'league': match.league_name,
            'home': match.home_team,
            'away': match.away_team,
            'commence_time': match.commence_time.isoformat(),
        }

    @staticmethod
    def _serialize_offers(mapping: dict[str, list[Offer]], limit: int = 25) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for match_key, offers in mapping.items():
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
                    }
                )
                if len(rows) >= limit:
                    return rows
        return rows
