import asyncio
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.providers.sstats_pari_daily import SStatsPariDailyProvider
from app.schemas import Match
from app.services.context_admission import admit_context


def fixture(now):
    return Match(
        "test",
        "1",
        "soccer",
        "England Premier League",
        "Arsenal",
        "Chelsea",
        now + timedelta(hours=1),
        "arsenal",
        "chelsea",
        "pl",
        metadata={},
    )


def pari_row(now, status="NotStarted"):
    return {
        "matchInfo": {
            "eventId": 42,
            "startDate": (now + timedelta(hours=1)).isoformat(),
            "homeTeam": {"name": "Arsenal"},
            "awayTeam": {"name": "Chelsea"},
            "status": status,
        },
        "currentOdds": [
            {"id": 1, "name": "Тотал больше 2.5", "value": 1.95},
            {"id": 2, "name": "Тотал меньше 2.5", "value": 1.95},
        ],
    }


def client_patch(monkeypatch, module, callback):
    import httpx

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params=None, **kwargs):
            status, payload = callback(url, params or {})
            return httpx.Response(status, json=payload, request=httpx.Request("GET", url))

    monkeypatch.setattr(module + ".BudgetedAsyncClient", Client)


@pytest.mark.parametrize("change", ["valid", "live", "wrong_squad", "wrong_id", "blocked", "closing_only", "subperiod"])
def test_pari_accepts_only_verified_current_prematch_quotes(tmp_path, monkeypatch, change):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAILY_PARI_ENABLED", "true")
    now = datetime.now(UTC)
    row = pari_row(now)
    detail = deepcopy(row)
    calls = []
    if change == "live":
        detail["matchInfo"]["status"] = "Live"
    if change == "wrong_squad":
        detail["matchInfo"]["homeTeam"]["name"] = "Arsenal U21"
    if change == "wrong_id":
        detail["matchInfo"]["eventId"] = 43
    if change == "blocked":
        for q in detail["currentOdds"]:
            q["isBlocked"] = True
    if change == "closing_only":
        detail["prematchOdds"] = detail.pop("currentOdds")
    if change == "subperiod":
        for q in detail["currentOdds"]:
            q["period"] = "1st half"

    def response(url, params):
        calls.append(url)
        if url.endswith("/matches"):
            assert params["timezone"] == 0 and params["upcoming"] == "true"
            return 200, {"data": [row], "TotalCount": 1}
        return 200, {"data": detail}

    client_patch(monkeypatch, "app.providers.sstats_pari_daily", response)
    p = SStatsPariDailyProvider(SimpleNamespace(sstats_api_key="test-only"))
    result, stats, _ = asyncio.run(p.confirm_offers([fixture(now)]))
    assert bool(result) == (change == "valid")
    assert stats["requests"] == len(calls) == 2
    if result:
        offers = result[fixture(now).match_key]
        assert {o.bookmaker for o in offers} == {"Pari"} and {o.source for o in offers} == {"sstats_pari"}
        assert all(o.metadata["current_prematch"] for o in offers)
        assert {o.point for o in offers} == {2.5}


@pytest.mark.parametrize("status", [401, 402, 403, 429])
def test_pari_stops_after_access_or_quota_denial(tmp_path, monkeypatch, status):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAILY_PARI_ENABLED", "true")
    now = datetime.now(UTC)
    calls = []

    def response(url, params):
        calls.append(url)
        return status, {}

    client_patch(monkeypatch, "app.providers.sstats_pari_daily", response)
    p = SStatsPariDailyProvider(SimpleNamespace(sstats_api_key="test-only"))
    result, stats, _ = asyncio.run(
        p.fetch_offers([fixture(now), replace(fixture(now), commence_time=now + timedelta(hours=2))])
    )
    assert not result and len(calls) == 1 and stats["blocked_status"] == status


def test_pari_respects_physical_request_cap_and_missing_key(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAILY_PARI_ENABLED", "true")
    monkeypatch.setenv("DAILY_PARI_REQUEST_LIMIT", "1")
    now = datetime.now(UTC)
    calls = []

    def response(url, params):
        calls.append(url)
        return 200, {"data": [pari_row(now)], "TotalCount": 1}

    client_patch(monkeypatch, "app.providers.sstats_pari_daily", response)
    p = SStatsPariDailyProvider(SimpleNamespace(sstats_api_key="test-only"))
    result, stats, _ = asyncio.run(p.confirm_offers([fixture(now)]))
    assert not result and len(calls) == 1 and stats["budget_exhausted"]
    p = SStatsPariDailyProvider(SimpleNamespace(sstats_api_key=""))
    asyncio.run(p.confirm_offers([fixture(now)]))
    assert len(calls) == 1


def football_payloads(now):
    comp = {
        "code": "PL",
        "id": 2021,
        "name": "Premier League",
        "area": {"name": "England"},
        "plan": "TIER_ONE",
        "currentSeason": {"startDate": f"{now.year}-01-01", "endDate": f"{now.year}-12-31"},
    }
    rows = []
    for team_id, name in [(10, "Arsenal"), (11, "Chelsea")]:
        for i in range(8):
            rows.append(
                {
                    "id": team_id * 100 + i,
                    "utcDate": (now - timedelta(days=7 * i + 1)).isoformat(),
                    "homeTeam": {"id": team_id, "name": name},
                    "awayTeam": {"id": 99, "name": "Porto"},
                    "competition": comp,
                    "status": "FINISHED",
                    "score": {"duration": "REGULAR", "fullTime": {"home": 2, "away": 1}},
                }
            )
    return comp, rows


@pytest.mark.parametrize("change", ["valid", "youth", "future", "extra_without_regulation", "ambiguous_competition"])
def test_football_data_empty_schedule_still_fetches_verified_history(tmp_path, monkeypatch, change):
    from app.config import Settings
    from app.providers.football_data import FootballDataContextProvider

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUBLICATION_PROFILE", "daily_quality")
    now = datetime.now(UTC)
    m = fixture(now)
    comp, rows = football_payloads(now)
    calls = []
    if change == "youth":
        m.home_team = "Arsenal U21"
    if change == "future":
        for r in rows:
            r["utcDate"] = (now + timedelta(minutes=15)).isoformat()
    if change == "extra_without_regulation":
        for r in rows:
            r["score"]["duration"] = "EXTRA_TIME"
    catalogue = [comp, {**comp, "code": "XX"}] if change == "ambiguous_competition" else [comp]

    def response(url, params):
        calls.append(url)
        if url.endswith("/competitions"):
            return 200, {"competitions": catalogue}
        if url.endswith("/PL/matches"):
            return 200, {"matches": rows}
        if url.endswith("/standings"):
            return 200, {
                "standings": [
                    {"table": [{"team": {"id": 10, "name": "Arsenal"}}, {"team": {"id": 11, "name": "Chelsea"}}]}
                ]
            }
        return 200, {"matches": []}

    client_patch(monkeypatch, "app.providers.football_data", response)
    client_patch(monkeypatch, "app.services.football_data_team_context", response)
    settings = Settings(_env_file=None)
    settings.football_data_api_key = "test-only"
    settings.football_data_requests_max_per_run = 20
    settings.enable_football_data_context = True
    p = FootballDataContextProvider(settings)
    result, stats, _ = asyncio.run(p.fetch_context([m]))
    assert bool(result) == (change == "valid")
    if result:
        c = result[m.match_key]
        assert c.source == "football_data" and c.details["football_data_home_sample"] >= 6
        assert c.details["football_data_standings_available"]
        assert admit_context("football_data", m, c, c.details["daily_source_observed_at"], datetime.now(UTC)) == []
        stamp = c.details["daily_source_observed_at"]
        n = len(calls)
        result, _, _ = asyncio.run(p.fetch_context([m]))
        assert result[m.match_key].details["daily_source_observed_at"] == stamp
        assert len(calls) == n + 1  # Only future schedule is queried again.
        import json

        history = json.loads((tmp_path / ".data/daily_quality/goal-history.json").read_text())
        assert all("sstats" not in r.get("sources", []) for r in history["results"].values())


def test_persistent_team_archive_reuses_identified_results_without_restamping(tmp_path, monkeypatch):
    from app.services.team_result_archive import retain_history
    from tests.test_free_api_enrichment import history_rows, targeted_provider

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUBLICATION_PROFILE", "daily_quality")
    now = datetime.now(UTC)
    p = targeted_provider()
    rows = history_rows(now)
    at = (now - timedelta(hours=2)).isoformat()
    _, stats = retain_history(p, rows, at)
    assert stats["retained"] == 16
    short = [rows[0], rows[8]]
    combined, stats = retain_history(p, short, now.isoformat())
    assert len(combined) == 16 and stats["reused"] == 14
    assert all(r["_history_observed_at"] == at for r in combined[2:])
    wrong = deepcopy(short)
    for r in wrong:
        r["homeTeam"]["name"] += " U21"
    combined, stats = retain_history(p, wrong, now.isoformat())
    assert stats["reused"] == 0


@pytest.mark.parametrize("change", ["valid", "wrong_squad", "stale", "wrong_line", "missing_key"])
def test_optional_odds_backup_has_one_credit_request_contract(tmp_path, monkeypatch, change):
    from app.providers.the_odds_daily import TheOddsDailyProvider

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("THE_ODDS_API_KEY", "" if change == "missing_key" else "test-only")
    now = datetime.now(UTC)
    m = fixture(now)
    calls = []
    event = {
        "id": "abc",
        "home_team": "Arsenal U21" if change == "wrong_squad" else "Arsenal",
        "away_team": "Chelsea",
        "commence_time": m.commence_time.isoformat(),
        "bookmakers": [
            {
                "title": "William Hill",
                "last_update": (now - timedelta(hours=1) if change == "stale" else now).isoformat(),
                "markets": [
                    {
                        "key": "h2h" if change == "wrong_line" else "totals",
                        "outcomes": [{"name": "Over", "price": 1.9, "point": 2.5}],
                    }
                ],
            }
        ],
    }

    def response(url, params):
        calls.append(url)
        assert params["regions"] == "uk" and params["markets"] == "totals"
        return 200, [event]

    client_patch(monkeypatch, "app.providers.the_odds_daily", response)
    p = TheOddsDailyProvider(None)
    result, stats, _ = asyncio.run(p.fetch_offers([m]))
    assert bool(result) == (change == "valid")
    assert len(calls) == (0 if change == "missing_key" else 1)
    if result:
        assert result[m.match_key][0].source == "the_odds"


def test_backup_shared_budget_blocks_when_server_reports_zero(tmp_path, monkeypatch):
    import httpx

    from app.services.api_budget import ApiBudget, request_identity

    monkeypatch.setenv("PUBLICATION_PROFILE", "daily_quality")
    monkeypatch.setenv("API_BUDGET_ENABLED", "true")
    budget = ApiBudget(tmp_path / "budget.json")
    request = httpx.Request("GET", "https://api.the-odds-api.com/v4/sports/soccer_epl/odds?apiKey=test-only")
    identity = request_identity(request)
    now = datetime.now(UTC)
    assert budget.reserve(*identity, now=now)[0] == "allowed"
    budget.observe(identity, httpx.Response(200, headers={"x-requests-remaining": "0"}, request=request), now=now)
    assert budget.reserve(*identity, now=now + timedelta(seconds=30))[0] != "allowed"


@pytest.mark.parametrize("source,book", [("sstats_pari", "Pari"), ("the_odds", "William Hill")])
def test_final_confirmation_routes_to_actual_quote_source(source, book):
    from collections import Counter

    from app.schemas import Offer
    from app.services.daily_quality_runner import DailyQualityRunner

    now = datetime.now(UTC)
    m = fixture(now)
    calls = []

    class Quotes:
        async def fetch_offers(self, matches):
            calls.append(source)
            return (
                {
                    m.match_key: [
                        Offer(
                            source,
                            book,
                            "totals",
                            "Over",
                            1.9,
                            2.5,
                            metadata={"fetched_at_utc": datetime.now(UTC).isoformat()},
                        )
                    ]
                },
                {},
                {},
            )

    r = object.__new__(DailyQualityRunner)
    r.inventory_matches = [m]
    r.odds_api_io = None
    r.sstats_pari = Quotes()
    r.the_odds = Quotes()
    r.daily_rejections = Counter()
    r.admitted_sources = {m.match_key: {"sstats": now.isoformat()}}
    r.registry = SimpleNamespace(now=now, cached=lambda *args: SimpleNamespace())
    c = SimpleNamespace(
        match_key=m.match_key,
        commence_time=m.commence_time,
        bookmaker=book,
        family="totals",
        point=2.5,
        selection_key="over",
        adjusted_probability=0.7,
        odds=1.9,
        source_summary={"selected_source": source, "publication_tier": "A"},
    )
    assert asyncio.run(r._confirm_publication_price(c))
    assert calls == [source] and c.source_summary["final_price_confirmation"]["book"] == book


def test_pari_reports_collection_targets_and_deduplicates_provider_events(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAILY_PARI_ENABLED", "true")
    now = datetime.now(UTC)
    m = fixture(now)
    alias = replace(m, home_team="AS Arsenal", home_team_norm="as arsenal")
    later = replace(m, home_team="Later", home_team_norm="later", commence_time=now + timedelta(hours=8))
    calls = []
    row = pari_row(now)

    def response(url, params):
        calls.append(url)
        return 200, {"data": [row], "TotalCount": 1} if url.endswith("/matches") else {"data": row}

    client_patch(monkeypatch, "app.providers.sstats_pari_daily", response)
    p = SStatsPariDailyProvider(SimpleNamespace(sstats_api_key="test-only"))
    result, stats, _ = asyncio.run(p.fetch_offers([alias, m, later]))
    assert set(result) == {m.match_key}
    assert later.match_key in stats["attempted_match_keys"]
    assert stats["duplicate_detail_requests_avoided"] == 1 and 1 <= len(calls) <= 2
    assert all(url.endswith("/matches") for url in calls)
    assert stats["collection_request_limit"] == 8 and stats["confirmation_requests_reserved"] == 4


def test_pari_ambiguous_exact_runtime_aliases_never_duplicate_quotes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAILY_PARI_ENABLED", "true")
    now = datetime.now(UTC)
    m = fixture(now)
    alias = replace(m, home_team="Arsenal FC", home_team_norm="arsenal fc")
    calls = []

    def response(url, params):
        calls.append(url)
        return 200, {"data": [pari_row(now)], "TotalCount": 1}

    client_patch(monkeypatch, "app.providers.sstats_pari_daily", response)
    p = SStatsPariDailyProvider(SimpleNamespace(sstats_api_key="test-only"))
    result, stats, _ = asyncio.run(p.fetch_offers([m, alias]))
    assert not result and len(calls) == 1
    assert stats["skip_reasons"]["ambiguous_runtime_fixture"] == 1
