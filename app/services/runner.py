from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime, timedelta
import csv
import json
from pathlib import Path
from typing import Any

from app.config import Settings
from app.providers.bookies_api import BookiesApiProvider
from app.providers.bookies_bootstrap import BookiesBootstrapProvider
from app.providers.sstats import SStatsContextProvider
from app.schemas import CandidateBet, Match
from app.services.model import CandidateFactory
from app.services.normalizer import dedupe_matches, merge_offers
from app.services.telegram import TelegramPublisher
from app.state import JsonStateStore


class PredictionRunner:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.bookies_api = BookiesApiProvider(settings)
        self.bookies_bootstrap = BookiesBootstrapProvider(settings)
        self.sstats = SStatsContextProvider(settings)
        self.factory = CandidateFactory(settings)
        self.telegram = TelegramPublisher(settings)
        self.state = JsonStateStore(settings.state_path)

    async def run_once(self) -> dict[str, Any]:
        now_utc = datetime.now(UTC)
        now_local = now_utc.astimezone(self.settings.tzinfo)
        self.state.save_run(
            "running",
            summary={
                "current_time_utc": now_utc.isoformat(),
                "current_time_local": now_local.isoformat(),
                "app_timezone": self.settings.app_timezone,
            },
        )

        try:
            bootstrap_matches, bootstrap_stats, bootstrap_preview = await self._fetch_bootstrap_matches()
            all_matches = dedupe_matches(bootstrap_matches)
            filtered_matches, filtering = self._filter_matches(all_matches, now_utc)

            bookies_api_offers, bookies_stats, bookies_preview = await self._fetch_bookies_offers(filtered_matches)
            merged_offers = merge_offers(self.settings, {}, {}, bookies_api_offers)

            contexts, sstats_stats, sstats_preview = await self._fetch_contexts(filtered_matches)
            candidates, rejections, model_debug = self.factory.build_candidates(filtered_matches, merged_offers, contexts)

            sent_messages, telegram_payloads = await self.telegram.publish(candidates)
            stored_candidates = self.state.store_candidates(candidates, telegram_sent=sent_messages > 0)
            export_info = self._write_exports(filtered_matches, candidates, now_local)

            mode_counts: dict[str, int] = defaultdict(int)
            for candidate in candidates:
                mode_counts[str(candidate.model_mode)] += 1

            summary = {
                "current_time_utc": now_utc.isoformat(),
                "current_time_local": now_local.isoformat(),
                "app_timezone": self.settings.app_timezone,
                "matches_seen": len(filtered_matches),
                "matches_before_publish_window": len(all_matches),
                "matches_with_offers": sum(1 for match in filtered_matches if merged_offers.get(match.match_key)),
                "contexts_built": len(contexts),
                "candidates": len(candidates),
                "published": stored_candidates,
                "published_to_telegram": sent_messages,
                "stored_candidates": stored_candidates,
                "telegram_messages_sent": sent_messages,
                "dry_run": self.settings.publish_dry_run,
                "state_path": self.settings.state_path,
                "debug_path": self.settings.debug_path,
                "storage_export_dir": self.settings.storage_export_dir,
                "filtering": filtering,
                "source_stats": {
                    "bookies_bootstrap": bootstrap_stats,
                    "bookies_api": bookies_stats,
                    "sstats": sstats_stats,
                },
                "mapping": {
                    "matched_exact": bookies_stats.get("matched_exact", 0),
                    "matched_loose": bookies_stats.get("matched_loose", 0),
                    "matched_fuzzy": bookies_stats.get("matched_fuzzy", 0),
                    "unmatched_offer_events": bookies_stats.get("unmatched_offer_events", 0),
                    "sstats_exact": sstats_stats.get("matched_exact", 0),
                    "sstats_loose": sstats_stats.get("matched_loose", 0),
                    "sstats_fuzzy": sstats_stats.get("matched_fuzzy", 0),
                    "sstats_unmatched_rows": sstats_stats.get("unmatched_rows", 0),
                },
                "rejections": rejections,
                "candidate_modes": dict(mode_counts),
                "exports": export_info,
            }

            self._write_debug(
                summary=summary,
                all_matches=filtered_matches,
                merged_offers=merged_offers,
                contexts=contexts,
                candidates=candidates,
                model_debug=model_debug,
                telegram_payloads=telegram_payloads,
                source_previews={
                    "bookies_bootstrap": bootstrap_preview,
                    "bookies_api": bookies_preview,
                    "sstats": sstats_preview,
                },
            )
            self.state.save_run("ok", summary=summary)
            return summary
        except Exception as exc:
            error_text = f"{type(exc).__name__}: {exc}"
            self.state.save_run("error", error_text=error_text)
            self._write_debug(summary=None, error_text=error_text)
            raise

    async def _fetch_bootstrap_matches(self) -> tuple[list[Match], dict[str, Any], dict[str, Any]]:
        if not self.settings.bookies_api_enabled:
            return [], {"enabled": False}, {"sample_events": []}

        result = await self.bookies_bootstrap.fetch_matches()
        if isinstance(result, tuple):
            if len(result) == 3:
                return result[0] or [], result[1] or {}, result[2] or {}
            if len(result) == 2:
                return result[0] or [], result[1] or {}, {}
            if len(result) == 1:
                return result[0] or [], {}, {}
        return result or [], {}, {}

    async def _fetch_bookies_offers(self, matches: list[Match]) -> tuple[dict[str, list[Any]], dict[str, Any], dict[str, Any]]:
        if not matches:
            return {}, {"enabled": bool(self.settings.bookies_api_enabled)}, {"sample_events": []}

        try:
            result = await self.bookies_api.fetch_offers(matches, existing_offer_maps={})
        except TypeError:
            result = await self.bookies_api.fetch_offers(matches)

        if isinstance(result, tuple):
            if len(result) == 3:
                return result[0] or {}, result[1] or {}, result[2] or {}
            if len(result) == 2:
                return result[0] or {}, result[1] or {}, {}
            if len(result) == 1:
                return result[0] or {}, {}, {}
        return result or {}, {}, {}

    async def _fetch_contexts(self, matches: list[Match]) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
        if not matches:
            return {}, {"enabled": bool(self.settings.sstats_api_key)}, {"sample_rows": []}

        result = await self.sstats.fetch_context(matches)
        if isinstance(result, tuple):
            if len(result) == 3:
                return result[0] or {}, result[1] or {}, result[2] or {}
            if len(result) == 2:
                return result[0] or {}, result[1] or {}, {}
            if len(result) == 1:
                return result[0] or {}, {}, {}
        return result or {}, {}, {}

    def _filter_matches(self, matches: list[Match], now_utc: datetime) -> tuple[list[Match], dict[str, Any]]:
        min_lead = timedelta(minutes=self.settings.min_kickoff_lead_minutes)
        max_window = timedelta(hours=self.settings.publish_window_hours)

        total_before = len(matches)
        skipped_started = 0
        skipped_too_soon = 0
        skipped_outside_window = 0
        kept: list[Match] = []

        for match in matches:
            kickoff = match.commence_time.astimezone(UTC)
            delta = kickoff - now_utc

            if delta.total_seconds() <= 0:
                skipped_started += 1
                continue
            if delta < min_lead:
                skipped_too_soon += 1
                continue
            if delta > max_window:
                skipped_outside_window += 1
                continue
            kept.append(match)

        filtering = {
            "total_before": total_before,
            "total_after": len(kept),
            "skipped_started": skipped_started,
            "skipped_too_soon": skipped_too_soon,
            "skipped_outside_window": skipped_outside_window,
            "publish_window_hours": self.settings.publish_window_hours,
            "min_kickoff_lead_minutes": self.settings.min_kickoff_lead_minutes,
            "now_utc": now_utc.isoformat(),
            "now_local": now_utc.astimezone(self.settings.tzinfo).isoformat(),
        }
        return kept, filtering

    def _write_debug(
        self,
        *,
        summary: dict[str, Any] | None,
        all_matches: list[Match] | None = None,
        merged_offers: dict[str, list[Any]] | None = None,
        contexts: dict[str, Any] | None = None,
        candidates: list[CandidateBet] | None = None,
        model_debug: Any = None,
        telegram_payloads: list[str] | None = None,
        source_previews: dict[str, Any] | None = None,
        error_text: str | None = None,
    ) -> None:
        payload = {
            "created_at": datetime.now(UTC).isoformat(),
            "summary": summary,
            "error": error_text,
            "settings": {
                "run_sports": self.settings.run_sports,
                "run_days_ahead": self.settings.run_days_ahead,
                "publish_window_hours": self.settings.publish_window_hours,
                "min_kickoff_lead_minutes": self.settings.min_kickoff_lead_minutes,
                "target_bookmakers": self.settings.target_bookmakers,
                "consensus_bookmakers": self.settings.consensus_bookmakers,
                "publish_dry_run": self.settings.publish_dry_run,
                "bookies_api_enabled": self.settings.bookies_api_enabled,
                "app_timezone": self.settings.app_timezone,
            },
            "source_previews": source_previews or {},
            "sample_matches": [self._serialize_match(item) for item in (all_matches or [])[:25]],
            "sample_offers": self._serialize_offers(merged_offers or {}, limit=25),
            "model_debug": model_debug,
            "candidates": [self._serialize_candidate(item) for item in (candidates or [])[:25]],
            "telegram_messages": telegram_payloads or [],
        }
        debug_path = Path(self.settings.debug_path)
        debug_path.parent.mkdir(parents=True, exist_ok=True)
        debug_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _write_exports(
        self,
        matches: list[Match],
        candidates: list[CandidateBet],
        now_local: datetime,
    ) -> dict[str, str]:
        base_dir = Path(self.settings.storage_export_dir)
        dated_dir = base_dir / now_local.strftime("%Y-%m-%d")
        dated_dir.mkdir(parents=True, exist_ok=True)

        stamp = now_local.strftime("%H%M%S")
        matches_rows = [self._serialize_match(item) for item in matches]
        picks_rows = [self._serialize_candidate(item) for item in candidates]

        matches_json = dated_dir / f"{stamp}-matches.json"
        matches_csv = dated_dir / f"{stamp}-matches.csv"
        picks_json = dated_dir / f"{stamp}-picks.json"
        picks_csv = dated_dir / f"{stamp}-picks.csv"

        self._write_json(matches_json, matches_rows)
        self._write_json(picks_json, picks_rows)
        self._write_csv(matches_csv, matches_rows)
        self._write_csv(picks_csv, picks_rows)

        self._write_json(base_dir / "latest-matches.json", matches_rows)
        self._write_json(base_dir / "latest-picks.json", picks_rows)
        self._write_csv(base_dir / "latest-matches.csv", matches_rows)
        self._write_csv(base_dir / "latest-picks.csv", picks_rows)

        return {
            "matches_json": str(matches_json),
            "matches_csv": str(matches_csv),
            "picks_json": str(picks_json),
            "picks_csv": str(picks_csv),
            "latest_matches_json": str(base_dir / "latest-matches.json"),
            "latest_matches_csv": str(base_dir / "latest-matches.csv"),
            "latest_picks_json": str(base_dir / "latest-picks.json"),
            "latest_picks_csv": str(base_dir / "latest-picks.csv"),
        }

    @staticmethod
    def _write_json(path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not rows:
            path.write_text("", encoding="utf-8")
            return
        fieldnames: list[str] = []
        seen: set[str] = set()
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    seen.add(key)
                    fieldnames.append(key)
        with path.open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _serialize_match(match: Match) -> dict[str, Any]:
        row = asdict(match)
        row["commence_time"] = match.commence_time.isoformat()
        row["match_key"] = match.match_key
        row["loose_key"] = match.loose_key
        return row

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
                if is_dataclass(offer):
                    row = asdict(offer)
                else:
                    row = dict(getattr(offer, "__dict__", {}))
                row["match_key"] = match_key
                rows.append(row)
                if len(rows) >= limit:
                    return rows
        return rows
