import asyncio
import importlib
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest

from app.services.daily_match_registry import DailyMatchRegistry, match_identity
from app.services.daily_quality_runner import DailyQualityRunner
from tests.test_free_api_enrichment import fixture, history_rows, targeted_provider


def setup_runner(tmp_path):
    now = datetime.now(UTC)
    m, p = fixture(now), targeted_provider()
    context = p._build_team_form_contexts([m], history_rows(now), {"team_form_examples": []})[m.match_key]
    stamp = (now - timedelta(hours=1)).isoformat()
    context.details["daily_source_observed_at"] = stamp
    r = DailyQualityRunner.__new__(DailyQualityRunner)
    r.registry = DailyMatchRegistry(tmp_path / "registry.json", now)
    r.registry.sync([m])
    r.registry.record("sstats", "context", [m], {m.match_key: context}, {}, observed_at=now)
    r.inventory_matches, r.work_plan, r.settings = [m], {}, p.settings
    r._provider_name = lambda _: "sstats"
    return r, p, m, context, stamp


@pytest.mark.parametrize("change", ["normal", "removed", "expired", "replaced"])
def test_standings_never_resurrect_or_overwrite_obsolete_form(tmp_path, monkeypatch, change):
    monkeypatch.chdir(tmp_path)
    r, p, m, context, stamp = setup_runner(tmp_path)
    row = r.registry.data["matches"][match_identity(m)]
    previous = deepcopy(row["evidence"]["sstats:context"])

    async def standings(provider, matches, contexts, stats):
        contexts[m.match_key].details["sstats_standings"] = {"home_rank": 1, "away_rank": 2}
        contexts[m.match_key].payload["verified_standings"] = {"home": {}, "away": {}}
        # Mutation of a detached enrichment copy must not alter stored form.
        assert row["evidence"]["sstats:context"]["observed_at"] == previous["observed_at"]
        assert "sstats_standings" not in row["evidence"]["sstats:context"]["payload"]["details"]
        assert "verified_standings" not in row["evidence"]["sstats:context"]["payload"]["payload"]
        if change == "removed":
            row["evidence"].pop("sstats:context")
        elif change == "expired":
            row["evidence"]["sstats:context"]["observed_at"] = (datetime.now(UTC) - timedelta(hours=7)).isoformat()
        elif change == "replaced":
            new = replace(context, expected_home=2.6, details={**context.details,
                          "daily_source_observed_at": (datetime.now(UTC) - timedelta(minutes=20)).isoformat()})
            r.registry.record("sstats", "context", [m], {m.match_key: new}, {})

    monkeypatch.setattr(importlib.import_module("app.services.sstats_standings"), "enrich_standings", standings)
    result, _, _ = asyncio.run(r._fetch_provider(p, "fetch_context", [], empty_data={}))
    if change == "normal":
        assert result[m.match_key].details["sstats_standings"]["home_rank"] == 1
        assert row["evidence"]["sstats:context"]["observed_at"] == stamp
    elif change in {"removed", "expired"}:
        assert m.match_key not in result
        if change == "removed":
            assert "sstats:context" not in row["evidence"]
    else:
        assert result[m.match_key].expected_home == 2.6
        assert "sstats_standings" not in result[m.match_key].details


def test_same_day_kickoff_collision_is_visible_but_never_transfers_evidence(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    r, p, first, _, _ = setup_runner(tmp_path)
    second = replace(first, commence_time=first.commence_time + timedelta(hours=1))
    assert first.match_key == second.match_key and match_identity(first) != match_identity(second)
    r.registry.sync([second])
    r.inventory_matches = [first, second]
    snapshot = deepcopy(r.registry.data["matches"])
    assert len(r.registry.matches()) == 2
    assert not r.registry.runtime_matches()
    assert not r.registry.targets("sstats", "context")
    result, stats, _ = asyncio.run(r._fetch_provider(p, "fetch_context", [], empty_data={}))
    assert result == {} and stats["assigned_matches"] == 0
    assert r.registry.data["matches"] == snapshot
    assert "sstats:context" not in r.registry.data["matches"][match_identity(second)]["evidence"]
    coverage = r.registry.coverage()
    assert coverage["runtime_identity_quarantined"] == 2
    assert coverage["inventory"] == 2 and coverage["near_ready"] == 0
    # Once authoritative inventory correction removes the conflicting record,
    # the original receipt becomes usable without moving or re-dating it.
    r.registry.data["matches"].pop(match_identity(second))
    assert len(r.registry.runtime_matches()) == 1
    assert r.registry.cached("sstats", "context", first)
