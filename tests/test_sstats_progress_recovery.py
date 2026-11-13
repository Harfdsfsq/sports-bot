import asyncio
import importlib
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from app.config import Settings
from app.providers.sstats import SStatsContextProvider
from app.schemas import Match
from app.services.context_admission import admit_context
from app.services.daily_match_registry import DailyMatchRegistry


def fixture(now):
    return Match("test", "1", "soccer", "Test League", "Home Team", "Away Team",
                 now + timedelta(hours=2), "home team", "away team", "test", metadata={})


def history(now):
    return [{"id": i + 1, "date": (now - timedelta(days=i + 1)).isoformat(), "status": 8,
             "homeTeam": {"id": 10, "name": "Home Team"}, "awayTeam": {"id": 11, "name": "Away Team"},
             "homeFTResult": 1, "awayFTResult": 2, "season": {"league": {"name": "Test League"}}}
            for i in range(3)]


def setup(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUBLICATION_PROFILE", "daily_quality")
    monkeypatch.setenv("DAILY_SSTATS_BROAD_EXTENSION_ENABLED", "false")

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    monkeypatch.setattr("app.providers.sstats.BudgetedAsyncClient", Client)
    return Settings(_env_file=None, sstats_api_key="test-only", enable_sstats_context=True)


@pytest.mark.parametrize("age,accepted", [(2, True), (7, False)])
def test_initial_timeout_keeps_completed_pages_without_rejuvenating_receipt(monkeypatch, tmp_path, age, accepted):
    p = SStatsContextProvider(setup(monkeypatch, tmp_path))
    now = datetime.now(UTC)
    m, rows = fixture(now), history(now)
    stamp = (now - timedelta(hours=age)).isoformat()

    async def slow_history(client, start, end, stats):
        p._daily_partial_rows = rows
        p._daily_partial_observed_at = stamp
        stats["requests"] = 2
        raise TimeoutError

    async def no_extension(provider, matches, rows, stats, preview):
        stats["targeted_history"] = {}
        return {}

    monkeypatch.setattr(p, "_fetch_rows", slow_history)
    monkeypatch.setattr(importlib.import_module("app.services.sstats_targeted_history"), "enrich_team_form", no_extension)
    contexts, stats, _ = asyncio.run(p.fetch_context([m]))
    assert stats["initial_history_timeout"] and stats["initial_history_partial_rows"] == 3
    assert stats["requests"] == 2
    assert contexts[m.match_key].details["daily_source_observed_at"] == stamp
    assert (not admit_context("sstats", m, contexts[m.match_key], stamp, now)) is accepted


@pytest.mark.parametrize("age,accepted", [(2, True), (7, False)])
def test_outer_deadline_salvages_completed_context_through_normal_admission(monkeypatch, tmp_path, age, accepted):
    from app.services.daily_quality_runner import DailyQualityRunner

    settings = setup(monkeypatch, tmp_path)
    monkeypatch.setenv("DAILY_PROVIDER_TIMEOUT_SECONDS_SSTATS", "0.02")
    now = datetime.now(UTC)
    m, rows = fixture(now), history(now)
    r = DailyQualityRunner(settings)
    r.registry = DailyMatchRegistry(tmp_path / "registry.json", now)
    r.registry.sync([m])
    r.inventory_matches = [m]
    p = r.sstats
    stamp = (now - timedelta(hours=age)).isoformat()

    async def initial(client, start, end, stats):
        p._daily_source_observed_at = stamp
        stats["requests"] = 2
        return rows

    async def hangs(provider, matches, rows, stats, preview):
        stats["targeted_history"] = {"started": True}
        await asyncio.Event().wait()

    async def no_tables(provider, matches, contexts, stats):
        return contexts

    monkeypatch.setattr(p, "_fetch_rows", initial)
    monkeypatch.setattr(importlib.import_module("app.services.sstats_targeted_history"), "enrich_team_form", hangs)
    monkeypatch.setattr(importlib.import_module("app.services.sstats_standings"), "enrich_standings", no_tables)
    data, stats, _ = asyncio.run(r._fetch_provider(p, "fetch_context", [m], empty_data={}))
    assert stats["runtime_error"] == "provider_deadline"
    assert stats["salvaged_contexts"] == 1 and stats["requests"] == 2
    assert (m.match_key in data) is accepted
    if accepted:
        assert data[m.match_key].details["daily_source_observed_at"] == stamp
    else:
        assert stats["registry_record"]["rejected"] == 1


def test_page_checkpoint_survives_timeout_on_following_page(monkeypatch, tmp_path):
    p = SStatsContextProvider(setup(monkeypatch, tmp_path))
    now, calls = datetime.now(UTC), []
    rows = history(now)

    class Response:
        def json(self):
            return {"data": rows, "count": 3}

    async def request(client, url, params, stats):
        calls.append(params["offset"])
        if len(calls) == 2:
            raise TimeoutError
        return Response()

    monkeypatch.setattr(p, "_request_with_retries", request)
    with pytest.raises(TimeoutError):
        asyncio.run(p._fetch_rows_window(None, "2026-11-01", "2026-11-10", {"payload_shapes": []}))
    assert calls == [0, 3] and p._daily_partial_rows == rows
    assert p._daily_partial_observed_at
    assert not list(Path(".data/provider_cache").glob("sstats-history*.json"))
