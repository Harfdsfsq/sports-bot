from __future__ import annotations

from datetime import datetime, timedelta, timezone

from app.schemas import Match, Offer
from app.services import targeted_enrichment_queue as q

UTC = timezone.utc


def _match(name: str, hours: int, league: str = "Test League") -> Match:
    home, away = name.split("-")
    return Match(
        source="test",
        source_event_id=name,
        sport_key="soccer",
        league_name=league,
        home_team=home,
        away_team=away,
        commence_time=datetime.now(UTC) + timedelta(hours=hours),
        home_team_norm="",
        away_team_norm="",
        league_key="",
        tier="mid",
    )


def _offer(source: str = "odds_api_io") -> Offer:
    return Offer(
        source=source,
        bookmaker="Betfair",
        family="totals",
        selection="Over",
        price=2.0,
        point=2.5,
        market_name="totals",
        market_key="totals",
    )


def test_provider_limit_uses_per_run_env(monkeypatch):
    monkeypatch.setenv("TARGETED_ENRICHMENT_BZZOIRO_MATCH_LIMIT", "2")
    monkeypatch.setenv("TARGETED_ENRICHMENT_MAX_MATCHES_PER_PROVIDER", "5")
    assert q.provider_limit("bzzoiro") == 2


def test_targeted_queue_prefers_near_offered_matches(monkeypatch):
    monkeypatch.setenv("TARGETED_ENRICHMENT_THESPORTSDB_MATCH_LIMIT", "2")
    far = _match("Far-Away", 30)
    soon = _match("Soon-Away", 2)
    mid = _match("Mid-Away", 8)
    offers = {soon.match_key: [_offer()], mid.match_key: [_offer()]}
    selected, info = q.select_for_provider([far, mid, soon], "thesportsdb", offers)
    assert [m.match_key for m in selected] == [soon.match_key, mid.match_key]
    assert info["selected_matches"] == 2
