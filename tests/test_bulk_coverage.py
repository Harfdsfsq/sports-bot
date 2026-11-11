import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.providers.sstats_pari_daily import SStatsPariDailyProvider
from app.services.daily_match_registry import DailyMatchRegistry
from tests.test_native_data_expansion import client_patch, fixture, pari_row


def named_match(now, i):
    suffix = "q" + chr(97 + i // 26) + chr(97 + i % 26)
    return replace(
        fixture(now),
        home_team="Home " + suffix,
        away_team="Away " + suffix,
        home_team_norm="home " + suffix,
        away_team_norm="away " + suffix,
    )


def bulk_row(match, i):
    row = pari_row(match.commence_time - timedelta(hours=1))
    row["matchInfo"].update(eventId=1000 + i, homeTeam={"name": match.home_team}, awayTeam={"name": match.away_team})
    return row


def test_pari_bulk_covers_120_fixtures_without_per_match_requests(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAILY_PARI_ENABLED", "true")
    now = datetime.now(UTC)
    matches = [replace(named_match(now, i), commence_time=now + timedelta(hours=1 + i / 10)) for i in range(120)]
    rows = [bulk_row(m, i) for i, m in enumerate(matches)]
    calls = []

    def response(url, params):
        calls.append(url)
        assert url.endswith("/matches") and params["includeOdds"] == "true"
        page = [r for r in rows if r["matchInfo"]["startDate"][:10] == params["date"]]
        return 200, {"data": page, "TotalCount": len(page)}

    client_patch(monkeypatch, "app.providers.sstats_pari_daily", response)
    p = SStatsPariDailyProvider(SimpleNamespace(sstats_api_key="test-only"))
    offers, stats, _ = asyncio.run(p.fetch_offers(matches))
    assert len(offers) == 120 and len(calls) <= 2
    assert stats["detail_requests_avoided"] == stats["bulk_quotes_matches"] == 120
    assert stats["confirmation_requests_reserved"] == 4
    assert all(len(quotes) == 2 for quotes in offers.values())


@pytest.mark.parametrize("problem", ["live", "squad", "blocked", "period", "closing_only"])
def test_bulk_prices_keep_identity_status_and_market_guards(tmp_path, monkeypatch, problem):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAILY_PARI_ENABLED", "true")
    now = datetime.now(UTC)
    m = fixture(now)
    row = bulk_row(m, 0)
    if problem == "live":
        row["matchInfo"]["status"] = "Live"
    elif problem == "squad":
        row["matchInfo"]["homeTeam"]["name"] += " U21"
    elif problem == "blocked":
        for quote in row["currentOdds"]:
            quote["isBlocked"] = True
    elif problem == "period":
        for quote in row["currentOdds"]:
            quote["period"] = "1st half"
    else:
        row["prematchOdds"] = row.pop("currentOdds")
    client_patch(
        monkeypatch,
        "app.providers.sstats_pari_daily",
        lambda url, params: (200, {"data": [row], "TotalCount": 1} if url.endswith("/matches") else {"data": row}),
    )
    p = SStatsPariDailyProvider(SimpleNamespace(sstats_api_key="test-only"))
    assert not asyncio.run(p.fetch_offers([m]))[0]


def test_bulk_quotes_never_replace_final_detail_confirmation(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAILY_PARI_ENABLED", "true")
    now = datetime.now(UTC)
    m = fixture(now)
    row = bulk_row(m, 0)
    calls = []

    def response(url, params):
        calls.append(url)
        if url.endswith("/matches"):
            return 200, {"data": [row], "TotalCount": 1}
        detail = deepcopy(row)
        detail["matchInfo"]["status"] = "Live"
        return 200, {"data": detail}

    client_patch(monkeypatch, "app.providers.sstats_pari_daily", response)
    p = SStatsPariDailyProvider(SimpleNamespace(sstats_api_key="test-only"))
    assert asyncio.run(p.fetch_offers([m]))[0]
    assert not asyncio.run(p.confirm_offers([m]))[0]
    assert calls[-1].endswith("/match/1000")


def test_bzzoiro_datetime_range_and_bulk_contexts_beyond_first_eight(tmp_path, monkeypatch):
    from app.config import Settings
    from app.providers.bzzoiro import BzzoiroContextProvider
    from app.services.bzzoiro_free_enrichment import enrich
    from tests.test_free_api_enrichment import prediction

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("BZZOIRO_V2_NATIVE_MATCH_LIMIT", "2")
    monkeypatch.setenv("BZZOIRO_V2_NATIVE_EXTRA_FACTS_ENABLED", "false")
    now = datetime.now(UTC)
    matches = [replace(named_match(now, i), commence_time=now + timedelta(hours=1 + i / 2)) for i in range(20)]
    rows = []
    for i, m in enumerate(matches):
        p = prediction(now)
        p["id"] = 200 + i
        p["event"].update(
            id=1000 + i, home_team=m.home_team, away_team=m.away_team, event_date=m.commence_time.isoformat()
        )
        rows.append(p)
    calls = []

    def response(url, params):
        calls.append(url)
        if "/predictions/" in url:
            start = datetime.fromisoformat(params["date_from"])
            end = datetime.fromisoformat(params["date_to"])
            assert start < min(m.commence_time for m in matches) < max(m.commence_time for m in matches) < end
            assert params["status"] == "upcoming"
            return 200, {"results": rows, "next": None}
        # Optional facts must not consume one request for every prediction.
        return 200, {}

    client_patch(monkeypatch, "app.services.bzzoiro_free_enrichment", response)
    provider = BzzoiroContextProvider(Settings(_env_file=None))
    provider.api_key = "test-only"
    contexts, stats = {}, {"requests": 0}
    asyncio.run(enrich(provider, matches, contexts, stats))
    assert len(contexts) == 20 and len(calls) == 3
    assert stats["v2_native"]["detail_attempts"] == 2


def future_sstats_row(m):
    return {
        "id": 555,
        "date": m.commence_time.isoformat(),
        "homeTeam": {"id": 10, "name": m.home_team},
        "awayTeam": {"id": 11, "name": m.away_team},
    }


@pytest.mark.parametrize("problem", ["valid", "wrong_event", "squad", "time", "malformed"])
def test_future_fixture_team_ids_require_verified_game_binding(problem):
    from app.services.sstats_targeted_history import team_bindings
    from tests.test_free_api_enrichment import fixture as history_fixture

    now = datetime.now(UTC)
    m = history_fixture(now)
    row = future_sstats_row(m)
    m.metadata = {"provider_source_ids": {"sstats": "555"}, "sstats_fixture": row}
    if problem == "wrong_event":
        row["id"] = 556
    elif problem == "squad":
        row["homeTeam"]["name"] += " U21"
    elif problem == "time":
        row["date"] = (now + timedelta(hours=7)).isoformat()
    elif problem == "malformed":
        row["homeTeam"] = "broken"
    result = team_bindings([m], [])
    assert result == ({"10": m.home_team, "11": m.away_team} if problem == "valid" else {})


def test_future_ids_unlock_real_history_without_becoming_context(tmp_path, monkeypatch):
    from app.services.sstats_targeted_history import enrich_team_form
    from tests.test_free_api_enrichment import fixture as history_fixture
    from tests.test_free_api_enrichment import history_rows, targeted_client, targeted_provider

    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    m = history_fixture(now)
    m.metadata = {"provider_source_ids": {"sstats": "555"}, "sstats_fixture": future_sstats_row(m)}
    registry = DailyMatchRegistry(tmp_path / "registry.json", now)
    registry.sync([m])
    assert not registry.has_role(m, "context")
    calls = []

    def response(params):
        calls.append(params)
        assert params["Team"] == "10,11" and params["Ended"] == "true"
        return {"data": history_rows(now) if params["Offset"] == 0 else []}

    targeted_client(monkeypatch, response)
    stats = {"requests": 0}
    contexts = asyncio.run(enrich_team_form(targeted_provider(), [m], [], stats, {"team_form_examples": []}))
    assert m.match_key in contexts and len(calls) == 2
    assert contexts[m.match_key].details["home_recent_count"] == 8


def test_prefetch_keeps_more_than_sixty_next_day_matches(tmp_path):
    now = datetime(2026, 11, 12, 12, tzinfo=UTC)
    matches = [replace(named_match(now, i), commence_time=now + timedelta(hours=16, minutes=i)) for i in range(90)]
    outside = replace(named_match(now, 100), commence_time=now + timedelta(hours=25))
    registry = DailyMatchRegistry(tmp_path / "registry.json", now)
    registry.sync([*matches, outside])
    assert len(registry.matches()) == 90
    assert len(registry.targets("sstats", "context", limit=300)) == 90
    assert registry.coverage()["future_matches"] == 90
    assert registry.coverage()["near"] == 0


@pytest.mark.parametrize("problem", ["valid", "team_id", "game_id", "squad", "kickoff", "ambiguous_team_id", "same_name_conflict"])
def test_pari_aliases_need_explicit_crosswalk_and_matching_squads(problem):
    from app.providers.sstats_pari_daily import matches_fixture

    now = datetime.now(UTC)
    m = fixture(now)
    m.metadata = {"provider_source_ids": {"sstats": "555"}, "sstats_fixture": future_sstats_row(m)}
    row = bulk_row(m, 0)
    row["matchInfo"]["mappings"] = {"sstats": "555"}
    row["matchInfo"]["homeTeam"] = {"name": "Unrelated Display Name", "mappings": {"sstats": ["10"]}}
    row["matchInfo"]["awayTeam"]["mappings"] = {"sstats": ["11"]}
    if problem in {"team_id", "same_name_conflict"}:
        row["matchInfo"]["homeTeam"]["mappings"]["sstats"] = ["99"]
        if problem == "same_name_conflict":
            row["matchInfo"]["homeTeam"]["name"] = m.home_team
    elif problem == "game_id":
        row["matchInfo"]["mappings"]["sstats"] = "556"
    elif problem == "squad":
        row["matchInfo"]["homeTeam"]["name"] += " U21"
    elif problem == "kickoff":
        row["matchInfo"]["startDate"] = (m.commence_time + timedelta(hours=1)).isoformat()
    elif problem == "ambiguous_team_id":
        row["matchInfo"]["homeTeam"]["mappings"]["sstats"] = ["10", "99"]
    assert matches_fixture(m, row) is (problem == "valid")


def catalogue():
    return {
        "data": [
            {
                "name": "Total",
                "outcomes": [
                    {"id": 900, "name": "Over", "period": "FullTime", "parameter": 2.5},
                    {"id": 901, "name": "Under", "period": "FullTime", "parameter": 2.5},
                    {"id": 902, "name": "Over", "period": "FirstHalf", "parameter": 0.5},
                ],
            },
            {
                "name": "Corners_Total",
                "outcomes": [{"id": 903, "name": "Over", "period": "FullTime", "parameter": 8.5}],
            },
        ]
    }


@pytest.mark.parametrize("corrupt", [False, True])
def test_compact_bulk_quotes_use_cached_official_dictionary(tmp_path, monkeypatch, corrupt):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAILY_PARI_ENABLED", "true")
    now = datetime.now(UTC)
    m = fixture(now)
    row = bulk_row(m, 0)
    row["currentOdds"] = [{"id": i, "value": 1.9} for i in (900, 901, 902, 903, 999)]
    if corrupt:
        from app.services.daily_match_registry import write_json
        write_json(".data/provider_cache/pari-market-types-v1.json", {"observed_at": now.isoformat(), "payload": {"data": []}})
    calls = []

    def response(url, params):
        calls.append(url)
        if url.endswith("market-types"):
            return 200, catalogue()
        assert url.endswith("/matches")  # No per-game calls needed.
        return 200, {"data": [row], "TotalCount": 1}

    client_patch(monkeypatch, "app.providers.sstats_pari_daily", response)
    p = SStatsPariDailyProvider(SimpleNamespace(sstats_api_key="test-only"))
    offers, stats, _ = asyncio.run(p.fetch_offers([m]))
    assert len(offers[m.match_key]) == 2 and len(calls) == 2
    assert stats["market_dictionary_outcomes"] == 2
    assert {o.point for o in offers[m.match_key]} == {2.5}
    again, stats, _ = asyncio.run(p.fetch_offers([m]))
    assert len(again[m.match_key]) == 2 and len(calls) == 3
    assert stats["market_dictionary_cache_hit"]


@pytest.mark.parametrize("problem", ["valid", "missing_away", "wrong_id"])
def test_standings_are_additional_facts_and_keep_form_timestamp(tmp_path, monkeypatch, problem):
    from app.schemas import MatchContext
    from app.services.sstats_standings import enrich_standings
    from tests.test_free_api_enrichment import targeted_provider

    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    m = fixture(now)
    row = future_sstats_row(m)
    row["season"] = {"year": 2026, "league": {"id": 370}}
    m.metadata = {"provider_source_ids": {"sstats": "555"}, "sstats_fixture": row}
    at = (now - timedelta(hours=1)).isoformat()
    c = MatchContext("sstats_form", {}, expected_home=1.4, expected_away=1.1, details={"daily_source_observed_at": at})
    standings = {
        str(i): {"teamId": i, "rank": i - 9, "points": 25, "totalGames": 15, "goalsScored": 20, "goalsMissed": 10}
        for i in (10, 11)
    }
    if problem == "missing_away":
        del standings["11"]
    elif problem == "wrong_id":
        standings["10"]["teamId"] = 99
    provider = targeted_provider()
    calls = []
    client_patch(
        monkeypatch,
        "app.services.sstats_standings",
        lambda *args: (_ for _ in ()).throw(AssertionError("unexpected HTTP")),
    )

    async def request(client, url, *, params, stats, request_ceiling):
        calls.append(params)
        stats["requests"] += 1
        return SimpleNamespace(json=lambda: {"data": standings})

    monkeypatch.setattr(provider, "_request_with_retries", request)
    stats = {"requests": 0}
    asyncio.run(enrich_standings(provider, [m], {m.match_key: c}, stats))
    assert ("sstats_standings" in c.details) is (problem == "valid")
    assert c.details["daily_source_observed_at"] == at and c.expected_home == 1.4
    asyncio.run(enrich_standings(provider, [m], {m.match_key: c}, {"requests": 0}))
    assert len(calls) == 1


def test_inventory_keeps_verified_team_ids_and_unix_dateutc():
    from app.config import Settings
    from app.services.sstats_targeted_history import team_bindings
    from scripts.build_day_inventory_core import sstats_row_to_match

    now = datetime.now(UTC)
    row = future_sstats_row(fixture(now))
    row["dateUtc"] = int(fixture(now).commence_time.timestamp())
    row["season"] = {"year": 2026, "league": {"name": "England Premier League", "id": 1}}
    m = sstats_row_to_match(row, Settings(_env_file=None))
    assert m is not None
    assert abs((m.commence_time - fixture(now).commence_time).total_seconds()) < 1
    assert team_bindings([m], []) == {"10": m.home_team, "11": m.away_team}


def test_future_binding_reuses_archive_without_refreshing_receipt(tmp_path, monkeypatch):
    from app.services.team_result_archive import retain_history
    from tests.test_free_api_enrichment import fixture as history_fixture
    from tests.test_free_api_enrichment import history_rows, targeted_provider

    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    provider = targeted_provider()
    original = (now - timedelta(hours=2)).isoformat()
    retain_history(provider, history_rows(now), original)
    m = history_fixture(now)
    raw = future_sstats_row(m)
    reused, report = retain_history(provider, [], None, binding_rows=[raw])
    assert report["reused"] == 16
    assert all(r["_history_observed_at"] == original for r in reused)
    provider._daily_source_observed_at = now.isoformat()
    contexts = provider._build_team_form_contexts([m], reused, {"team_form_examples": []})
    assert contexts[m.match_key].details["daily_source_observed_at"] == original


@pytest.mark.parametrize(
    "league,home,allowed",
    [
        ("Premier League Women", "Arsenal Women", True),
        ("Premier League U21", "Arsenal U21", True),
        ("Unknown", "Arsenal", False),
        ("eSoccer Battle", "Arsenal (gamer)", False),
    ],
)
def test_inventory_collection_keeps_real_squads_but_rejects_simulations(monkeypatch, league, home, allowed):
    from scripts.build_day_inventory_core import make_match

    monkeypatch.setenv("PUBLICATION_PROFILE", "daily_quality")
    monkeypatch.setenv("DAY_INVENTORY_ALLOW_LOW_TIER", "true")
    result = make_match(
        source="sstats",
        source_event_id="123",
        league_name=league,
        home_team=home,
        away_team="Chelsea",
        commence_time=datetime.now(UTC) + timedelta(hours=2),
    )
    assert (result is not None) is allowed


@pytest.mark.parametrize(
    "label,allowed",
    [
        ("Full Time. 1X2: 1", True),
        ("Full Time. 1X2: 2", True),
        ("Full Time. 1X2: X", True),
        ("First Half. 1X2: 1", False),
        ("Corners. 1X2: 1", False),
    ],
)
def test_real_detail_labels_preserve_full_match_confirmation(label, allowed):
    from app.providers.sstats_pari_parser import parse_offers

    offers = parse_offers([{"id": 8250, "name": label, "value": 1.9}], fixture(datetime.now(UTC)), "123")
    assert bool(offers) is allowed


def test_cached_form_can_receive_standings_without_receipt_reset(tmp_path, monkeypatch):
    from app.services.daily_match_registry import match_identity
    from app.services.daily_quality_runner import DailyQualityRunner
    from tests.test_free_api_enrichment import fixture as history_fixture
    from tests.test_free_api_enrichment import history_rows, targeted_provider

    monkeypatch.chdir(tmp_path)
    now = datetime.now(UTC)
    m = history_fixture(now)
    raw = future_sstats_row(m)
    raw["season"] = {"year": 2026, "league": {"id": 370}}
    m.metadata = {"provider_source_ids": {"sstats": "555"}, "sstats_fixture": raw}
    p = targeted_provider()
    context = p._build_team_form_contexts([m], history_rows(now), {"team_form_examples": []})[m.match_key]
    at = (now - timedelta(hours=2)).isoformat()
    context.details["daily_source_observed_at"] = at
    r = DailyQualityRunner.__new__(DailyQualityRunner)
    r.registry = DailyMatchRegistry(tmp_path / "registry.json", now)
    r.registry.sync([m])
    r.registry.record("sstats", "context", [m], {m.match_key: context}, {})
    r.inventory_matches = [m]
    r.work_plan = {}
    r.settings = p.settings
    r._provider_name = lambda _: "sstats"
    client_patch(monkeypatch, "app.services.sstats_standings", lambda *args: (500, {}))
    table = {
        str(i): {"teamId": i, "rank": i - 9, "points": 25, "totalGames": 15, "goalsScored": 20, "goalsMissed": 10}
        for i in (10, 11)
    }
    calls = []

    async def request(client, url, *, params, stats, request_ceiling):
        assert url.endswith("/season-table")
        calls.append(url)
        stats["requests"] = stats.get("requests", 0) + 1
        return SimpleNamespace(json=lambda: {"data": table})

    monkeypatch.setattr(p, "_request_with_retries", request)
    result, stats, _ = asyncio.run(r._fetch_provider(p, "fetch_context", [], empty_data={}))
    assert result[m.match_key].details["sstats_standings"]["home_rank"] == 1
    assert stats["assigned_matches"] == 0 and len(calls) == 1
    evidence = r.registry.data["matches"][match_identity(m)]["evidence"]["sstats:context"]
    assert evidence["observed_at"] == at


def test_collection_and_live_coverage_are_separate(tmp_path):
    from app.schemas import Offer

    now = datetime.now(UTC)
    m = fixture(now)
    r = DailyMatchRegistry(tmp_path / "registry.json", now)
    r.sync([m])
    quotes = [Offer("odds_api_io", book, "totals", "Over", 1.9, 2.5) for book in ("William Hill", "Unibet")]
    r.record("odds_api_io", "offers", [m], {m.match_key: quotes}, {})
    c = r.coverage()
    assert c["collected_line"] == c["future_line"] == c["near_two_book_lines"] == 1
    r.now = now + timedelta(minutes=16)
    c = r.coverage()
    assert c["collected_line"] == 1 and c["future_line"] == c["near_two_book_lines"] == 0


def test_duplicate_outcome_id_cannot_cross_periods():
    from app.providers.sstats_pari_daily import market_definitions

    data = catalogue()
    data["data"][0]["outcomes"].append({"id": 900, "name": "Over", "period": "FirstHalf", "parameter": 2.5})
    definitions = market_definitions(data)
    assert "900" not in definitions and "901" in definitions
