from __future__ import annotations

from dataclasses import asdict
from datetime import UTC, datetime
from typing import Any

from app.config import Settings
from app.providers.odds_api_io import OddsApiIoProvider
from app.providers.sstats import SStatsContextProvider
from app.providers.the_odds_api import TheOddsApiProvider
from app.schemas import CandidateBet
from app.services.model import CandidateFactory
from app.services.normalizer import dedupe_matches, merge_offers
from app.services.telegram import TelegramPublisher
from app.state import JsonStateStore


class PredictionRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.the_odds = TheOddsApiProvider(settings)
        self.odds_api_io = OddsApiIoProvider(settings)
        self.sstats = SStatsContextProvider(settings)
        self.factory = CandidateFactory(settings)
        self.telegram = TelegramPublisher(settings)
        self.state = JsonStateStore(settings.state_path, settings.debug_path)

    async def run_once(self) -> dict[str, Any]:
        started_at = datetime.now(UTC).isoformat()
        self.state.save_run("running", summary={"started_at": started_at})
        try:
            the_odds_snapshot = await self.the_odds.fetch()
            matches = dedupe_matches(the_odds_snapshot.get("matches") or [])
            the_odds_offers = the_odds_snapshot.get("offers_by_match") or {}

            odds_api_io_offers, odds_io_stats, odds_io_preview = await self.odds_api_io.fetch_offers(matches)
            merged_offers = merge_offers(self.settings, the_odds_offers, odds_api_io_offers)

            contexts, sstats_stats, sstats_preview = await self.sstats.fetch_context(matches)
            candidates, rejections, model_debug = self.factory.build_candidates(matches, merged_offers, contexts)

            sent_messages = 0
            telegram_payloads: list[str] = []
            if candidates:
                sent_messages, telegram_payloads = await self.telegram.publish(candidates)
            published_count = self.state.store_candidates(candidates, telegram_sent=sent_messages > 0)

            source_stats = {
                "the_odds_api": the_odds_snapshot.get("stats") or {},
                "odds_api_io": odds_io_stats,
                "sstats": sstats_stats,
            }
            mode_counts: dict[str, int] = defaultdict(int)
            for candidate in candidates:
                mode_counts[str(candidate.model_mode)] += 1
            summary = {
                "matches_seen": len(matches),
                "matches_with_offers": sum(1 for match in matches if merged_offers.get(match.match_key)),
                "contexts_built": len(contexts),
                "candidates": len(candidates),
                "published": published_count,
                "dry_run": self.settings.publish_dry_run,
                "state_path": self.settings.state_path,
                "debug_path": self.settings.debug_path,
                "source_stats": source_stats,
                "mapping": {
                    "matched_exact": odds_io_stats.get("matched_exact", 0),
                    "matched_loose": odds_io_stats.get("matched_loose", 0),
                    "matched_fuzzy": odds_io_stats.get("matched_fuzzy", 0),
                    "unmatched_offer_events": odds_io_stats.get("unmatched_offer_events", 0),
                    "sstats_exact": sstats_stats.get("matched_exact", 0),
                    "sstats_loose": sstats_stats.get("matched_loose", 0),
                    "sstats_fuzzy": sstats_stats.get("matched_fuzzy", 0),
                    "sstats_unmatched_rows": sstats_stats.get("unmatched_rows", 0),
                },
                "rejections": rejections,
                "candidate_modes": dict(mode_counts),
            }

            self.state.write_debug(
                {
                    "created_at": datetime.now(UTC).isoformat(),
                    "summary": summary,
                    "settings": {
                        "run_sports": self.settings.run_sports,
                        "run_days_ahead": self.settings.run_days_ahead,
                        "target_bookmakers": self.settings.target_bookmakers,
                        "consensus_bookmakers": self.settings.consensus_bookmakers,
                        "publish_dry_run": self.settings.publish_dry_run,
                    },
                    "source_previews": {
                        "the_odds_api": the_odds_snapshot.get("preview") or {},
                        "odds_api_io": odds_io_preview,
                        "sstats": sstats_preview,
                    },
                    "sample_matches": [
                        {
                            "match_key": match.match_key,
                            "sport": match.sport_key,
                            "league": match.league_name,
                            "home": match.home_team,
                            "away": match.away_team,
                            "commence_time": match.commence_time.isoformat(),
                            "offers": len(merged_offers.get(match.match_key) or []),
                            "has_context": match.match_key in contexts,
                        }
                        for match in matches[:25]
                    ],
                    "sample_offers": self._serialize_offers(merged_offers, limit=25),
                    "model_debug": model_debug,
                    "candidates": [self._serialize_candidate(item) for item in candidates[:25]],
                    "telegram_messages": telegram_payloads,
                }
            )
            self.state.save_run("ok", summary=summary)
            return summary
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            self.state.save_run("error", error_text=error_text)
            self.state.write_debug({
                "created_at": datetime.now(UTC).isoformat(),
                "error": error_text,
            })
            raise

    @staticmethod
    def _serialize_candidate(item: CandidateBet) -> dict[str, Any]:
        row = asdict(item)
        row["commence_time"] = item.commence_time.isoformat()
        return row

    @staticmethod
    def _serialize_offers(mapping: dict[str, list[Any]], limit: int = 25) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for match_key, offers in mapping.items():
            for offer in offers:
                rows.append(
                    {
                        "match_key": match_key,
                        "source": offer.source,
                        "bookmaker": offer.bookmaker,
                        "family": offer.family,
                        "selection": offer.selection,
                        "price": offer.price,
                        "point": offer.point,
                        "team_side": offer.team_side,
                    }
                )
                if len(rows) >= limit:
                    return rows
        return rows
