from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.providers.bookies_api import BookiesApiProvider
from app.providers.bookies_bootstrap import BookiesBootstrapProvider
from app.providers.odds_api_io import OddsApiIoProvider
from app.providers.sstats import SStatsContextProvider
from app.schemas import CandidateBet
from app.services.model import CandidateFactory
from app.services.normalizer import dedupe_matches, merge_offers
from app.services.telegram import TelegramPublisher
from app.state import JsonStateStore


class PredictionRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.odds_api_io = OddsApiIoProvider(settings)
        self.bookies_api = BookiesApiProvider(settings)
        self.bookies_bootstrap = BookiesBootstrapProvider(settings)
        self.sstats = SStatsContextProvider(settings)
        self.factory = CandidateFactory(settings)
        self.telegram = TelegramPublisher(settings)
        self.state = JsonStateStore(settings.state_path, settings.debug_path)

    async def run_once(self) -> dict[str, Any]:
        started_at = datetime.now(UTC)
        started_at_iso = started_at.isoformat()
        now_local = started_at.astimezone(self.settings.tzinfo)
        self.state.save_run('running', summary={'started_at': started_at_iso})

        try:
            matches, bootstrap_stats, bootstrap_preview = await self.bookies_bootstrap.fetch_matches()
            matches = dedupe_matches(matches)

            odds_api_io_offers, odds_io_stats, odds_io_preview = await self.odds_api_io.fetch_offers(matches)
            bookies_api_offers, bookies_stats, bookies_preview = await self.bookies_api.fetch_offers(
                matches,
                existing_offer_maps={
                    'odds_api_io': odds_api_io_offers,
                },
            )
            merged_offers = merge_offers(self.settings, {}, odds_api_io_offers, bookies_api_offers)
            contexts, sstats_stats, sstats_preview = await self.sstats.fetch_context(matches)
            candidates, rejections, model_debug = self.factory.build_candidates(matches, merged_offers, contexts)

            sent_messages = 0
            telegram_payloads: list[str] = []
            if candidates:
                publish_result = await self.telegram.publish(candidates)
                if isinstance(publish_result, tuple):
                    if len(publish_result) >= 2:
                        sent_messages = int(publish_result[0] or 0)
                        telegram_payloads = list(publish_result[1] or [])
                    elif len(publish_result) == 1:
                        sent_messages = int(publish_result[0] or 0)
                elif isinstance(publish_result, int):
                    sent_messages = publish_result

            published_count = self.state.store_candidates(candidates, telegram_sent=sent_messages > 0)

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
                'current_time_utc': started_at_iso,
                'current_time_local': now_local.isoformat(),
                'app_timezone': self.settings.app_timezone,
                'matches_seen': len(matches),
                'matches_with_offers': sum(1 for match in matches if merged_offers.get(match.match_key)),
                'contexts_built': len(contexts),
                'candidates': len(candidates),
                'published': published_count,
                'published_to_telegram': sent_messages,
                'dry_run': self.settings.publish_dry_run,
                'state_path': self.settings.state_path,
                'debug_path': self.settings.debug_path,
                'storage_export_dir': self.settings.storage_export_dir,
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
                        'enable_bookies_api': self.settings.bookies_api_enabled,
                        'bookies_api_use_for_backfill_only': self.settings.bookies_api_use_for_backfill_only,
                        'bookies_api_sports': self.settings.bookies_api_sports,
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
                        for match in matches[:25]
                    ],
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

    @staticmethod
    def _serialize_candidate(item: CandidateBet) -> dict[str, Any]:
        row = asdict(item)
        row['commence_time'] = item.commence_time.isoformat()
        return row

    @staticmethod
    def _serialize_offers(mapping: dict[str, list[Any]], limit: int = 25) -> list[dict[str, Any]]:
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
