from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

from app.providers.allsportsapi import AllSportsApiOddsProvider
from app.services import allsportsapi_full_cohort_patch as allsports_patch
from app.services import daily_coverage_full_inventory_provider_patch as full_scope_patch
from app.services import strict_coverage_inventory_sync as strict_sync
from app.services.daily_coverage_assignments import build_assignments
from scripts import send_harizon_telegram_run_report_v13 as report_v13


def _row(index: int, *, kickoff: datetime | None = None) -> dict[str, Any]:
    start = kickoff or datetime(2026, 7, 18, 12, 0, tzinfo=UTC) + timedelta(minutes=index)
    return {
        "match_key": f"soccer|away {index}|home {index}|{start.date().isoformat()}",
        "home_team": f"Home {index}",
        "away_team": f"Away {index}",
        "kickoff_utc": start.isoformat(),
        "odds_sources": [],
        "context_sources": [],
        "hours_to_kickoff": 8.0,
    }


def test_verified_enrichment_counts_only_real_core_api_evidence() -> None:
    found = [
        {
            "odds": {
                "odds_api_io": {
                    "data": [
                        {"bookmaker": "Pinnacle"},
                        {"bookmaker": "Bet365"},
                    ]
                },
                "sstats_pari": {"data": [{"bookmaker": "SStats Pari"}]},
            },
            "context": {
                "sstats": {"data": {}},
                "clubelo": {"data": {}},
                "weather": {"data": {}},
                "newsapi": {"data": {}},
            },
        }
    ]
    score, enriched = strict_sync._enrich(_row(1), found)
    assert enriched["odds_sources"] == ["odds_api_io", "sstats_pari"]
    assert enriched["context_sources"] == ["clubelo", "sstats"]
    assert enriched["books"] == ["bet365", "pinnacle", "sstats pari"]
    assert enriched["coverage"]["strict_coverage_ready"] is True
    assert "ready_for_publish" not in enriched["coverage"]
    assert score[0] == 1


def test_target_local_day_identity_wins_over_utc_key_date() -> None:
    row = _row(1, kickoff=datetime(2026, 7, 17, 22, 30, tzinfo=UTC))
    row["match_key"] = "soccer|away 1|home 1|2026-07-17"
    assert strict_sync._identity(row, "2026-07-18")[0] == "2026-07-18"


def test_selector_keeps_best_partial_rows_and_rotates_exploration(monkeypatch) -> None:
    strict = [((1, 9, 1, 1, 1, 3, 0, f"s-{i}"), {"match_key": f"s-{i}"}) for i in range(10)]
    partial = [((0, 8, 1, 1, 1, 3, -i, f"p-{i}"), {"match_key": f"p-{i}"}) for i in range(350)]
    ranked = strict + partial

    monkeypatch.setenv("GITHUB_RUN_ID", "1")
    first, first_offset = strict_sync._select(ranked)
    monkeypatch.setenv("GITHUB_RUN_ID", "2")
    second, second_offset = strict_sync._select(ranked)

    first_keys = {row["match_key"] for row in first}
    second_keys = {row["match_key"] for row in second}
    assert len(first) == len(second) == 300
    assert {f"s-{i}" for i in range(10)} <= first_keys
    # need=290, so the best 193 partial rows remain stable while the rest rotates.
    assert {f"p-{i}" for i in range(193)} <= first_keys
    assert first_offset != second_offset
    assert first_keys != second_keys


def test_assignments_target_all_300_uncovered_rows(monkeypatch) -> None:
    monkeypatch.setenv("SSTATS_PARI_DETAIL_MATCH_LIMIT", "300")
    monkeypatch.setenv("BZZOIRO_RUNTIME_DETAIL_MATCH_LIMIT", "300")
    monkeypatch.setenv("SPORTLOGIC_MATCH_LIMIT", "300")
    assignments = build_assignments([_row(index) for index in range(300)], 3)
    assert len(assignments["odds_api_io"]["offers"]) == 300
    assert len(assignments["sstats_pari"]["offers"]) == 300
    assert len(assignments["bzzoiro"]["offers"]) == 300
    assert len(assignments["sstats"]["context"]) == 300
    assert len(assignments["bzzoiro"]["context"]) == 300
    assert len(assignments["espn"]["context"]) == 300


def test_full_day_provider_scope_does_not_expand_candidate_window(monkeypatch) -> None:
    class Match:
        def __init__(self, key: str, kickoff: datetime) -> None:
            self.match_key = key
            self.commence_time = kickoff

    class Runner:
        def __init__(self) -> None:
            self.settings = SimpleNamespace(tzinfo=UTC)
            self.received: list[Any] = []

        def _filter_matches(self, matches, now_utc):
            return list(matches[:1]), {"candidate_window": 1}

        async def _fetch_provider(self, provider, method_name, *args, empty_data):
            self.received = list(args[0])
            return {}, {}, {}

    monkeypatch.setattr(full_scope_patch, "_INSTALLED", False)
    monkeypatch.setattr(full_scope_patch, "_ORIGINAL_FILTER", None)
    monkeypatch.setattr(full_scope_patch, "_ORIGINAL_FETCH", None)
    monkeypatch.setattr(full_scope_patch, "target_date", lambda _now=None: "2026-07-18")
    monkeypatch.setattr(full_scope_patch, "_provider_name", lambda *_args: "sstats_pari")
    monkeypatch.setattr(full_scope_patch, "filter_matches", lambda _name, _method, matches: list(matches))
    full_scope_patch.install(Runner)

    now = datetime(2026, 7, 18, 8, 0, tzinfo=UTC)
    matches = [Match(f"m-{index}", now + timedelta(hours=index + 1)) for index in range(3)]
    runner = Runner()
    filtered, diagnostics = runner._filter_matches(matches, now)
    assert len(filtered) == 1
    assert diagnostics["candidate_window"] == 1

    asyncio.run(runner._fetch_provider(object(), "fetch_offers", filtered, empty_data={}))
    assert [match.match_key for match in runner.received] == ["m-0", "m-1", "m-2"]


def test_allsports_partial_cache_cannot_hide_uncovered_matches(monkeypatch) -> None:
    original_load = AllSportsApiOddsProvider._load_cached_offers
    original_prioritize = AllSportsApiOddsProvider._prioritize_matches
    monkeypatch.setattr(allsports_patch, "_INSTALLED", False)
    monkeypatch.setattr(allsports_patch, "_ORIGINAL_LOAD", None)
    monkeypatch.setattr(allsports_patch, "_ORIGINAL_PRIORITIZE", None)
    monkeypatch.setattr(
        AllSportsApiOddsProvider,
        "_load_cached_offers",
        lambda _self, _matches: {"m-0": [object()]},
    )
    monkeypatch.setattr(
        AllSportsApiOddsProvider,
        "_prioritize_matches",
        lambda _self, matches: list(matches),
    )
    try:
        allsports_patch.install()
        provider = object.__new__(AllSportsApiOddsProvider)
        provider.max_http_requests = 3
        matches = [SimpleNamespace(match_key=f"m-{index}") for index in range(4)]
        assert provider._load_cached_offers(matches) is None

        monkeypatch.setenv("GITHUB_RUN_ID", "1")
        first = [row.match_key for row in provider._prioritize_matches(matches)]
        monkeypatch.setenv("GITHUB_RUN_ID", "2")
        second = [row.match_key for row in provider._prioritize_matches(matches)]
        assert first != second
    finally:
        AllSportsApiOddsProvider._load_cached_offers = original_load
        AllSportsApiOddsProvider._prioritize_matches = original_prioritize


def test_russian_report_uses_exact_verified_percentages_and_preserves_publish_count(monkeypatch) -> None:
    monkeypatch.setattr(
        report_v13,
        "_verified_counts",
        lambda: {
            "total": 300,
            "line1": 300,
            "context1": 299,
            "books2": 300,
            "odds2": 300,
            "context2": 300,
            "model": 300,
            "a": 300,
            "b": 300,
        },
    )
    text = """📦 Инвентарь и покрытие
• 1+ линия: 10/300 (3%) | 1+ контекст: 10/300 (3%)
• 2+ букмекера: 10/300 (3%)
• 2+ independent odds-source: 10/300 (3%)
• 2+ контекста: 10/300 (3%)
• Готово для модели: 10/300 (3%)

🏷️ A/B-tier публикация
• A-tier strict-ready: 10 | main опубликовано: 2
• B-tier 2+ line/2+ bookmaker/2+ context coverage: 10 | fallback опубликовано: 1
• A-cover 2+ odds-source ∩ 2+ букмекера ∩ 2+ контекста: 10 матчей; B-cover strict intersection: 10 матчей.
"""
    rendered = report_v13._render_verified(text)
    assert "1+ линия: 300/300 (100%)" in rendered
    assert "1+ контекст: 299/300 (99%)" in rendered
    assert "2+ независимых источника линий: 300/300 (100%)" in rendered
    assert "2+ независимых контекста: 300/300 (100%)" in rendered
    assert "A-tier coverage-ready: 300/300 | main опубликовано: 2" in rendered
    assert "B-tier coverage-ready: 300/300 | fallback опубликовано: 1" in rendered
