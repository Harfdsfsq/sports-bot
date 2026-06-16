from __future__ import annotations

import json
import os
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.schemas import Match, MatchContext, Offer
from app.services.coverage_contract import normalize_source
from app.utils import clamp, ensure_utc

UTC = timezone.utc


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


@dataclass(slots=True)
class MatchCoverageRow:
    match_key: str
    kickoff_utc: str
    league_name: str
    home_team: str
    away_team: str
    odds_sources: set[str] = field(default_factory=set)
    books: set[str] = field(default_factory=set)
    families: set[str] = field(default_factory=set)
    context_sources: set[str] = field(default_factory=set)
    fixture_sources: set[str] = field(default_factory=set)
    has_xg: bool = False
    has_form: bool = False
    has_weather: bool = False
    has_news: bool = False
    has_line_movement: bool = False
    hours_to_kickoff: float = 9999.0
    priority: float = 0.0

    def as_dict(self) -> dict[str, Any]:
        odds_count = len(self.odds_sources)
        context_count = len(self.context_sources)
        return {
            "match_key": self.match_key,
            "kickoff_utc": self.kickoff_utc,
            "league_name": self.league_name,
            "home_team": self.home_team,
            "away_team": self.away_team,
            "odds_sources": sorted(self.odds_sources),
            "odds_source_count": odds_count,
            "bookmakers": sorted(self.books),
            "bookmaker_count": len(self.books),
            "families": sorted(self.families),
            "context_sources": sorted(self.context_sources),
            "context_source_count": context_count,
            "fixture_sources": sorted(self.fixture_sources),
            "fixture_source_count": len(self.fixture_sources),
            "has_xg": self.has_xg,
            "has_form": self.has_form,
            "has_weather": self.has_weather,
            "has_news": self.has_news,
            "has_line_movement": self.has_line_movement,
            "ready_for_model": odds_count >= 1 and context_count >= 1,
            "ready_for_publish": odds_count >= 1 and context_count >= 1 and len(self.books) >= 2,
            "hours_to_kickoff": round(self.hours_to_kickoff, 3),
            "priority": round(self.priority, 3),
        }


class CoveragePlanner:
    """Run-level planner for the 300-match inventory.

    The runner already knows how to fetch offers and contexts from individual
    providers.  This planner keeps the policy in one core module instead of in
    runtime monkey-patches: every run ranks all inventory matches by missing
    odds/context coverage, kickoff window and league priority, then providers
    receive deterministic target lists.
    """

    CORE_ODDS_SOURCES = {"odds_api_io", "sportlogic", "bzzoiro", "allsportsapi", "oddspapi", "bookies_api"}
    CORE_CONTEXT_SOURCES = {"bzzoiro", "bzzoiro_v2", "sstats", "sportlogic", "football_data", "thesportsdb", "api_football", "futrixmetrics"}

    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.min_odds_sources = max(1, _as_int(os.getenv("PUBLISH_MIN_ODDS_SOURCES") or getattr(settings, "min_sources_publish", 1), 1))
        self.min_context_sources = max(1, _as_int(os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or getattr(settings, "min_context_sources_publish", 1), 1))
        self.min_books = max(2, _as_int(os.getenv("PUBLISH_MIN_BOOKS") or getattr(settings, "min_books_publish", 2), 2))
        self.context_limit = max(0, _as_int(getattr(settings, "context_enrichment_match_limit", 420), 420))
        self.export_path = Path(os.getenv("COVERAGE_PLANNER_EXPORT_PATH", ".data/exports/latest-coverage-planner.json"))

    def build_matrix(
        self,
        matches: list[Match],
        offers_by_match: dict[str, list[Offer]] | None = None,
        contexts_by_match: dict[str, MatchContext] | None = None,
        market_signals_by_match: dict[str, dict[str, Any]] | None = None,
        now_utc: datetime | None = None,
    ) -> dict[str, MatchCoverageRow]:
        now_utc = ensure_utc(now_utc or datetime.now(UTC))
        offers_by_match = offers_by_match or {}
        contexts_by_match = contexts_by_match or {}
        market_signals_by_match = market_signals_by_match or {}
        matrix: dict[str, MatchCoverageRow] = {}
        for match in matches:
            kickoff = ensure_utc(match.commence_time)
            meta = dict(getattr(match, "metadata", {}) or {})
            row = MatchCoverageRow(
                match_key=match.match_key,
                kickoff_utc=kickoff.isoformat(),
                league_name=str(match.league_name or ""),
                home_team=str(match.home_team or ""),
                away_team=str(match.away_team or ""),
                hours_to_kickoff=(kickoff - now_utc).total_seconds() / 3600.0,
            )
            row.fixture_sources.update(self._fixture_sources(match))
            row.context_sources.update(self._context_sources_from_match_metadata(meta))
            row.has_xg = bool(meta.get("bzzoiro_context_fields") or meta.get("sstats_context_fields") or meta.get("has_xg"))
            row.has_form = bool(meta.get("sstats_has_context_hint") or meta.get("has_form"))
            for offer in offers_by_match.get(match.match_key, []) or []:
                source = self._odds_source(getattr(offer, "source", ""))
                if source:
                    row.odds_sources.add(source)
                book = str(getattr(offer, "bookmaker", "") or "").strip().lower()
                if book:
                    row.books.add(book)
                family = str(getattr(offer, "family", "") or "").strip().lower()
                if family:
                    row.families.add(family)
            context = contexts_by_match.get(match.match_key)
            if context is not None:
                row.context_sources.update(self._context_sources(context))
                details = dict(getattr(context, "details", {}) or {})
                row.has_xg = row.has_xg or getattr(context, "expected_home", None) is not None or getattr(context, "expected_away", None) is not None
                row.has_form = row.has_form or bool(details.get("sstats_mode") or details.get("form") or details.get("team_form_index"))
                row.has_weather = row.has_weather or bool(details.get("weather_context_applied") or details.get("weather_provider"))
                row.has_news = row.has_news or bool(details.get("news_context_applied") or details.get("news_articles"))
            signal_pack = market_signals_by_match.get(match.match_key) or {}
            row.has_line_movement = any(bool(sig.get("history_ready")) for sig in signal_pack.values() if isinstance(sig, dict))
            row.priority = self._priority(row)
            matrix[match.match_key] = row
        return matrix

    def select_context_targets(
        self,
        matches: list[Match],
        offers_by_match: dict[str, list[Offer]] | None,
        now_utc: datetime,
        market_signals_by_match: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[list[Match], dict[str, Any]]:
        requires_offers = _truthy(getattr(self.settings, "context_enrichment_requires_offers", False), False)
        matrix = self.build_matrix(matches, offers_by_match or {}, {}, market_signals_by_match or {}, now_utc)
        ranked: list[tuple[tuple[float, ...], Match, MatchCoverageRow]] = []
        skipped_without_offers = 0
        for match in matches:
            row = matrix.get(match.match_key)
            if row is None:
                continue
            has_offers = bool((offers_by_match or {}).get(match.match_key))
            if requires_offers and not has_offers:
                skipped_without_offers += 1
                continue
            odds_gap = max(0, self.min_odds_sources - len(row.odds_sources))
            context_gap = max(0, self.min_context_sources - len(row.context_sources))
            books_gap = max(0, self.min_books - len(row.books))
            near_bucket = 5.0 if 0 <= row.hours_to_kickoff <= 2 else 4.0 if row.hours_to_kickoff <= 6 else 3.0 if row.hours_to_kickoff <= 12 else 2.0 if row.hours_to_kickoff <= 24 else 1.0
            league_priority = float(getattr(self.settings, "league_priority_score", lambda _x: 1.0)(match.league_name))
            line_ready = 1.0 if row.has_line_movement else 0.0
            key = (
                float(context_gap),
                float(odds_gap),
                float(books_gap),
                near_bucket,
                line_ready,
                league_priority,
                row.priority,
                -max(row.hours_to_kickoff, -1.0),
            )
            ranked.append((key, match, row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        limit = self.context_limit
        selected = [match for _, match, _ in ranked[:limit]] if limit > 0 else [match for _, match, _ in ranked]
        matrix_rows = [row.as_dict() for row in matrix.values()]
        summary = self._summary(matrix_rows)
        summary.update(
            {
                "policy": "coverage_gap_first",
                "requires_offers": requires_offers,
                "limit": limit,
                "eligible_matches": len(ranked),
                "selected_matches": len(selected),
                "skipped_without_offers": skipped_without_offers,
                "min_odds_sources": self.min_odds_sources,
                "min_context_sources": self.min_context_sources,
                "min_books": self.min_books,
                "top_gaps": [row.as_dict() for _, _, row in ranked[:25]],
            }
        )
        self.export(summary, matrix_rows)
        return selected, summary

    def provider_targets(self, provider_key: str, targets: list[Match], offers_by_match: dict[str, list[Offer]] | None = None) -> list[Match]:
        provider_key = str(provider_key or "").strip().lower()
        if not targets:
            return []
        # Expensive/news providers only handle late shortlist.  Core context providers
        # can work over the broad coverage list.
        if provider_key in {"newsapi", "gnews"}:
            limit = max(0, _as_int(os.getenv("NEWS_CONTEXT_SHORTLIST_LIMIT"), 24))
            return targets[:limit] if limit else []
        if provider_key in {"futrixmetrics", "api_football"}:
            limit = max(0, _as_int(os.getenv("PREMIUM_CONTEXT_SHORTLIST_LIMIT") or getattr(self.settings, "premium_context_shortlist_limit", 18), 18))
            return targets[:limit] if limit else []
        if provider_key == "sportlogic":
            # SportLogic is quota-sensitive; only ask it for matches that either have
            # SportLogic odds already or are very high in the gap queue.
            limit = max(0, _as_int(os.getenv("SPORTLOGIC_CONTEXT_TARGET_LIMIT"), 80))
            if limit <= 0:
                return []
            return targets[:limit]
        return targets

    def export(self, summary: dict[str, Any], matrix_rows: list[dict[str, Any]]) -> None:
        try:
            self.export_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"created_at_utc": datetime.now(UTC).isoformat(), "summary": summary, "matches": matrix_rows[:500]}
            self.export_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except Exception:
            return

    def _summary(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        counter = Counter()
        source_counter = Counter()
        context_counter = Counter()
        family_counter = Counter()
        for row in rows:
            counter["matches_total"] += 1
            counter["matches_with_odds"] += int(int(row.get("odds_source_count") or 0) >= 1)
            counter["matches_with_2plus_odds_sources"] += int(int(row.get("odds_source_count") or 0) >= 2)
            counter["matches_with_2plus_books"] += int(int(row.get("bookmaker_count") or 0) >= 2)
            counter["matches_with_context"] += int(int(row.get("context_source_count") or 0) >= 1)
            counter["matches_with_2plus_context_sources"] += int(int(row.get("context_source_count") or 0) >= 2)
            counter["matches_ready_for_model"] += int(bool(row.get("ready_for_model")))
            counter["matches_ready_for_publish"] += int(bool(row.get("ready_for_publish")))
            counter["matches_with_xg"] += int(bool(row.get("has_xg")))
            counter["matches_with_form"] += int(bool(row.get("has_form")))
            counter["matches_with_weather"] += int(bool(row.get("has_weather")))
            counter["matches_with_news"] += int(bool(row.get("has_news")))
            for source in row.get("odds_sources") or []:
                source_counter[str(source)] += 1
            for source in row.get("context_sources") or []:
                context_counter[str(source)] += 1
            for family in row.get("families") or []:
                family_counter[str(family)] += 1
        return {
            **{key: int(value) for key, value in counter.items()},
            "odds_source_counts": dict(source_counter.most_common()),
            "context_source_counts": dict(context_counter.most_common()),
            "market_family_counts": dict(family_counter.most_common()),
        }

    def _priority(self, row: MatchCoverageRow) -> float:
        score = 0.0
        if 0 <= row.hours_to_kickoff <= 2:
            score += 60.0
        elif row.hours_to_kickoff <= 6:
            score += 48.0
        elif row.hours_to_kickoff <= 12:
            score += 36.0
        elif row.hours_to_kickoff <= 24:
            score += 24.0
        else:
            score += 10.0
        score += max(0, self.min_odds_sources - len(row.odds_sources)) * 14.0
        score += max(0, self.min_context_sources - len(row.context_sources)) * 16.0
        score += max(0, self.min_books - len(row.books)) * 6.0
        score += len(row.fixture_sources) * 4.0
        score += len(row.families) * 3.0
        if row.has_line_movement:
            score += 6.0
        if row.has_xg:
            score += 5.0
        if row.has_form:
            score += 3.0
        return clamp(score, 0.0, 150.0)

    def _fixture_sources(self, match: Match) -> set[str]:
        meta = dict(getattr(match, "metadata", {}) or {})
        out = {self._source_name(getattr(match, "source", ""))}
        source_ids = meta.get("provider_source_ids") or meta.get("day_inventory_source_ids") or {}
        if isinstance(source_ids, dict):
            out.update(self._source_name(key) for key in source_ids.keys())
        sources_seen = meta.get("sources_seen")
        if isinstance(sources_seen, str):
            out.update(self._source_name(item) for item in sources_seen.replace(";", ",").split(","))
        elif isinstance(sources_seen, (list, tuple, set)):
            out.update(self._source_name(item) for item in sources_seen)
        return {item for item in out if item and item not in {"day_inventory", "inventory", "unknown"}}

    def _context_sources_from_match_metadata(self, meta: dict[str, Any]) -> set[str]:
        out: set[str] = set()
        if meta.get("bzzoiro_has_context_hint") or meta.get("bzzoiro_context_fields"):
            out.add("bzzoiro")
        if meta.get("sstats_has_context_hint") or meta.get("sstats_context_fields"):
            out.add("sstats")
        if meta.get("sportlogic_context"):
            out.add("sportlogic")
        return out

    def _context_sources(self, context: MatchContext) -> set[str]:
        details = dict(getattr(context, "details", {}) or {})
        raw = details.get("merged_sources") or details.get("context_sources") or []
        if isinstance(raw, str):
            raw = [raw]
        out = {self._source_name(getattr(context, "source", ""))}
        if isinstance(raw, (list, tuple, set)):
            out.update(self._source_name(item) for item in raw)
        return {item for item in out if item and item not in {"ensemble", "market_signal", "unknown"}}

    def _source_name(self, source: Any) -> str:
        return normalize_source(source, count_api_accounts_as_sources=False)

    def _odds_source(self, source: Any) -> str:
        source_name = self._source_name(source)
        if source_name in {"bzzoiro_v2", "bzzoiro_predictions"}:
            return "bzzoiro"
        return source_name
