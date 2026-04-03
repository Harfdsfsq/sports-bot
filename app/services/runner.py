from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Settings
from app.providers.bookies_api import BookiesApiProvider
from app.providers.bookies_bootstrap import BookiesBootstrapProvider
from app.providers.odds_api_io import OddsApiIoProvider
from app.providers.sstats import SStatsContextProvider
from app.providers.the_odds_api import TheOddsApiProvider
from app.schemas import CandidateBet, Match
from app.services.model import CandidateFactory
from app.services.normalizer import dedupe_matches, merge_offers
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
        self.factory = CandidateFactory(settings)
        self.telegram = TelegramPublisher(settings)
        self.state = JsonStateStore(settings.state_path, settings.debug_path)

    async def run_once(self) -> dict[str, Any]:
        started_at = datetime.now(UTC).isoformat()
        self.state.save_run("running", summary={"started_at": started_at})

        try:
            the_odds_snapshot = await self.the_odds.fetch()
            raw_matches = dedupe_matches(the_odds_snapshot.get("matches") or [])
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

            if not raw_matches and self.settings.bookies_api_enabled:
                bootstrap_matches, bootstrap_stats, bootstrap_preview = await self.bookies_bootstrap.fetch_matches()
                if bootstrap_matches:
                    raw_matches = dedupe_matches(bootstrap_matches)
                    bootstrap_stats["used_as_primary_source"] = True

            now_utc = datetime.now(UTC)
            filtered_matches, filter_stats = self._filter_matches_for_run(raw_matches, now_utc)

            odds_api_io_offers, odds_io_stats, odds_io_preview = await self.odds_api_io.fetch_offers(filtered_matches)
            bookies_api_offers, bookies_stats, bookies_preview = await self.bookies_api.fetch_offers(
                filtered_matches,
                existing_offer_maps={
                    "the_odds_api": the_odds_offers,
                    "odds_api_io": odds_api_io_offers,
                },
            )

            merged_offers = merge_offers(
                self.settings,
                the_odds_offers,
                odds_api_io_offers,
                bookies_api_offers,
            )
            contexts, sstats_stats, sstats_preview = await self.sstats.fetch_context(filtered_matches)
            candidates, rejections, model_debug = self.factory.build_candidates(filtered_matches, merged_offers, contexts)

            sent_messages = 0
            telegram_payloads: list[str] = []
            if candidates:
                sent_messages, telegram_payloads = await self.telegram.publish(candidates)

            published_count = self.state.store_candidates(candidates, telegram_sent=sent_messages > 0)
            self._write_exports(filtered_matches, merged_offers, contexts, candidates)

            source_stats = {
                "the_odds_api": the_odds_snapshot.get("stats") or {},
                "bookies_bootstrap": bootstrap_stats,
                "odds_api_io": odds_io_stats,
                "bookies_api": bookies_stats,
                "sstats": sstats_stats,
            }

            mode_counts: dict[str, int] = defaultdict(int)
            for candidate in candidates:
                mode_counts[str(candidate.model_mode)] += 1

            local_tz = ZoneInfo(self.settings.app_timezone)
            summary = {
                "current_time_utc": now_utc.isoformat(),
                "current_time_local": now_utc.astimezone(local_tz).isoformat(),
                "app_timezone": self.settings.app_timezone,
                "matches_seen": len(filtered_matches),
                "matches_before_publish_window": len(raw_matches),
                "matches_with_offers": sum(1 for match in filtered_matches if merged_offers.get(match.match_key)),
                "contexts_built": len(contexts),
                "candidates": len(candidates),
                "published": published_count,
                "dry_run": self.settings.publish_dry_run,
                "state_path": self.settings.state_path,
                "debug_path": self.settings.debug_path,
                "storage_export_dir": self.settings.storage_export_dir,
                "filtering": filter_stats,
                "source_stats": source_stats,
                "mapping": {
                    "matched_exact": odds_io_stats.get("matched_exact", 0) + bookies_stats.get("matched_exact", 0),
                    "matched_loose": odds_io_stats.get("matched_loose", 0) + bookies_stats.get("matched_loose", 0),
                    "matched_fuzzy": odds_io_stats.get("matched_fuzzy", 0) + bookies_stats.get("matched_fuzzy", 0),
                    "unmatched_offer_events": odds_io_stats.get("unmatched_offer_events", 0)
                    + bookies_stats.get("unmatched_offer_events", 0),
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
                        "publish_window_hours": self.settings.publish_window_hours,
                        "min_kickoff_lead_minutes": self.settings.min_kickoff_lead_minutes,
                        "app_timezone": self.settings.app_timezone,
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
                    },
                    "sample_matches": [
                        {
                            "match_key": match.match_key,
                            "sport": match.sport_key,
                            "league": match.league_name,
                            "home": match.home_team,
                            "away": match.away_team,
                            "commence_time": match.commence_time.isoformat(),
                            "commence_time_local": match.commence_time.astimezone(local_tz).isoformat(),
                            "offers": len(merged_offers.get(match.match_key) or []),
                            "has_context": match.match_key in contexts,
                        }
                        for match in filtered_matches[:25]
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

    def _filter_matches_for_run(self, matches: list[Match], now_utc: datetime) -> tuple[list[Match], dict[str, Any]]:
        publish_deadline = now_utc + timedelta(hours=self.settings.publish_window_hours)
        lead_deadline = now_utc + timedelta(minutes=self.settings.min_kickoff_lead_minutes)

        filtered: list[Match] = []
        skipped_started = 0
        skipped_too_soon = 0
        skipped_outside_window = 0

        for match in matches:
            kickoff_utc = match.commence_time.astimezone(UTC)
            if kickoff_utc <= now_utc:
                skipped_started += 1
                continue
            if kickoff_utc < lead_deadline:
                skipped_too_soon += 1
                continue
            if kickoff_utc > publish_deadline:
                skipped_outside_window += 1
                continue
            filtered.append(match)

        local_tz = ZoneInfo(self.settings.app_timezone)
        return filtered, {
            "total_before": len(matches),
            "total_after": len(filtered),
            "skipped_started": skipped_started,
            "skipped_too_soon": skipped_too_soon,
            "skipped_outside_window": skipped_outside_window,
            "publish_window_hours": self.settings.publish_window_hours,
            "min_kickoff_lead_minutes": self.settings.min_kickoff_lead_minutes,
            "now_utc": now_utc.isoformat(),
            "now_local": now_utc.astimezone(local_tz).isoformat(),
        }

    def _write_exports(
        self,
        matches: list[Match],
        merged_offers: dict[str, list[Any]],
        contexts: dict[str, Any],
        candidates: list[CandidateBet],
    ) -> None:
        export_dir = Path(self.settings.storage_export_dir)
        export_dir.mkdir(parents=True, exist_ok=True)

        local_tz = ZoneInfo(self.settings.app_timezone)
        stamp = datetime.now(UTC).astimezone(local_tz)
        day_dir = export_dir / stamp.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)

        match_rows = [
            {
                "match_key": match.match_key,
                "sport": match.sport_key,
                "league": match.league_name,
                "home": match.home_team,
                "away": match.away_team,
                "kickoff_utc": match.commence_time.astimezone(UTC).isoformat(),
                "kickoff_local": match.commence_time.astimezone(local_tz).isoformat(),
                "offers": len(merged_offers.get(match.match_key) or []),
                "has_context": match.match_key in contexts,
            }
            for match in matches
        ]
        pick_rows = [self._serialize_candidate(item) for item in candidates]

        self._write_json(export_dir / "latest-matches.json", match_rows)
        self._write_json(export_dir / "latest-picks.json", pick_rows)
        self._write_csv(export_dir / "latest-matches.csv", match_rows)
        self._write_csv(export_dir / "latest-picks.csv", pick_rows)

        timestamp_prefix = stamp.strftime("%H%M%S")
        self._write_json(day_dir / f"{timestamp_prefix}-matches.json", match_rows)
        self._write_json(day_dir / f"{timestamp_prefix}-picks.json", pick_rows)
        self._write_csv(day_dir / f"{timestamp_prefix}-matches.csv", match_rows)
        self._write_csv(day_dir / f"{timestamp_prefix}-picks.csv", pick_rows)

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        fieldnames: list[str] = []
        for row in rows:
            for key in row.keys():
                if key not in fieldnames:
                    fieldnames.append(key)
        with path.open("w", encoding="utf-8", newline="") as fh:
            if not fieldnames:
                fh.write("")
                return
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

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
