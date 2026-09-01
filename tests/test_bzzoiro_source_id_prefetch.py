from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.schemas import Match
from app.services import bzzoiro_context_gap_finalizer as finalizer


def _match(event_id: str = "bzz-123") -> Match:
    return Match(
        source="inventory",
        source_event_id="inventory-1",
        sport_key="soccer",
        league_name="Test League",
        home_team="Alpha FC",
        away_team="Beta SC",
        commence_time=datetime.now(UTC) + timedelta(hours=3),
        home_team_norm="alpha",
        away_team_norm="beta",
        league_key="test-league",
        metadata={"provider_source_ids": {"bzzoiro": event_id}},
    )


def test_odds_only_resource_is_not_counted_as_context() -> None:
    assert finalizer._resources_have_context_information(
        {"odds": {"home_win": 2.0, "away_win": 3.0}}
    ) is False
    assert finalizer._resources_have_context_information(
        {"stats": {"home": {"shots": 7}, "away": {"shots": 4}}}
    ) is True


def test_source_id_prefetch_runs_before_broad_matching(monkeypatch) -> None:
    monkeypatch.setenv("BZZOIRO_API_KEY", "configured")
    monkeypatch.setenv("BZZOIRO_SOURCE_ID_PREFETCH_MATCH_LIMIT", "20")
    monkeypatch.setenv("BZZOIRO_SOURCE_ID_PREFETCH_MAX_REQUESTS", "60")

    async def fake_resources(_client, _headers, event_id, stats, _max_requests):
        stats["requests"] = 3
        stats["stats_resources"] = 1
        assert event_id == "bzz-123"
        return {
            "stats": {"home": {"shots": 7}, "away": {"shots": 4}},
            "metadata": {},
            "lineups": {},
            "odds": {},
        }

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc, _tb):
            return False

    monkeypatch.setattr(finalizer, "_fetch_v2_context_resources", fake_resources)
    monkeypatch.setattr(finalizer.httpx, "AsyncClient", FakeClient)
    provider = SimpleNamespace(
        settings=SimpleNamespace(bzzoiro_api_key="configured", bzzoiro_timeout_seconds=1)
    )

    contexts, stats, _preview = asyncio.run(
        finalizer._source_id_prefetch(provider, [_match()])
    )

    assert len(contexts) == 1
    context = next(iter(contexts.values()))
    assert context.source == "bzzoiro"
    assert context.details["bzzoiro_match_quality"] == "source_id"
    assert context.details["bzzoiro_source_id_prefetch"] is True
    assert stats["requests"] == 3
    assert stats["contexts_added"] == 1
