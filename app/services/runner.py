from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
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
        now_utc = datetime.now(UTC)
        now_local = now_utc.astimezone(self.settings.tzinfo)
        started_at = now_utc.isoformat()
        self.state.save_run("running", summary={"started_at": started_at})

        try:
            bootstrap_stats: dict[str, Any] = {
                "enabled": bool(self.settings.bookies_bootstrap_enabled and self.settings.bookies_api_enabled),
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
            matches = []
            if self.settings.bookies_bootstrap_enabled and self.settings.bookies_api_enabled:
                matches, bootstrap_stats, bootstrap_preview = await self.bookies_bootstrap.fetch_matches()
                bootstrap_stats["used_as_primary_source"] = bool(matches)

            matches = dedupe_matches(matches)
            filtered_matches, filtering = self._filter_matches(matches, now_utc)

            odds_api_io_offers: dict[str, list[Any]] = {}
            odds_io_stats: dict[str, Any] = {
                "enabled": bool(self.settings.enable_odds_api_io and self.settings.odds_api_io_key),
                "api_key_present": bool(self.settings.odds_api_io_key),
                "event_requests": 0,
                "odds_requests": 0,
                "response_errors": 0,
                "events_fetched": 0,
                "events_matched": 0,
                "matched_exact": 0,
                "matched_loose": 0,
                "matched_fuzzy": 0,
                "unmatched_offer_events": 0,
                "markets_parsed": 0,
                "offers_parsed": 0,
                "event_http_statuses": [],
                "odds_http_statuses": [],
                "payload_shapes": [],
                "bookmakers_seen": 0,
                "last_body_preview": None,
                "simulated_skipped": 0,
            }
            odds_io_preview: dict[str, Any] = {}
            if filtered_matches and self.settings.enable_odds_api_io and self.settings.odds_api_io_key:
                odds_api_io_offers, odds_io_stats, odds_io_preview = await self.odds_api_io.fetch_offers(filtered_matches)

            bookies_api_offers: dict[str, list[Any]] = {}
            bookies_stats: dict[str, Any] = {
                "enabled": bool(self.settings.bookies_api_enabled),
                "candidate_matches": len(filtered_matches),
                "event_requests": 0,
                "odds_requests": 0,
                "events_fetched": 0,
                "events_matched": 0,
                "matched_exact": 0,
                "matched_loose": 0,
                "matched_fuzzy": 0,
                "unmatched_offer_events": 0,
                "markets_parsed": 0,
                "offers_parsed": 0,
                "response_errors": 0,
                "event_http_statuses": [],
                "odds_http_statuses": [],
                "payload_shapes": [],
                "bookmakers_seen": 0,
                "last_body_preview": None,
                "simulated_skipped": 0,
                "task_used": self.settings.bookies_api_odds_task,
                "candidate_matches_limited_to": self.settings.max_matches_for_odds_fetch,
                "odds_fetch_limited_to": self.settings.bookies_api_odds_fetch_limit,
            }
            bookies_preview: dict[str, Any] = {}
            if filtered_matches and self.settings.bookies_api_enabled:
                bookies_api_offers, bookies_stats, bookies_preview = await self.bookies_api.fetch_offers(
                    filtered_matches,
                    existing_offer_maps={
                        "odds_api_io": odds_api_io_offers,
                    },
                )

            merged_offers = merge_offers(self.settings, odds_api_io_offers, bookies_api_offers)

            contexts: dict[str, Any] = {}
            sstats_stats: dict[str, Any] = {
                "enabled": bool(self.settings.sstats_enabled and self.settings.enable_sstats_context and self.settings.sstats_api_key),
                "api_key_present": bool(self.settings.sstats_api_key),
                "requests": 0,
                "response_errors": 0,
                "days_requested": self.settings.run_days_ahead,
                "rows_fetched": 0,
                "contexts_built": 0,
                "matched_exact": 0,
                "matched_loose": 0,
                "matched_fuzzy": 0,
                "unmatched_rows": 0,
                "http_statuses": [],
                "payload_shapes": [],
                "last_body_preview": None,
                "last_url": None,
            }
            sstats_preview: dict[str, Any] = {}
            if filtered_matches and self.settings.sstats_enabled and self.settings.enable_sstats_context and self.settings.sstats_api_key:
                contexts, sstats_stats, sstats_preview = await self.sstats.fetch_context(filtered_matches)

            candidates: list[CandidateBet] = []
            rejections: dict[str, int] = {}
            model_debug: dict[str, Any] = {}
            if filtered_matches:
                candidates, rejections, model_debug = self.factory.build_candidates(filtered_matches, merged_offers, contexts)

            if len(candidates) > self.settings.max_picks_per_run:
                candidates = candidates[: self.settings.max_picks_per_run]

            sent_messages = 0
            telegram_payloads: list[str] = []
            if candidates:
                publish_result = await self.telegram.publish(candidates)
                if isinstance(publish_result, tuple):
                    sent_messages = int(publish_result[0]) if len(publish_result) >= 1 else 0
                    telegram_payloads = list(publish_result[1]) if len(publish_result) >= 2 else []

            published_count = len(candidates)
            if hasattr(self.state, "store_candidates"):
                try:
                    stored = self.state.store_candidates(candidates, telegram_sent=sent_messages > 0)
                    if isinstance(stored, int):
                        published_count = stored
                except Exception:
                    published_count = len(candidates)

            exports = self._export_run(now_local, filtered_matches, candidates)

            mode_counts: dict[str, int] = defaultdict(int)
            for candidate in candidates:
                mode_counts[str(getattr(candidate, "model_mode", "unknown"))] += 1

            summary = {
                "current_time_utc": now_utc.isoformat(),
                "current_time_local": now_local.isoformat(),
                "app_timezone": self.settings.app_timezone,
                "matches_seen": len(filtered_matches),
                "matches_before_publish_window": len(matches),
                "matches_with_offers": sum(1 for match in filtered_matches if merged_offers.get(match.match_key)),
                "contexts_built": len(contexts),
                "candidates": len(candidates),
                "published": published_count,
                "published_to_telegram": sent_messages,
                "dry_run": self.settings.publish_dry_run,
                "state_path": self.settings.state_path,
                "debug_path": self.settings.debug_path,
                "storage_export_dir": self.settings.storage_export_dir,
                "filtering": filtering,
                "source_stats": {
                    "bookies_bootstrap": bootstrap_stats,
                    "odds_api_io": odds_io_stats,
                    "bookies_api": bookies_stats,
                    "sstats": sstats_stats,
                },
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
                "exports": exports,
            }

            self.state.write_debug(
                {
                    "created_at": datetime.now(UTC).isoformat(),
                    "summary": summary,
                    "settings": {
                        "app_timezone": self.settings.app_timezone,
                        "run_sports": self.settings.run_sports,
                        "run_days_ahead": self.settings.run_days_ahead,
                        "publish_window_hours": self.settings.publish_window_hours,
                        "min_kickoff_lead_minutes": self.settings.min_kickoff_lead_minutes,
                        "target_bookmakers": self.settings.target_bookmakers,
                        "consensus_bookmakers": self.settings.consensus_bookmakers,
                        "publish_dry_run": self.settings.publish_dry_run,
                        "bookies_bootstrap_enabled": self.settings.bookies_bootstrap_enabled,
                        "bookies_api_enabled": self.settings.bookies_api_enabled,
                        "enable_odds_api_io": self.settings.enable_odds_api_io,
                        "sstats_enabled": self.settings.sstats_enabled,
                    },
                    "source_previews": {
                        "bookies_bootstrap": bootstrap_preview,
                        "odds_api_io": odds_io_preview,
                        "bookies_api": bookies_preview,
                        "sstats": sstats_preview,
                    },
                    "sample_matches": [self._serialize_match(match, merged_offers, contexts) for match in filtered_matches[:25]],
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

    def _filter_matches(self, matches: list[Any], now_utc: datetime) -> tuple[list[Any], dict[str, Any]]:
        result: list[Any] = []
        skipped_started = 0
        skipped_too_soon = 0
        skipped_outside_window = 0
        max_delta = timedelta(hours=self.settings.publish_window_hours)
        min_delta = timedelta(minutes=self.settings.min_kickoff_lead_minutes)

        for match in matches:
            commence_time = getattr(match, "commence_time", None)
            if commence_time is None:
                skipped_outside_window += 1
                continue
            delta = commence_time - now_utc
            if delta.total_seconds() < 0:
                skipped_started += 1
                continue
            if delta < min_delta:
                skipped_too_soon += 1
                continue
            if delta > max_delta:
                skipped_outside_window += 1
                continue
            result.append(match)

        filtering = {
            "total_before": len(matches),
            "total_after": len(result),
            "skipped_started": skipped_started,
            "skipped_too_soon": skipped_too_soon,
            "skipped_outside_window": skipped_outside_window,
            "publish_window_hours": self.settings.publish_window_hours,
            "min_kickoff_lead_minutes": self.settings.min_kickoff_lead_minutes,
            "now_utc": now_utc.isoformat(),
            "now_local": now_utc.astimezone(self.settings.tzinfo).isoformat(),
        }
        return result, filtering

    def _export_run(self, now_local: datetime, matches: list[Any], candidates: list[CandidateBet]) -> dict[str, str]:
        base_dir = Path(self.settings.storage_export_dir)
        day_dir = base_dir / now_local.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_local.strftime("%H%M%S")

        matches_json = day_dir / f"{stamp}-matches.json"
        matches_csv = day_dir / f"{stamp}-matches.csv"
        picks_json = day_dir / f"{stamp}-picks.json"
        picks_csv = day_dir / f"{stamp}-picks.csv"

        match_rows = [self._plain_match(match) for match in matches]
        pick_rows = [self._plain_candidate(candidate) for candidate in candidates]

        matches_json.write_text(json.dumps(match_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        picks_json.write_text(json.dumps(pick_rows, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_csv(matches_csv, match_rows)
        self._write_csv(picks_csv, pick_rows)

        latest_matches_json = base_dir / "latest-matches.json"
        latest_matches_csv = base_dir / "latest-matches.csv"
        latest_picks_json = base_dir / "latest-picks.json"
        latest_picks_csv = base_dir / "latest-picks.csv"

        latest_matches_json.write_text(matches_json.read_text(encoding="utf-8"), encoding="utf-8")
        latest_matches_csv.write_text(matches_csv.read_text(encoding="utf-8"), encoding="utf-8")
        latest_picks_json.write_text(picks_json.read_text(encoding="utf-8"), encoding="utf-8")
        latest_picks_csv.write_text(picks_csv.read_text(encoding="utf-8"), encoding="utf-8")

        return {
            "matches_json": str(matches_json),
            "matches_csv": str(matches_csv),
            "picks_json": str(picks_json),
            "picks_csv": str(picks_csv),
            "latest_matches_json": str(latest_matches_json),
            "latest_matches_csv": str(latest_matches_csv),
            "latest_picks_json": str(latest_picks_json),
            "latest_picks_csv": str(latest_picks_csv),
        }

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        fieldnames: list[str] = []
        for row in rows:
            for key in row.keys():
                if key not in fieldnames:
                    fieldnames.append(key)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _serialize_match(self, match: Any, merged_offers: dict[str, list[Any]], contexts: dict[str, Any]) -> dict[str, Any]:
        return {
            "match_key": getattr(match, "match_key", ""),
            "sport": getattr(match, "sport_key", ""),
            "league": getattr(match, "league_name", ""),
            "home": getattr(match, "home_team", ""),
            "away": getattr(match, "away_team", ""),
            "commence_time": getattr(match, "commence_time", None).isoformat() if getattr(match, "commence_time", None) else None,
            "offers": len(merged_offers.get(getattr(match, "match_key", ""), [])),
            "has_context": getattr(match, "match_key", "") in contexts,
        }

    @staticmethod
    def _serialize_candidate(item: CandidateBet) -> dict[str, Any]:
        row = asdict(item) if is_dataclass(item) else dict(item)
        if getattr(item, "commence_time", None):
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
                        "source": getattr(offer, "source", None),
                        "bookmaker": getattr(offer, "bookmaker", None),
                        "family": getattr(offer, "family", None),
                        "selection": getattr(offer, "selection", None),
                        "price": getattr(offer, "price", None),
                        "point": getattr(offer, "point", None),
                        "team_side": getattr(offer, "team_side", None),
                    }
                )
                if len(rows) >= limit:
                    return rows
        return rows

    @staticmethod
    def _plain_match(match: Any) -> dict[str, Any]:
        return {
            "match_key": getattr(match, "match_key", ""),
            "source": getattr(match, "source", ""),
            "source_event_id": getattr(match, "source_event_id", ""),
            "sport_key": getattr(match, "sport_key", ""),
            "league_name": getattr(match, "league_name", ""),
            "home_team": getattr(match, "home_team", ""),
            "away_team": getattr(match, "away_team", ""),
            "commence_time": getattr(match, "commence_time", None).isoformat() if getattr(match, "commence_time", None) else None,
        }

    @staticmethod
    def _plain_candidate(candidate: CandidateBet) -> dict[str, Any]:
        row = asdict(candidate) if is_dataclass(candidate) else dict(candidate)
        if row.get("commence_time") and hasattr(row["commence_time"], "isoformat"):
            row["commence_time"] = row["commence_time"].isoformat()
        return row
