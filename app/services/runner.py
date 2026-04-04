from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.providers.bookies_api import BookiesApiProvider
from app.providers.bookies_bootstrap import BookiesBootstrapProvider
from app.providers.odds_api_io import OddsApiIoProvider
from app.providers.sstats import SStatsContextProvider
from app.schemas import CandidateBet, Match
from app.services.normalizer import dedupe_matches, merge_offers
from app.services.telegram import TelegramPublisher
from app.state import JsonStateStore

try:
    from app.services.model import CandidateFactory as _Factory
except ImportError:
    from app.services.model import ValueModel as _Factory


class PredictionRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bookies_bootstrap = BookiesBootstrapProvider(settings)
        self.odds_api_io = OddsApiIoProvider(settings)
        self.bookies_api = BookiesApiProvider(settings)
        self.sstats = SStatsContextProvider(settings)
        self.factory = _Factory(settings)
        self.telegram = TelegramPublisher(settings)
        self.state = self._build_state_store()

    def _build_state_store(self):
        try:
            return JsonStateStore(self.settings.state_path, self.settings.debug_path)
        except TypeError:
            return JsonStateStore(self.settings.state_path)

    @staticmethod
    def _unpack_result(result: Any, empty_primary: Any) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        if isinstance(result, tuple):
            if len(result) == 3:
                return result[0], result[1], result[2]
            if len(result) == 2:
                return result[0], result[1], {}
            if len(result) == 1:
                return result[0], {}, {}
        if result is None:
            return empty_primary, {}, {}
        return result, {}, {}

    def _filter_matches(self, matches: list[Match]) -> tuple[list[Match], dict[str, Any]]:
        now_utc = datetime.now(UTC)
        now_local = now_utc.astimezone(self.settings.tzinfo)
        min_lead = timedelta(minutes=self.settings.min_kickoff_lead_minutes)
        max_window = timedelta(hours=self.settings.publish_window_hours)

        filtered: list[Match] = []
        skipped_started = 0
        skipped_too_soon = 0
        skipped_outside_window = 0

        for match in matches:
            commence = match.commence_time
            if commence.tzinfo is None:
                commence = commence.replace(tzinfo=UTC)
            else:
                commence = commence.astimezone(UTC)

            delta = commence - now_utc
            if delta.total_seconds() <= 0:
                skipped_started += 1
                continue
            if delta < min_lead:
                skipped_too_soon += 1
                continue
            if delta > max_window:
                skipped_outside_window += 1
                continue
            filtered.append(match)

        info = {
            'total_before': len(matches),
            'total_after': len(filtered),
            'skipped_started': skipped_started,
            'skipped_too_soon': skipped_too_soon,
            'skipped_outside_window': skipped_outside_window,
            'publish_window_hours': self.settings.publish_window_hours,
            'min_kickoff_lead_minutes': self.settings.min_kickoff_lead_minutes,
            'now_utc': now_utc.isoformat(),
            'now_local': now_local.isoformat(),
        }
        return filtered, info

    async def run_once(self) -> dict[str, Any]:
        started_at = datetime.now(UTC).isoformat()
        if hasattr(self.state, 'save_run'):
            self.state.save_run('running', summary={'started_at': started_at})

        try:
            now_utc = datetime.now(UTC)
            now_local = now_utc.astimezone(self.settings.tzinfo)

            bootstrap_matches, bootstrap_stats, bootstrap_preview = self._unpack_result(
                await self.bookies_bootstrap.fetch_matches(),
                [],
            )
            matches = dedupe_matches(bootstrap_matches or [])
            filtered_matches, filtering = self._filter_matches(matches)

            odds_api_io_offers, odds_io_stats, odds_io_preview = self._unpack_result(
                await self.odds_api_io.fetch_offers(filtered_matches),
                {},
            )
            bookies_api_offers, bookies_stats, bookies_preview = self._unpack_result(
                await self.bookies_api.fetch_offers(
                    filtered_matches,
                    existing_offer_maps={'odds_api_io': odds_api_io_offers},
                ),
                {},
            )
            merged_offers = merge_offers(self.settings, odds_api_io_offers, bookies_api_offers)

            contexts, sstats_stats, sstats_preview = self._unpack_result(
                await self.sstats.fetch_context(filtered_matches),
                {},
            )

            built = self.factory.build_candidates(filtered_matches, merged_offers, contexts)
            if isinstance(built, tuple):
                if len(built) == 3:
                    candidates, rejections, model_debug = built
                elif len(built) == 2:
                    candidates, rejections = built
                    model_debug = {}
                else:
                    candidates = list(built[0])
                    rejections = {}
                    model_debug = {}
            else:
                candidates = built or []
                rejections = {}
                model_debug = {}

            sent_messages, telegram_payloads = await self.telegram.publish(candidates)

            try:
                published_count = self.state.store_candidates(candidates, telegram_sent=sent_messages > 0)
            except TypeError:
                published_count = self.state.store_candidates(candidates)

            source_stats = {
                'bookies_bootstrap': bootstrap_stats or {},
                'odds_api_io': odds_io_stats or {},
                'bookies_api': bookies_stats or {},
                'sstats': sstats_stats or {},
            }

            mode_counts: dict[str, int] = defaultdict(int)
            for candidate in candidates:
                mode_counts[str(getattr(candidate, 'model_mode', 'unknown'))] += 1

            summary = {
                'current_time_utc': now_utc.isoformat(),
                'current_time_local': now_local.isoformat(),
                'app_timezone': self.settings.app_timezone,
                'matches_seen': len(filtered_matches),
                'matches_before_publish_window': len(matches),
                'matches_with_offers': sum(1 for match in filtered_matches if merged_offers.get(match.match_key)),
                'contexts_built': len(contexts),
                'candidates': len(candidates),
                'published': published_count,
                'published_to_telegram': sent_messages,
                'stored_candidates': published_count,
                'telegram_messages_sent': sent_messages,
                'dry_run': self.settings.publish_dry_run,
                'state_path': self.settings.state_path,
                'debug_path': self.settings.debug_path,
                'storage_export_dir': self.settings.storage_export_dir,
                'filtering': filtering,
                'source_stats': source_stats,
                'mapping': {
                    'matched_exact': (odds_io_stats or {}).get('matched_exact', 0) + (bookies_stats or {}).get('matched_exact', 0),
                    'matched_loose': (odds_io_stats or {}).get('matched_loose', 0) + (bookies_stats or {}).get('matched_loose', 0),
                    'matched_fuzzy': (odds_io_stats or {}).get('matched_fuzzy', 0) + (bookies_stats or {}).get('matched_fuzzy', 0),
                    'unmatched_offer_events': (odds_io_stats or {}).get('unmatched_offer_events', 0) + (bookies_stats or {}).get('unmatched_offer_events', 0),
                    'sstats_exact': (sstats_stats or {}).get('matched_exact', 0),
                    'sstats_loose': (sstats_stats or {}).get('matched_loose', 0),
                    'sstats_fuzzy': (sstats_stats or {}).get('matched_fuzzy', 0),
                    'sstats_unmatched_rows': (sstats_stats or {}).get('unmatched_rows', 0),
                },
                'rejections': rejections or {},
                'candidate_modes': dict(mode_counts),
            }

            debug_payload = {
                'created_at': datetime.now(UTC).isoformat(),
                'summary': summary,
                'settings': {
                    'run_sports': self.settings.run_sports,
                    'run_days_ahead': self.settings.run_days_ahead,
                    'publish_window_hours': self.settings.publish_window_hours,
                    'min_kickoff_lead_minutes': self.settings.min_kickoff_lead_minutes,
                    'target_bookmakers': self.settings.target_bookmakers,
                    'consensus_bookmakers': self.settings.consensus_bookmakers,
                    'publish_dry_run': self.settings.publish_dry_run,
                    'enable_bookies_api': self.settings.bookies_api_enabled,
                },
                'source_previews': {
                    'bookies_bootstrap': bootstrap_preview,
                    'odds_api_io': odds_io_preview,
                    'bookies_api': bookies_preview,
                    'sstats': sstats_preview,
                },
                'sample_matches': [
                    {
                        'match_key': match.match_key,
                        'sport': match.sport_key,
                        'league': match.league_name,
                        'home': match.home_team,
                        'away': match.away_team,
                        'commence_time': match.commence_time.isoformat(),
                        'offers': len(merged_offers.get(match.match_key) or []),
                        'has_context': match.match_key in contexts,
                    }
                    for match in filtered_matches[:25]
                ],
                'sample_offers': self._serialize_offers(merged_offers, limit=25),
                'model_debug': model_debug or {},
                'candidates': [self._serialize_candidate(item) for item in candidates[:25]],
                'telegram_messages': telegram_payloads,
            }

            if hasattr(self.state, 'write_debug'):
                self.state.write_debug(debug_payload)
            elif self.settings.debug_path:
                import json
                from pathlib import Path

                debug_path = Path(self.settings.debug_path)
                debug_path.parent.mkdir(parents=True, exist_ok=True)
                debug_path.write_text(json.dumps(debug_payload, ensure_ascii=False, indent=2), encoding='utf-8')

            if hasattr(self.state, 'save_run'):
                self.state.save_run('ok', summary=summary)
            return summary

        except Exception as exc:
            error_text = f'{type(exc).__name__}: {exc}'
            if hasattr(self.state, 'save_run'):
                self.state.save_run('error', error_text=error_text)
            raise

    @staticmethod
    def _serialize_candidate(item: CandidateBet) -> dict[str, Any]:
        if is_dataclass(item):
            row = asdict(item)
        else:
            row = dict(item)
        commence = getattr(item, 'commence_time', None)
        if commence is not None:
            row['commence_time'] = commence.isoformat()
        return row

    @staticmethod
    def _serialize_offers(mapping: dict[str, list[Any]], limit: int = 25) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for match_key, offers in mapping.items():
            for offer in offers:
                rows.append(
                    {
                        'match_key': match_key,
                        'source': getattr(offer, 'source', None),
                        'bookmaker': getattr(offer, 'bookmaker', None),
                        'family': getattr(offer, 'family', None),
                        'selection': getattr(offer, 'selection', None),
                        'price': getattr(offer, 'price', None),
                        'point': getattr(offer, 'point', None),
                        'team_side': getattr(offer, 'team_side', None),
                    }
                )
                if len(rows) >= limit:
                    return rows
        return rows
