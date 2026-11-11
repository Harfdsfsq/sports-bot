import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.providers.espn import EspnContextProvider
from app.providers.sstats import SStatsContextProvider, form_team_key
from app.schemas import Match, MatchContext
from app.services.context_admission import admit_context, identity_reasons
from app.services.daily_match_registry import DailyMatchRegistry, match_identity
from app.services.espn_identity import espn_event_identity
from app.services.fixture_identity import history_team_matches, same_history_team_identity
from app.services.sstats_targeted_history import team_bindings

NOW = datetime(2026, 11, 11, 21, tzinfo=UTC)


def fixture(home="CD FAS Santa Ana", away="Inter Santa Tecla", league="El Salvador - Primera Division"):
    return Match("test", "1", "soccer", league, home, away, NOW + timedelta(hours=1), home, away, league, metadata={})


@pytest.mark.parametrize("home,history", [
    ("CD FAS Santa Ana", "Santa Ana"),
    ("Manchester United", "Manchester"),
    ("Real Cartagena", "Cartagena"),
    ("Arsenal U21", "Arsenal"),
])
def test_city_and_squad_are_not_history_identity(home, history):
    assert not same_history_team_identity(home, [history])
    p = SStatsContextProvider(SimpleNamespace())
    assert p._resolve_team_key(home, {history.lower()}, {}) is None


def test_reviewed_spelling_and_abbreviation_survive():
    assert same_history_team_identity("JS Djidjel", ["JS Jijel"])
    assert same_history_team_identity("AGF Aarhus", ["AGF"])


@pytest.mark.parametrize("team", ["DC Power W", "Brooklyn W", "América W", "Toluca W", "Arsenal U21", "Arsenal II"])
def test_generated_squad_key_is_not_treated_as_a_provider_alias(team):
    provider = SStatsContextProvider(SimpleNamespace())
    key = form_team_key(team)
    assert provider._resolve_team_key(team, {key}, {}) == key


def test_womens_form_is_rebuilt_with_verified_ids():
    m = fixture("DC Power W", "Brooklyn W", "USL Super League")
    receipt = {"id": 42, "date": m.commence_time.isoformat(),
               "homeTeam": {"id": 24659, "name": "DC Power W", "country": {"code": "US"}},
               "awayTeam": {"id": 24657, "name": "Brooklyn W", "country": {"code": "US"}}}
    m.metadata.update({"sstats_fixture": receipt, "provider_source_ids": {"sstats": "42"}})
    rows = [{"id": i + 1, "date": (NOW - timedelta(days=i + 1)).isoformat(), "status": 8,
             "homeTeam": receipt["homeTeam"], "awayTeam": receipt["awayTeam"],
             "homeFTResult": 1, "awayFTResult": 2, "season": {"league": {"name": "USL Super League"}}}
            for i in range(3)]
    p = SStatsContextProvider(SimpleNamespace())
    contexts = p._build_team_form_contexts([m], rows, {"skip_goal_history_archive": True, "team_form_examples": []})
    assert m.match_key in contexts
    assert identity_reasons("sstats", m, contexts[m.match_key]) == []
    assert contexts[m.match_key].payload["home_recent"][0]["team_id"] == 24659


@pytest.mark.parametrize("result,revision,expected", [
    ("empty", None, True), ("empty", 2, False),
    ("rate_limited", None, False), ("unavailable", None, False), ("deferred", None, False),
])
def test_squad_key_repair_retries_old_empty_once_but_respects_backoff(tmp_path, result, revision, expected):
    m = fixture("DC Power W", "Brooklyn W")
    registry = DailyMatchRegistry(tmp_path / "registry.json", NOW)
    registry.sync([m])
    registry.record("sstats", "context", [m], {}, {}, observed_at=NOW)
    attempt = registry.data["matches"][match_identity(m)]["attempts"]["sstats:context"]
    assert attempt["history_identity_revision"] == 2
    attempt["result"] = result
    if revision is None:
        attempt.pop("history_identity_revision")
    assert bool(registry.targets("sstats", "context")) is expected
    if expected:
        registry.record("sstats", "context", [m], {}, {}, observed_at=NOW)
        assert not registry.targets("sstats", "context")


def test_contaminated_cached_form_is_quarantined_without_deleting_receipt(tmp_path):
    m = fixture()
    row = {"team": "Santa Ana", "team_key": "santa ana", "start": (NOW - timedelta(days=1)).isoformat()}
    context = MatchContext("sstats_form", payload={"home_recent": [row], "away_recent": [{**row, "team": "Inter Santa Tecla"}]})
    registry = DailyMatchRegistry(tmp_path / "registry.json", NOW)
    registry.sync([m])
    evidence = {"observed_at": NOW.isoformat(), "payload": {"source": context.source, "payload": context.payload}}
    registry.data["matches"][match_identity(m)]["evidence"]["sstats:context"] = deepcopy(evidence)
    assert registry.cached("sstats", "context", m) is None
    assert registry.coverage()["collected_context"] == 0
    assert registry.data["matches"][match_identity(m)]["evidence"]["sstats:context"] == evidence
    assert identity_reasons("sstats", m, context) == ["context_team_identity_mismatch"]
    rows = [{"homeTeam": {"id": 3, "name": "Santa Ana"}, "awayTeam": {"id": 4, "name": "Inter Santa Tecla"}}]
    assert "3" not in team_bindings([m], rows)


@pytest.mark.parametrize("row_id,country,ok", [(10, "SV", True), (11, "SV", False), (None, "SV", False), (10, "CR", False)])
def test_history_uses_verified_id_and_preserves_provider_name(row_id, country, ok):
    m = fixture(home="Central SC PE", away="Belo Jardim", league="Brazil Pernambucano")
    receipt = {"id": 42, "date": m.commence_time.isoformat(),
               "homeTeam": {"id": 10, "name": "Central SC", "country": {"code": "SV"}},
               "awayTeam": {"id": 20, "name": "Belo Jardim"}}
    m.metadata.update({"sstats_fixture": receipt, "provider_source_ids": {"sstats": "42"}})
    row = {"team": "Central SC", "team_id": row_id, "team_country": country}
    assert history_team_matches(m, "home", row) is ok


@pytest.mark.parametrize("league,expected", [
    ("USA - America East Conference, Playoffs", "usa.ncaa.m.1"),
    ("USA - Big Ten Conference, Playoffs", "usa.ncaa.m.1"),
    ("USA - Summit League, Playoffs", "usa.ncaa.m.1"),
    ("NCAA Women's Soccer", "usa.ncaa.w.1"),
    ("England Championship", "eng.2"),
    ("UEFA Women's Champions League", None),
    ("UEFA U19 Champions League", None),
    ("Brazil Copa Paraiba", None),
])
def test_only_supported_competitions_are_routed(league, expected):
    assert EspnContextProvider(SimpleNamespace(espn_base_site_url="", espn_base_core_url="", espn_timeout_seconds=5, espn_soccer_leagues=[]))._league_slug(league) == expected


def ncaa_event():
    payload = json.loads((Path(__file__).parent / "fixtures/espn_ncaa_scoreboard_20261111.json").read_text())
    return next(e for e in payload["events"] if any(c["team"]["id"] == "5693" for c in e["competitions"][0]["competitors"]))


@pytest.mark.parametrize("change,accepted", [("valid", True), ("wrong_id", False), ("wrong_slug", False), ("reverse", False), ("women", False), ("late", False), ("live", False)])
def test_ncaa_aliases_are_scoped_to_id_league_orientation_and_kickoff(monkeypatch, change, accepted):
    monkeypatch.setenv("PUBLICATION_PROFILE", "daily_quality")
    e = ncaa_event()
    m = fixture("University At Albany Great Danes", "Njit Highlanders", "USA - America East Conference, Playoffs")
    e["date"] = m.commence_time.isoformat()
    slug = "usa.ncaa.m.1"
    if change == "wrong_id":
        e["competitions"][0]["competitors"][0]["team"]["id"] = "999999"
    elif change == "wrong_slug":
        slug = "usa.ncaa.w.1"
    elif change == "reverse":
        for c in e["competitions"][0]["competitors"]:
            c["homeAway"] = "away" if c["homeAway"] == "home" else "home"
    elif change == "women":
        m = replace(m, home_team=m.home_team + " W")
    elif change == "late":
        e["date"] = (m.commence_time + timedelta(hours=2)).isoformat()
    elif change == "live":
        e["status"] = {"type": {"state": "in"}}
    p = EspnContextProvider(SimpleNamespace(espn_base_site_url="", espn_base_core_url="", espn_timeout_seconds=5, espn_soccer_leagues=[]))
    result, _, _ = p._match_event(m, [(slug, e)])
    assert (result is not None) is accepted
    c = MatchContext("espn", payload={"event": e}, details={"espn_league_slug": slug})
    assert (not admit_context("espn", m, c, NOW.isoformat(), NOW)) is accepted


def test_ncaa_scoreboard_form_needs_no_per_match_requests(monkeypatch):
    monkeypatch.setenv("PUBLICATION_PROFILE", "daily_quality")
    e = ncaa_event()
    m = fixture("University At Albany Great Danes", "Njit Highlanders")
    e["date"] = m.commence_time.isoformat()

    class NoRequests:
        async def get(self, *args, **kwargs):
            raise AssertionError("College scoreboard form must not request unavailable model probabilities")

    stats = {"partial_contexts_built": 0}
    p = EspnContextProvider(SimpleNamespace(espn_base_site_url="", espn_base_core_url="", espn_timeout_seconds=5, espn_soccer_leagues=[]))
    c = asyncio.run(p._build_context(NoRequests(), m, "usa.ncaa.m.1", e, stats, {}))
    assert c and c.details["espn_scoreboard_only"] and not c.details["espn_partial_context"]
    assert not admit_context("espn", m, c, NOW.isoformat(), NOW)
    for competitor in e["competitions"][0]["competitors"]:
        competitor.pop("form", None)
    assert asyncio.run(p._build_context(NoRequests(), m, "usa.ncaa.m.1", e, stats, {})) is None


def test_duplicate_receipts_and_partial_defaults_are_not_context(monkeypatch):
    monkeypatch.setenv("PUBLICATION_PROFILE", "daily_quality")
    e = ncaa_event()
    m = fixture("University At Albany Great Danes", "Njit Highlanders")
    e["date"] = m.commence_time.isoformat()
    p = EspnContextProvider(SimpleNamespace(espn_base_site_url="", espn_base_core_url="", espn_timeout_seconds=5, espn_soccer_leagues=[]))
    assert p._match_event(m, [("usa.ncaa.m.1", e), ("usa.ncaa.m.1", {**e, "id": "other"})])[0] is None
    c = MatchContext("espn", payload={"event": e}, details={"espn_league_slug": "usa.ncaa.m.1", "espn_partial_context": True})
    assert identity_reasons("espn", m, c) == ["partial_sporting_context"]
    assert "University At Albany Great Danes" in espn_event_identity(e, "usa.ncaa.m.1")["home_aliases"]


def test_ncaa_fetch_routes_conference_and_uses_one_shared_scoreboard(monkeypatch):
    import httpx

    monkeypatch.setenv("PUBLICATION_PROFILE", "daily_quality")
    e = ncaa_event()
    m = fixture("University At Albany Great Danes", "Njit Highlanders", "USA - America East Conference, Playoffs")
    e["date"] = m.commence_time.isoformat()
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params=None):
            calls.append((url, params))
            assert url.endswith("/usa.ncaa.m.1/scoreboard")
            assert params == {"dates": "20261111", "limit": 1000}
            return httpx.Response(200, json={"events": [e]}, request=httpx.Request("GET", url))

    monkeypatch.setattr("app.providers.espn.BudgetedAsyncClient", Client)
    settings = SimpleNamespace(espn_base_site_url="", espn_base_core_url="", espn_timeout_seconds=5,
                               espn_soccer_leagues=[], enable_espn_context=True, espn_max_matches=120, run_days_ahead=4)
    contexts, stats, _ = asyncio.run(EspnContextProvider(settings).fetch_context([m]))
    assert len(calls) == stats["scoreboard_requests"] == 1
    assert stats["summary_requests"] == stats["probability_requests"] == 0
    assert m.match_key in contexts
    assert not admit_context("espn", m, contexts[m.match_key], NOW.isoformat(), NOW)


@pytest.mark.parametrize("sid,accepted", [(10, True), (11, False)])
def test_form_builder_keeps_receipt_ids_and_provider_names(sid, accepted):
    m = fixture("Central SC PE", "Belo Jardim", "Brazil Pernambucano")
    receipt = {"id": 42, "date": m.commence_time.isoformat(),
               "homeTeam": {"id": 10, "name": "Central SC", "country": {"code": "BR"}},
               "awayTeam": {"id": 20, "name": "Belo Jardim", "country": {"code": "BR"}}}
    m.metadata.update({"sstats_fixture": receipt, "provider_source_ids": {"sstats": "42"}})
    rows = [{"id": i + 1, "date": (NOW - timedelta(days=i + 1)).isoformat(), "status": 8,
             "homeTeam": {**receipt["homeTeam"], "id": sid}, "awayTeam": receipt["awayTeam"],
             "homeFTResult": 1, "awayFTResult": 2, "season": {"league": {"name": "Brazil Pernambucano"}}}
            for i in range(3)]
    p = SStatsContextProvider(SimpleNamespace())
    contexts = p._build_team_form_contexts([m], rows, {"skip_goal_history_archive": True, "team_form_examples": []})
    assert (m.match_key in contexts) is accepted
    if accepted:
        c = contexts[m.match_key]
        assert c.payload["home_recent"][0]["team_id"] == 10
        assert c.payload["home_recent"][0]["team_country"] == "BR"
        assert identity_reasons("sstats", m, c) == []


@pytest.mark.parametrize("changed_path", ["app/services/fixture_identity.py", "config/espn_team_aliases.json"])
def test_forward_fingerprint_includes_identity_code_and_aliases(tmp_path, monkeypatch, changed_path):
    import shutil

    from app.services import prospective_validation as validation

    root = Path(__file__).parents[1]
    for name in ("app", "config"):
        shutil.copytree(root / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(validation, "ROOT", tmp_path)
    before = validation.fingerprint(validation.policy())
    path = tmp_path / changed_path
    if changed_path.endswith(".json"):
        aliases = json.loads(path.read_text())
        aliases["usa.ncaa.m.1"]["5693"]["aliases"].append("Another Alias")
        path.write_text(json.dumps(aliases))
    else:
        path.write_text(path.read_text() + "\nIDENTITY_INPUT_VERSION = 2\n")
    assert validation.fingerprint(validation.policy()) != before
