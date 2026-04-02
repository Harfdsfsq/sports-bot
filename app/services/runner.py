from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from typing import Any

from app.config import Settings
from app.providers.api_football import ApiFootballContextProvider
from app.providers.bookies_api import BookiesApiProvider
from app.providers.bzzoiro import BzzoiroContextProvider
from app.providers.bookies_bootstrap import BookiesBootstrapProvider
from app.providers.odds_api_io import OddsApiIoProvider
from app.providers.sstats import SStatsContextProvider
from app.providers.the_odds_api import TheOddsApiProvider
from app.schemas import CandidateBet
from app.services.model import CandidateFactory
from app.services.normalizer import dedupe_matches, merge_offers
from app.services.sheet_export import SheetExportService
from app.services.telegram import TelegramPublisher
from app.state import JsonStateStore


class PredictionRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.the_odds = TheOddsApiProvider(settings)
        self.odds_api_io = OddsApiIoProvider(settings)
        self.bookies_api = BookiesApiProvider(settings)
        self.bookies_bootstrap = BookiesBootstrapProvider(settings)
        self.sstats = SStatsContextProvider(settings)
        self.bzzoiro = BzzoiroContextProvider(settings)
        self.api_football = ApiFootballContextProvider(settings)
        self.factory = CandidateFactory(settings)
        self.telegram = TelegramPublisher(settings)
        self.sheet_export = SheetExportService(settings)
        self.state = JsonStateStore(settings.state_path, settings.debug_path)

    async def run_once(self) -> dict[str, Any]:
        started_at = datetime.now(UTC).isoformat()
        self.state.save_run("running", summary={"started_at": started_at})
        try:
            the_odds_snapshot = await self.the_odds.fetch()
            matches = dedupe_matches(the_odds_snapshot.get("matches") or [])
            the_odds_offers = the_odds_snapshot.get("offers_by_match") or {}

            bootstrap_stats: dict[str, Any] = {
                "enabled": bool(self.settings.bookies_api_enabled),
                "used_as_primary_source": False,
                "requests": 0,
                "response_errors": 0,
                "events_fetched": 0,
                "matches_built": 0,
                "event_http_statuses": [],
                "payload_shapes": [],
                "last_body_preview": None,
            }
            bootstrap_preview: dict[str, Any] = {"sample_events": []}

            if not matches and self.settings.bookies_api_enabled:
                bootstrap_matches, bootstrap_stats, bootstrap_preview = await self.bookies_bootstrap.fetch_matches()
                if bootstrap_matches:
                    matches = dedupe_matches(bootstrap_matches)
                    bootstrap_stats["used_as_primary_source"] = True

            matches_before_publish_window = len(matches)
            matches = self._filter_matches_for_publish_window(matches)

            odds_api_io_offers, odds_io_stats, odds_io_preview = await self.odds_api_io.fetch_offers(matches)
            bookies_api_offers, bookies_stats, bookies_preview = await self.bookies_api.fetch_offers(
                matches,
                existing_offer_maps={
                    "the_odds_api": the_odds_offers,
                    "odds_api_io": odds_api_io_offers,
                },
            )
            merged_offers = merge_offers(self.settings, the_odds_offers, odds_api_io_offers, bookies_api_offers)
            sstats_contexts, sstats_stats, sstats_preview = await self.sstats.fetch_context(matches)
            bzzoiro_contexts, bzzoiro_stats, bzzoiro_preview = await self.bzzoiro.fetch_context(matches)
            api_football_contexts, api_football_stats, api_football_preview = await self.api_football.fetch_context(matches)
            contexts = self._merge_contexts(sstats_contexts, bzzoiro_contexts, api_football_contexts)
            candidates, rejections, model_debug = self.factory.build_candidates(matches, merged_offers, contexts)

            sent_messages = 0
            telegram_payloads: list[str] = []
            if candidates:
                sent_messages, telegram_payloads = await self.telegram.publish(candidates)
            stored_count = self.state.store_candidates(candidates, telegram_sent=sent_messages > 0)
            telegram_pick_count = len(candidates) if (telegram_payloads and not self.settings.publish_dry_run) else 0
            published_count = telegram_pick_count or stored_count

            source_stats = {
                "the_odds_api": the_odds_snapshot.get("stats") or {},
                "bookies_bootstrap": bootstrap_stats,
                "odds_api_io": odds_io_stats,
                "bookies_api": bookies_stats,
                "sstats": sstats_stats,
                "bzzoiro": bzzoiro_stats,
                "api_football": api_football_stats,
            }

            mode_counts: dict[str, int] = defaultdict(int)
            for candidate in candidates:
                mode_counts[str(candidate.model_mode)] += 1

            summary = {
                "matches_seen": len(matches),
                "matches_before_publish_window": matches_before_publish_window,
                "matches_with_offers": sum(1 for match in matches if merged_offers.get(match.match_key)),
                "contexts_built": len(contexts),
                "candidates": len(candidates),
                "published": published_count,
                "published_to_telegram": telegram_pick_count,
                "stored_candidates": stored_count,
                "telegram_messages_sent": sent_messages,
                "dry_run": self.settings.publish_dry_run,
                "state_path": self.settings.state_path,
                "debug_path": self.settings.debug_path,
                "source_stats": source_stats,
                "mapping": {
                    "matched_exact": odds_io_stats.get("matched_exact", 0) + bookies_stats.get("matched_exact", 0),
                    "matched_loose": odds_io_stats.get("matched_loose", 0) + bookies_stats.get("matched_loose", 0),
                    "matched_fuzzy": odds_io_stats.get("matched_fuzzy", 0) + bookies_stats.get("matched_fuzzy", 0),
                    "unmatched_offer_events": odds_io_stats.get("unmatched_offer_events", 0) + bookies_stats.get("unmatched_offer_events", 0),
                    "sstats_exact": sstats_stats.get("matched_exact", 0),
                    "sstats_loose": sstats_stats.get("matched_loose", 0),
                    "sstats_fuzzy": sstats_stats.get("matched_fuzzy", 0),
                    "sstats_unmatched_rows": sstats_stats.get("unmatched_rows", 0),
                },
                "rejections": rejections,
                "candidate_modes": dict(mode_counts),
            }

            sheet_export_info = self.sheet_export.write(candidates, matches=matches, summary=summary)
            summary["sheet_export"] = sheet_export_info

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
                        "enable_bookies_api": self.settings.bookies_api_enabled,
                        "bookies_api_use_for_backfill_only": self.settings.bookies_api_use_for_backfill_only,
                        "bookies_api_sports": self.settings.bookies_api_sports,
                    },
                    "source_previews": {
                        "the_odds_api": the_odds_snapshot.get("preview") or {},
                        "bookies_bootstrap": bootstrap_preview,
                        "odds_api_io": odds_io_preview,
                        "bookies_api": bookies_preview,
                        "sstats": sstats_preview,
                        "bzzoiro": bzzoiro_preview,
                        "api_football": api_football_preview,
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
            self.state.write_debug({"created_at": datetime.now(UTC).isoformat(), "error": error_text})
            raise

    def _filter_matches_for_publish_window(self, matches: list[Any]) -> list[Any]:
        if not matches:
            return []
        now = datetime.now(UTC)
        cutoff = now + timedelta(hours=max(1, int(self.settings.publish_window_hours or 48)))
        filtered = [match for match in matches if now <= match.commence_time <= cutoff]
        return filtered or matches

    def _merge_contexts(self, *maps: dict[str, Any]) -> dict[str, Any]:
        merged: dict[str, Any] = {}
        for mapping in maps:
            for match_key, context in (mapping or {}).items():
                current = merged.get(match_key)
                if current is None or self._context_score(context) > self._context_score(current):
                    merged[match_key] = context
        return merged

    @staticmethod
    def _context_score(context: Any) -> tuple[float, int, int, int]:
        expected = int(getattr(context, "expected_home", None) is not None) + int(getattr(context, "expected_away", None) is not None)
        win_probs = int(getattr(context, "home_win_probability", None) is not None) + int(getattr(context, "away_win_probability", None) is not None)
        details = len(getattr(context, "details", {}) or {})
        return (float(getattr(context, "confidence", 0.0) or 0.0), expected, win_probs, details)

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
