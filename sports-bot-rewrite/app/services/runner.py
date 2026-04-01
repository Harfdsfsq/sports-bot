from __future__ import annotations

from app.config import Settings
from app.providers.odds_api_io import OddsApiIoProvider
from app.providers.sstats import SStatsContextProvider
from app.providers.the_odds_api import TheOddsEventsProvider
from app.schemas import CandidateBet
from app.services.model import ValueModel
from app.services.normalizer import dedupe_matches
from app.services.telegram import TelegramPublisher
from app.state import JsonStateStore


class PredictionRunner:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.events_provider = TheOddsEventsProvider(settings)
        self.odds_provider = OddsApiIoProvider(settings)
        self.context_provider = SStatsContextProvider(settings)
        self.model = ValueModel(settings)
        self.telegram = TelegramPublisher(settings)
        self.state = JsonStateStore(settings.state_path)

    async def run_once(self) -> dict:
        try:
            raw_matches = await self.events_provider.fetch_matches()
            matches = dedupe_matches(raw_matches)
            offers = await self.odds_provider.fetch_offers(matches)
            contexts = await self.context_provider.fetch_context(matches)
            candidates = self.model.build_candidates(matches, offers, contexts)
            fresh_candidates = self._remove_already_published(candidates)
            message = await self.telegram.publish(fresh_candidates)
            stored = self.state.store_candidates(
                fresh_candidates,
                telegram_sent=bool(message and not self.settings.publish_dry_run),
            )
            summary = {
                'matches_seen': len(matches),
                'matches_with_offers': len(offers),
                'contexts_built': len(contexts),
                'candidates': len(candidates),
                'published': stored,
                'dry_run': self.settings.publish_dry_run,
                'state_path': self.settings.state_path,
            }
            self.state.save_run(status='success', summary=summary)
            return summary
        except Exception as exc:
            self.state.save_run(status='failed', error_text=str(exc))
            raise

    def _remove_already_published(self, candidates: list[CandidateBet]) -> list[CandidateBet]:
        return [item for item in candidates if not self.state.has_published(item)]
