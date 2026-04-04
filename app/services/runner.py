from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.config import Settings
from app.providers.bookies_api import BookiesApiProvider
from app.providers.bookies_bootstrap import BookiesBootstrapProvider
from app.providers.odds_api_io import OddsApiIoProvider
from app.providers.sstats import SStatsContextProvider
from app.schemas import CandidateBet, Match
from app.services.model import CandidateFactory
from app.services.normalizer import dedupe_matches, merge_offers
from app.services.telegram import TelegramPublisher
from app.state import JsonStateStore


class PredictionRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bookies_bootstrap = BookiesBootstrapProvider(settings)
        self.odds_api_io = OddsApiIoProvider(settings)
        self.bookies_api = BookiesApiProvider(settings)
        self.sstats = SStatsContextProvider(settings)
        self.factory = CandidateFactory(settings)
        self.telegram = TelegramPublisher(settings)
        self.state = JsonStateStore(settings.state_path, settings.debug_path)

    async def run_once(self) -> dict[str, Any]:
        now_utc = datetime.now(UTC)
        now_local = now_utc.astimezone(self.settings.tzinfo)
        self.state.save_run(
            "running",
            summary={
                "started_at": now_utc.isoformat(),
                "current_time_utc": now_utc.isoformat(),
                "current_time_local": now_local.isoformat(),
                "app_timezone": self.settings.app_timezone,
            },
        )

        try:
            matches_before: list[Match] = []
            bootstrap_stats: dict[str, Any] = {"enabled": bool(self.settings.bookies_bootstrap_enabled)}
            bootstrap_preview: dict[str, Any] = {}
            if self.settings.bookies_bootstrap_enabled:
                fetched = await self.bookies_bootstrap.fetch_matches()
                matches_before, bootstrap_stats, bootstrap_preview = self._normalize_provider_result(fetched, default_stats=bootstrap_stats)

            matches_before = dedupe_matches(matches_before)
            filtered_matches, filtering = self._filter_matches(matches_before, now_utc)

            odds_io_offers: dict[str, list[Any]] = {}
            odds_io_stats: dict[str, Any] = {"enabled": bool(self.settings.enable_odds_api_io)}
            odds_io_preview: dict[str, Any] = {}
            if self.settings.enable_odds_api_io:
                fetched = await self.odds_api_io.fetch_offers(filtered_matches)
                odds_io_offers, odds_io_stats, odds_io_preview = self._normalize_provider_result(
                    fetched,
                    default_data={},
                    default_stats=odds_io_stats,
                )

            bookies_offers: dict[str, list[Any]] = {}
            bookies_stats: dict[str, Any] = {"enabled": bool(self.settings.bookies_api_enabled)}
            bookies_preview: dict[str, Any] = {}
            if self.settings.bookies_api_enabled:
                fetched = await self.bookies_api.fetch_offers(
                    filtered_matches,
                    existing_offer_maps={"odds_api_io": odds_io_offers},
                )
                bookies_offers, bookies_stats, bookies_preview = self._normalize_provider_result(
                    fetched,
                    default_data={},
                    default_stats=bookies_stats,
                )

            merged_offers = merge_offers(self.settings, {}, odds_io_offers, bookies_offers)

            contexts: dict[str, Any] = {}
            sstats_stats: dict[str, Any] = {"enabled": bool(self.settings.sstats_enabled and self.settings.enable_sstats_context)}
            sstats_preview: dict[str, Any] = {}
            if self.settings.sstats_enabled and self.settings.enable_sstats_context:
                fetched = await self.sstats.fetch_context(filtered_matches)
                contexts, sstats_stats, sstats_preview = self._normalize_provider_result(
                    fetched,
                    default_data={},
                    default_stats=sstats_stats,
                )

            candidates, rejections, model_debug = self.factory.build_candidates(filtered_matches, merged_offers, contexts)
            candidates = candidates[: max(1, self.settings.max_picks_per_run)]

            sent_messages, telegram_payloads = await self.telegram.publish(candidates)
            published_count = self.state.store_candidates(candidates, telegram_sent=sent_messages > 0)

            exports = self._write_exports(filtered_matches, candidates, now_local)

            mode_counts: dict[str, int] = defaultdict(int)
            for candidate in candidates:
                mode_counts[str(candidate.model_mode)] += 1

            summary = {
                "current_time_utc": now_utc.isoformat(),
                "current_time_local": now_local.isoformat(),
                "app_timezone": self.settings.app_timezone,
                "matches_seen": len(filtered_matches),
                "matches_before_publish_window": len(matches_before),
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
                    "created_at": now_utc.isoformat(),
                    "summary": summary,
                    "settings": {
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
            self.state.write_debug({"created_at": now_utc.isoformat(), "error": error_text})
            raise

    def _filter_matches(self, matches: list[Match], now_utc: datetime) -> tuple[list[Match], dict[str, Any]]:
        now_local = now_utc.astimezone(self.settings.tzinfo)
        lead_cutoff = now_utc + timedelta(minutes=max(0, self.settings.min_kickoff_lead_minutes))
        window_cutoff = now_utc + timedelta(hours=max(1, self.settings.publish_window_hours))

        filtered: list[Match] = []
        skipped_started = 0
        skipped_too_soon = 0
        skipped_outside_window = 0
        for match in matches:
            kickoff = match.commence_time.astimezone(UTC)
            if kickoff <= now_utc:
                skipped_started += 1
                continue
            if kickoff < lead_cutoff:
                skipped_too_soon += 1
                continue
            if kickoff > window_cutoff:
                skipped_outside_window += 1
                continue
            filtered.append(match)

        return filtered, {
            "total_before": len(matches),
            "total_after": len(filtered),
            "skipped_started": skipped_started,
            "skipped_too_soon": skipped_too_soon,
            "skipped_outside_window": skipped_outside_window,
            "publish_window_hours": self.settings.publish_window_hours,
            "min_kickoff_lead_minutes": self.settings.min_kickoff_lead_minutes,
            "now_utc": now_utc.isoformat(),
            "now_local": now_local.isoformat(),
        }

    def _write_exports(self, matches: list[Match], candidates: list[CandidateBet], now_local: datetime) -> dict[str, str]:
        base = Path(self.settings.storage_export_dir)
        day_dir = base / now_local.strftime("%Y-%m-%d")
        day_dir.mkdir(parents=True, exist_ok=True)
        stamp = now_local.strftime("%H%M%S")

        matches_rows = [self._match_row(match) for match in matches]
        picks_rows = [self._candidate_row(candidate) for candidate in candidates]

        paths = {
            "matches_json": day_dir / f"{stamp}-matches.json",
            "matches_csv": day_dir / f"{stamp}-matches.csv",
            "picks_json": day_dir / f"{stamp}-picks.json",
            "picks_csv": day_dir / f"{stamp}-picks.csv",
            "latest_matches_json": base / "latest-matches.json",
            "latest_matches_csv": base / "latest-matches.csv",
            "latest_picks_json": base / "latest-picks.json",
            "latest_picks_csv": base / "latest-picks.csv",
        }

        self._write_json(paths["matches_json"], matches_rows)
        self._write_json(paths["picks_json"], picks_rows)
        self._write_json(paths["latest_matches_json"], matches_rows)
        self._write_json(paths["latest_picks_json"], picks_rows)
        self._write_csv(paths["matches_csv"], matches_rows)
        self._write_csv(paths["picks_csv"], picks_rows)
        self._write_csv(paths["latest_matches_csv"], matches_rows)
        self._write_csv(paths["latest_picks_csv"], picks_rows)

        return {key: str(value) for key, value in paths.items()}

    @staticmethod
    def _write_json(path: Path, rows: list[dict[str, Any]]) -> None:
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        fieldnames = list(rows[0].keys())
        with path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    def _match_row(self, match: Match) -> dict[str, Any]:
        return {
            "match_key": match.match_key,
            "sport_key": match.sport_key,
            "league_name": match.league_name,
            "home_team": match.home_team,
            "away_team": match.away_team,
            "commence_time_utc": match.commence_time.astimezone(UTC).isoformat(),
            "commence_time_local": match.commence_time.astimezone(self.settings.tzinfo).isoformat(),
            "tier": match.tier,
            "source": match.source,
        }

    def _candidate_row(self, bet: CandidateBet) -> dict[str, Any]:
        return {
            "match_key": bet.match_key,
            "sport_key": bet.sport_key,
            "league_name": bet.league_name,
            "home_team": bet.home_team,
            "away_team": bet.away_team,
            "commence_time_utc": bet.commence_time.astimezone(UTC).isoformat(),
            "commence_time_local": bet.commence_time.astimezone(self.settings.tzinfo).isoformat(),
            "family": bet.family,
            "selection": bet.selection,
            "odds": bet.odds,
            "fair_odds": bet.fair_odds,
            "market_probability": bet.market_probability,
            "model_probability": bet.model_probability,
            "adjusted_probability": bet.adjusted_probability,
            "edge_pct": bet.edge_pct,
            "ev_pct": bet.ev_pct,
            "confidence": bet.confidence,
            "books_count": bet.books_count,
            "sources_count": bet.sources_count,
            "model_mode": bet.model_mode,
            "point": bet.point,
            "expected_home": bet.expected_home,
            "expected_away": bet.expected_away,
            "reasons": "; ".join(bet.reasons or []),
        }

    @staticmethod
    def _normalize_provider_result(
        result: Any,
        default_data: Any | None = None,
        default_stats: dict[str, Any] | None = None,
    ) -> tuple[Any, dict[str, Any], dict[str, Any]]:
        if isinstance(result, tuple):
            if len(result) == 3:
                return result[0], result[1] or (default_stats or {}), result[2] or {}
            if len(result) == 2:
                return result[0], result[1] or (default_stats or {}), {}
            if len(result) == 1:
                return result[0], default_stats or {}, {}
        return (result if result is not None else default_data), default_stats or {}, {}

    def _serialize_match(self, match: Match, merged_offers: dict[str, list[Any]], contexts: dict[str, Any]) -> dict[str, Any]:
        return {
            "match_key": match.match_key,
            "sport": match.sport_key,
            "league": match.league_name,
            "home": match.home_team,
            "away": match.away_team,
            "commence_time": match.commence_time.isoformat(),
            "offers": len(merged_offers.get(match.match_key) or []),
            "has_context": match.match_key in contexts,
        }

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
