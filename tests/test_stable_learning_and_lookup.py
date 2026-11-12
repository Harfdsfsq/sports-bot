import asyncio
import importlib
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.providers.sstats import SStatsContextProvider
from app.schemas import Match
from app.services.prospective_validation import declare, evaluate
from app.services.sstats_team_lookup import discover, verified_lookup
from app.services.system_readiness import assess
from tests.test_prospective_validation import NOW, cohort


def test_transport_change_preserves_frozen_losses_and_declaration(monkeypatch):
    h, rules, clv = cohort()
    h.data["priced_totals_trials"][0]["total"] = 0
    before = deepcopy(h.data)
    monkeypatch.setattr(importlib.import_module("app.services.validation_lineage"), "pipeline_fingerprint", lambda: "pipeline-2")
    state = declare(h, NOW, rules=rules, code_hash="test")
    assert state == before["prospective_validation"]
    report = evaluate(h, NOW, rules=rules, code_hash="test", clv=clv)
    assert report["cohort"] == 24 and report["metrics"]["paper_pnl"] < 24 * 0.9
    assert h.data["priced_totals_trials"] == before["priced_totals_trials"]


def test_model_change_archives_epoch_without_crediting_old_winners():
    h, rules, clv = cohort()
    rows = deepcopy(h.data["priced_totals_trials"])
    old = deepcopy(h.data["prospective_validation"])
    state = declare(h, NOW, rules=rules, code_hash="new-model")
    report = evaluate(h, NOW, rules=rules, code_hash="new-model", clv=clv)
    assert report["cohort"] == 0 and not report["live_ready"]
    assert state["cohort_id"] != old["cohort_id"]
    assert h.data["previous_validation_protocols"][-1] == old
    assert h.data["priced_totals_trials"] == rows


@pytest.mark.parametrize("field", ["model_hash", "validation_cohort_id"])
def test_unstamped_forecasts_never_authorize_live(field):
    h, rules, clv = cohort()
    for r in h.data["priced_totals_trials"]:
        r.pop(field)
    report = evaluate(h, NOW, rules=rules, code_hash="test", clv=clv)
    assert report["cohort"] == 0 and report["unversioned_excluded"] == 24
    assert not report["live_ready"]


def match():
    return Match("test", "1", "soccer", "Turkmenistan - Yokary Liga", "Arkadag", "Altyn Asyr",
                 datetime.now(UTC) + timedelta(hours=2), "arkadag", "altyn asyr", "tm", metadata={})


@pytest.mark.parametrize("problem", ["valid", "wrong_country", "ambiguous", "wrong_squad", "substring"])
def test_country_catalogue_requires_unique_exact_team(tmp_path, monkeypatch, problem):
    monkeypatch.chdir(tmp_path)
    calls = []

    class Response:
        def json(self):
            rows = [{"id": 21232, "name": "Arkadag", "country": {"code": "TM"}}]
            if problem == "wrong_country":
                rows[0]["country"]["code"] = "TR"
            if problem == "ambiguous":
                rows.append({**rows[0], "id": 999})
            if problem == "wrong_squad":
                rows[0]["name"] += " U21"
            if problem == "substring":
                rows[0]["name"] = "Arkadag City"
            return {"data": rows, "TotalCount": len(rows)}

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    async def request(client, url, params, stats, **kwargs):
        calls.append(params)
        stats["requests"] = stats.get("requests", 0) + 1
        assert url.endswith("/Teams/list") and params["Country"] == "TM"
        return Response()

    monkeypatch.setattr(importlib.import_module("app.services.sstats_team_lookup"), "BudgetedAsyncClient", Client)
    p = SStatsContextProvider(SimpleNamespace(sstats_api_key="test-only"))
    monkeypatch.setattr(p, "_request_with_retries", request)
    result, provenance = asyncio.run(discover(p, [match()], {"22": "Altyn Asyr"}, {}))
    assert bool(result) is (problem == "valid")
    assert len(calls) == 1
    if result:
        m = match()
        assert verified_lookup(m, "home", provenance[m.match_key]["home"])
        before = len(calls)
        asyncio.run(discover(p, [m], {"22": "Altyn Asyr"}, {}))
        assert len(calls) == before


def test_workflow_success_does_not_hide_negative_native_performance():
    summary = {"totals_trial": {"settled": 211, "paper_roi_pct": -7.327},
               "prospective_validation": {"live_ready": False}, "source_stats": {}}
    r = assess(summary, {"near": 31, "near_ready": 10})
    assert r["betting_status"] == "paper_only" and r["data_status"] == "degraded"
    assert "native_paper_model_not_profitable" in r["blockers"]
    assert not r["automatic_live_promotion"]


@pytest.mark.parametrize("change,model_changes", [("transport", False), ("math", True)])
def test_model_fingerprint_tracks_math_but_not_http_transport(tmp_path, monkeypatch, change, model_changes):
    import shutil
    from pathlib import Path

    from app.services import validation_lineage as lineage
    from app.services.prospective_validation import policy

    root = Path(__file__).parents[1]
    for name in ("app", "config"):
        shutil.copytree(root / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    monkeypatch.setattr(lineage, "ROOT", tmp_path)
    before, data = lineage.model_fingerprint(policy()), lineage.pipeline_fingerprint()
    path = tmp_path / "app/providers/sstats.py"
    source = path.read_text()
    if change == "transport":
        source = source.replace('"90"', '"89"')
    else:
        assert '** 0.6' in source
        source = source.replace('** 0.6', '** 0.7')
    path.write_text(source)
    assert (lineage.model_fingerprint(policy()) != before) is model_changes
    assert lineage.pipeline_fingerprint() != data


def test_historical_native_rows_are_not_relabelled_a_tier():
    from app.services.totals_trial import PROTOCOL, summarize
    row = {"validation_protocol": PROTOCOL, "goal_model_version": "test", "family": "totals",
           "reserve_only": True, "published_at": NOW.isoformat(),
           "commence_time_utc": (NOW + timedelta(hours=2)).isoformat(), "status": "lost", "odds": 2.0}
    report = summarize([row], "test")
    assert report["quality_cohorts"]["unclassified"]["settled"] == 1
    assert report["quality_cohorts"]["A"]["settled"] == 0
    assert report["paper_pnl"] == -1


def test_catalogue_to_history_to_admission_is_real_context(tmp_path, monkeypatch):

    import httpx

    from app.services.context_admission import admit_context
    from app.services.sstats_targeted_history import enrich_team_form

    monkeypatch.chdir(tmp_path)
    m, calls = match(), []
    now = datetime.now(UTC)
    teams = [{"id": 10, "name": m.home_team, "country": {"code": "TM"}},
             {"id": 11, "name": m.away_team, "country": {"code": "TM"}}]
    rows = [{"id": i + 1, "date": (now - timedelta(days=i + 1)).isoformat(), "status": 8,
             "homeTeam": teams[0], "awayTeam": teams[1], "homeFTResult": 2, "awayFTResult": 1,
             "season": {"league": {"name": m.league_name}}} for i in range(8)]

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params, **kwargs):
            calls.append((url, params))
            if url.endswith("/Teams/list"):
                data = [t for t in teams if t["name"] == params["Name"]]
                payload = {"data": data, "TotalCount": len(data)}
            else:
                assert params["Team"] == "10,11" and params["Ended"] == "true"
                payload = {"data": rows if params["Offset"] == 0 else []}
            return httpx.Response(200, json=payload, request=httpx.Request("GET", url))

    for name in ("sstats_team_lookup", "sstats_targeted_history"):
        monkeypatch.setattr(importlib.import_module("app.services." + name), "BudgetedAsyncClient", Client)
    p = SStatsContextProvider(SimpleNamespace(sstats_api_key="test-only", sstats_recent_matches=8))
    stats = {"requests": 0, "response_errors": 0}
    contexts = asyncio.run(enrich_team_form(p, [m], [], stats, {"team_form_examples": []}))
    assert len(calls) == 4 and stats["team_catalogue"]["bound"] == 2
    assert contexts[m.match_key].details["home_recent_count"] == 8
    assert not admit_context("sstats", m, contexts[m.match_key], contexts[m.match_key].details["daily_source_observed_at"], datetime.now(UTC))


def test_negative_catalogue_cache_advances_past_uncovered_prefix(tmp_path, monkeypatch):
    from dataclasses import replace

    import httpx

    monkeypatch.chdir(tmp_path)
    calls = []

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params, **kwargs):
            calls.append(params["Name"])
            return httpx.Response(200, json={"data": [], "TotalCount": 0}, request=httpx.Request("GET", url))

    monkeypatch.setattr(importlib.import_module("app.services.sstats_team_lookup"), "BudgetedAsyncClient", Client)
    matches = [replace(match(), home_team=f"Unknown Home {i}", away_team=f"Unknown Away {i}") for i in range(4)]
    p = SStatsContextProvider(SimpleNamespace(sstats_api_key="test-only"))
    stats = {"requests": 0}
    asyncio.run(discover(p, matches, {}, stats))
    assert len(calls) == stats["team_catalogue"]["requests"] == 6
    stats = {"requests": 0}
    asyncio.run(discover(p, matches, {}, stats))
    assert len(calls) == 8 and stats["team_catalogue"]["negative_cache_hits"] == 6
