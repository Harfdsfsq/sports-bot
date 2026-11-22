from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.schemas import Match, MatchContext
from app.services.daily_match_registry import DailyMatchRegistry, match_identity
from app.services.fixture_identity import direct_fixture_identity, history_team_name, same_history_team_identity
from app.services.odds_line_audit import quote_identity_reason
from app.services.reviewed_fixture_aliases import fixture_view, scoped_fixture_alias_match
from app.services.sstats_targeted_history import verified_fixture

NOW = datetime(2026, 11, 16, 7, tzinfo=UTC)


def matches(home="Inhulets", away="Probiy Horodenka", other_home="FC Ingulets Petrove", other_away="Probiy Gorodenka"):
    start = NOW + timedelta(hours=2)
    fixture = {"id": 10, "date": start.isoformat(), "homeTeam": {"id": 1, "name": home},
               "awayTeam": {"id": 2, "name": away},
               "season": {"league": {"name": "Persha Liga", "country": {"code": "UA"}}}}
    a = Match("test", "10", "soccer", "Persha Liga", home, away, start,
              home.lower(), away.lower(), "persha liga", metadata={"provider_source_ids": {"sstats": "10"}, "sstats_fixture": fixture})
    b = Match("test", "20", "soccer", "Ukraine - Persha Liga", other_home, other_away, start,
              other_home.lower(), other_away.lower(), "ukraine persha liga", metadata={"provider_source_ids": {"odds_api_io": "20"}})
    return a, b


def quote(match):
    return {"id": 20, "status": "pending", "home": match.home_team, "away": match.away_team,
            "date": match.commence_time.isoformat(), "league": {"name": match.league_name}}


@pytest.mark.parametrize("teams", [(), ("UCSA", "Polissya II", "FC Ucsa Tarasivka", "FC Polissya Zhytomyr 2"),
                                   ("Chernihiv", "Lokomotiv Kyiv", "FC Chernigiv", "FC Lokomotiv Kyiv")])
def test_reviewed_both_team_receipts_allow_exact_quote_identity(teams):
    a, b = matches(*teams)
    assert scoped_fixture_alias_match(a, fixture_view(b))
    assert direct_fixture_identity(a, fixture_view(b))
    assert quote_identity_reason(quote(b), a, 20) is None
    # No global history alias or canonical team key is changed by this rule.
    if not teams:
        assert not same_history_team_identity(a.home_team, [b.home_team])


@pytest.mark.parametrize("problem", ["opponent", "orientation", "kickoff", "league", "country", "unwitnessed_country", "youth", "women", "reserve", "event_id", "status"])
def test_scoped_alias_does_not_bypass_other_identity_checks(problem):
    a, b = matches()
    payload = quote(b)
    if problem == "opponent":
        payload["away"] = "Different club"
    elif problem == "orientation":
        payload["home"], payload["away"] = payload["away"], payload["home"]
    elif problem == "kickoff":
        payload["date"] = (b.commence_time + timedelta(minutes=1)).isoformat()
    elif problem == "league":
        payload["league"]["name"] = "Ukraine - Premier League"
    elif problem == "country":
        a.metadata["sstats_fixture"]["season"]["league"]["country"]["code"] = "RU"
    elif problem == "unwitnessed_country":
        a.metadata["provider_source_ids"]["sstats"] = "wrong"
    elif problem in {"youth", "women", "reserve"}:
        payload["home"] += {"youth": " U21", "women": " W", "reserve": " II"}[problem]
    elif problem == "event_id":
        payload["id"] = 21
    else:
        payload["status"] = "live"
    assert quote_identity_reason(payload, a, 20) is not None


def test_merge_retains_provider_receipts_and_binds_form_to_sstats_id(tmp_path):
    a, b = matches()
    # Exercise the harder order: the retained label originates in the odds feed.
    registry = DailyMatchRegistry(tmp_path / "registry.json", NOW)
    registry.sync([b])
    old_key = next(iter(registry.data["matches"]))
    registry.data["matches"][old_key]["evidence"]["quote_receipt"] = {"observed_at": NOW.isoformat()}
    registry.sync([a])
    assert len(registry.matches()) == 1
    merged = registry.matches()[0]
    assert merged.metadata["provider_source_ids"] == {"odds_api_io": "20", "sstats": "10"}
    assert verified_fixture(merged)["id"] == 10
    assert history_team_name(merged, "home") == "Inhulets"
    assert registry.data["matches"][old_key]["evidence"]["quote_receipt"]["observed_at"] == NOW.isoformat()
    # Reloading must not recreate the duplicate or alter receipt timestamps.
    reloaded = DailyMatchRegistry(registry.path, NOW)
    reloaded.sync([a, b])
    assert len(reloaded.matches()) == 1


@pytest.mark.parametrize("conflict", ["id", "two_publications", "kickoff"])
def test_alias_merge_preserves_conflicts_for_review(tmp_path, conflict):
    a, b = matches()
    if conflict == "id":
        b.metadata["provider_source_ids"]["sstats"] = "other"
    if conflict == "kickoff":
        b = replace(b, commence_time=b.commence_time + timedelta(hours=1))
    registry = DailyMatchRegistry(tmp_path / "registry.json", NOW)
    registry.sync([a, b] if conflict != "two_publications" else [a])
    if conflict == "two_publications":
        saved = deepcopy(registry.data["matches"])
        other = DailyMatchRegistry(tmp_path / "other.json", NOW)
        other.sync([b])
        registry.data["matches"].update(other.data["matches"])
        for row in registry.data["matches"].values():
            row["publication"] = {"sent": True}
        registry.sync([])
        assert saved
    assert len(registry.matches()) == 2


@pytest.mark.parametrize("stamp", [None, "2026-11-16T09:00:00", "bad"])
def test_scoped_alias_requires_an_explicit_offset(stamp):
    a, b = matches()
    event = fixture_view(b)
    event["commence_time"] = stamp
    assert not scoped_fixture_alias_match(a, event)


def test_repaired_scoped_quote_can_retry_once_without_waiting_for_old_empty_cooldown(tmp_path):
    a, b = matches()
    registry = DailyMatchRegistry(tmp_path / "registry.json", NOW)
    registry.sync([a])
    diagnostic = {"event_id": 20, "accounts": {"one": {"result": "fixture_identity_mismatch", "returned_fixture": quote(b)}}}
    registry.record("odds_api_io", "offers", [a], {}, {"line_diagnostics": {a.match_key: diagnostic}}, observed_at=NOW)
    attempt = registry.data["matches"][match_identity(a)]["attempts"]["odds_api_io:offers"]
    attempt["quote_identity_revision"] = 2
    assert registry.targets("odds_api_io", "offers")
    registry.record("odds_api_io", "offers", [a], {}, {"line_diagnostics": {a.match_key: diagnostic}}, observed_at=NOW)
    assert not registry.targets("odds_api_io", "offers")


def test_chile_alias_merge_keeps_verified_history_route_and_pari_identity(tmp_path, monkeypatch):
    monkeypatch.delenv("PUBLICATION_PROFILE", raising=False)
    a, b = matches("Colo Colo", "U. Catolica", "Colo-Colo", "CD Universidad Catolica")
    a = replace(a, league_name="Primera División")
    b = replace(b, league_name="Chile - Primera Division")
    a.metadata["sstats_fixture"]["season"]["league"].update(name=a.league_name, country={"code": "CL"})
    for match in (a, b):
        match.metadata["provider_source_ids"]["sstats_pari"] = "69776911"
    assert not scoped_fixture_alias_match(a, fixture_view(replace(b, league_name="Primera División")))
    assert not scoped_fixture_alias_match(a, fixture_view(replace(b, away_team="CD Universidad Catolica W")))
    assert not scoped_fixture_alias_match(a, fixture_view(replace(b, commence_time=b.commence_time + timedelta(minutes=1))))
    registry = DailyMatchRegistry(tmp_path / "registry.json", NOW)
    donor = DailyMatchRegistry(tmp_path / "sstats.json", NOW)
    for store, match, at in ((registry, b, NOW), (donor, a, NOW - timedelta(minutes=10))):
        store.sync([match])
        context = MatchContext("sstats_form", {
            "home_recent": [{"team": match.home_team, "team_id": 1, "start": (NOW - timedelta(days=4)).isoformat()}],
            "away_recent": [{"team": match.away_team, "team_id": 2, "start": (NOW - timedelta(days=4)).isoformat()}],
        })
        assert store.record("sstats", "context", [match], {match.match_key: context}, {}, observed_at=at)["accepted"] == 1
    registry.data["matches"].update(donor.data["matches"])
    registry.sync([])
    [merged] = registry.runtime_matches()
    assert merged.metadata["provider_source_ids"] == {"odds_api_io": "20", "sstats": "10", "sstats_pari": "69776911"}
    assert verified_fixture(merged)["awayTeam"]["name"] == "U. Catolica"
    cached = registry.cached("sstats", "context", merged)
    assert cached.payload["away_recent"][0]["team"] == "U. Catolica"
    assert cached.details["daily_evidence_observed_at"] == (NOW - timedelta(minutes=10)).isoformat()
