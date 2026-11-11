import asyncio
import json
from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import httpx
import pytest

from app.providers.odds_api_io import OddsApiIoProvider
from app.schemas import Match
from app.services.daily_match_registry import DailyMatchRegistry, match_identity
from app.services.odds_line_audit import audit_empty_lines, quote_identity_reason


def fixture(index=0):
    return Match("test", str(index), "soccer", "Test League", f"Home {index}", f"Away {index}",
                 datetime.now(UTC) + timedelta(hours=2), f"home {index}", f"away {index}", "test",
                 metadata={"provider_source_ids": {"odds_api_io": str(100 + index)}})


def payload(m, count=4):
    return {"id": int(m.metadata["provider_source_ids"]["odds_api_io"]), "home": m.home_team,
            "away": m.away_team, "date": m.commence_time.isoformat(), "status": "pending", "bookmakerCount": count}


def client_patch(monkeypatch, callback):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def get(self, url, params=None, **kwargs):
            status, body = callback(url, params or {})
            return httpx.Response(status, json=deepcopy(body), request=httpx.Request("GET", url))

    monkeypatch.setattr("app.providers.odds_api_io.BudgetedAsyncClient", Client)


def provider(monkeypatch, tmp_path, max_fetch=40):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUBLICATION_PROFILE", "daily_quality")
    monkeypatch.setenv("ODDS_API_IO_DISCOVER_SELECTED_BOOKMAKERS", "false")
    p = OddsApiIoProvider(SimpleNamespace(odds_api_io_key="test-only", odds_api_io_key_2="",
                         odds_api_io_bookmakers_account1=["Unibet", "William Hill"], odds_api_io_per_run_max=40,
                         max_matches_for_odds_fetch=max_fetch))
    monkeypatch.setattr(p, "_account_quota_enabled", lambda: False)
    return p


@pytest.mark.parametrize("count,reason", [(4, "selected_books_gap"), (0, "no_bookmakers_listed"), (None, "bookmaker_count_unknown"), (True, "bookmaker_count_unknown")])
def test_empty_snapshot_is_classified_by_separate_metadata_not_invented_odds(tmp_path, monkeypatch, count, reason):
    p = provider(monkeypatch, tmp_path)
    m, calls = fixture(), []

    def response(url, params):
        calls.append(url)
        return (200, []) if url.endswith("/odds/multi") else (200, payload(m, count))

    client_patch(monkeypatch, response)
    offers, stats, preview = asyncio.run(p.fetch_offers([m]))
    assert not offers
    assert len(calls) == 2 and stats["odds_requests"] == stats["line_availability_audit"]["requests"] == 1
    d = preview["line_diagnostics"][m.match_key]
    assert d["reason"] == "no_quotes_from_selected_books" and d["availability"]["reason"] == reason
    assert stats["attempted_match_keys"] == [m.match_key]
    assert "test-only" not in json.dumps(d)
    r = DailyMatchRegistry(tmp_path / "registry.json", datetime.now(UTC))
    r.sync([m])
    r.record("odds_api_io", "offers", [m], {}, stats)
    assert r.data["matches"][match_identity(m)]["attempts"]["odds_api_io:offers"]["diagnostic"] == d
    assert r.actionable_gaps()[0]["attempts"]["odds_api_io:offers"]["diagnostic"] == d


def test_metadata_audit_reuses_receipt_but_revalidates_identity(tmp_path, monkeypatch):
    p = provider(monkeypatch, tmp_path)
    m, calls = fixture(), []

    def response(url, params):
        calls.append(url)
        return (200, []) if url.endswith("/odds/multi") else (200, payload(m))

    client_patch(monkeypatch, response)
    asyncio.run(p.fetch_offers([m]))
    _, stats, _ = asyncio.run(p.fetch_offers([m]))
    assert stats["line_availability_audit"]["cache_hits"] == 1
    other = replace(m, home_team="Different Club")
    _, _, preview = asyncio.run(p.fetch_offers([other]))
    assert preview["line_diagnostics"][other.match_key]["availability"]["reason"] == "cached_fixture_identity_mismatch"
    assert len([x for x in calls if "/events/" in x]) == 1


@pytest.mark.parametrize("change,expected", [("valid", None), ("live", "event_not_pending"), ("id", "event_id_mismatch"), ("reverse", "fixture_identity_mismatch"), ("time", "fixture_identity_mismatch"), ("squad", "fixture_identity_mismatch")])
def test_quote_receipt_requires_exact_pending_fixture(change, expected):
    m = fixture()
    raw = payload(m)
    if change == "live":
        raw["status"] = "live"
    elif change == "id":
        raw["id"] = 999
    elif change == "reverse":
        raw["home"], raw["away"] = raw["away"], raw["home"]
    elif change == "time":
        raw["date"] = (m.commence_time + timedelta(hours=1)).isoformat()
    elif change == "squad":
        raw["home"] += " U21"
    assert quote_identity_reason(raw, m, 100) == expected


@pytest.mark.parametrize("change", ["valid", "live", "unsupported"])
def test_returned_payload_is_validated_before_quote_parsing(tmp_path, monkeypatch, change):
    p = provider(monkeypatch, tmp_path)
    m = fixture()
    raw = payload(m)
    raw["bookmakers"] = {"Unibet": [{"name": "ML" if change != "unsupported" else "1st Half ML",
                                    "odds": [{"home": "1.9", "draw": "3.2", "away": "4.0"}]}]}
    if change == "live":
        raw["status"] = "live"
    client_patch(monkeypatch, lambda url, params: (200, [raw]))
    offers, stats, preview = asyncio.run(p.fetch_offers([m]))
    assert bool(offers) is (change == "valid")
    assert stats["line_availability_audit"]["requests"] == 0
    d = preview["line_diagnostics"][m.match_key]
    assert d["accounts"]["account1"]["result"] == {"valid": "quotes", "live": "event_not_pending", "unsupported": "no_supported_full_match_markets"}[change]


def test_request_cap_does_not_negative_cache_unqueried_match(tmp_path, monkeypatch):
    p = provider(monkeypatch, tmp_path, max_fetch=1)
    matches = [fixture(0), fixture(1)]
    client_patch(monkeypatch, lambda url, params: (200, []))
    _, stats, preview = asyncio.run(p.fetch_offers(matches))
    assert len(stats["attempted_match_keys"]) == 1
    skipped = next(m for m in matches if m.match_key not in stats["attempted_match_keys"])
    assert preview["line_diagnostics"][skipped.match_key]["reason"] == "not_queried"


@pytest.mark.parametrize("status", [401, 403, 429, 500])
def test_failed_odds_request_never_means_no_bookmakers(tmp_path, monkeypatch, status):
    p = provider(monkeypatch, tmp_path)
    m, calls = fixture(), []

    def response(url, params):
        calls.append(url)
        return status, {"error": "unavailable"}

    client_patch(monkeypatch, response)
    _, stats, preview = asyncio.run(p.fetch_offers([m]))
    assert stats["line_availability_audit"]["requests"] == 0
    assert preview["line_diagnostics"][m.match_key]["reason"] == "incomplete_or_rejected_response"
    assert all("/events/" not in x for x in calls)


def test_audit_caps_requests_and_counts_failures_toward_daily_budget(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = SimpleNamespace(base_url="https://example.test", _request_budget_allows=lambda *args, **kwargs: True,
                        _record_request=lambda **kwargs: None)
    accounts = [{"name": "account1", "api_key": "test-only"}]
    calls = []

    class Client:
        async def get(self, url, **kwargs):
            calls.append(url)
            return httpx.Response(404, json={})

    for batch in range(5):
        matches = [fixture(batch * 6 + i) for i in range(6)]
        mapping = {m.match_key: {"match": m, "event": {"id": 100 + batch * 6 + i}} for i, m in enumerate(matches)}
        diagnostics = {k: {"reason": "no_quotes_from_selected_books"} for k in mapping}
        stats = {}
        asyncio.run(audit_empty_lines(p, Client(), accounts, mapping, diagnostics, stats))
        assert stats["line_availability_audit"]["requests"] <= 4
    assert len(calls) == 16


@pytest.mark.parametrize("status", [401, 429, 500])
def test_metadata_service_failure_stops_optional_probes(tmp_path, monkeypatch, status):
    p = provider(monkeypatch, tmp_path)
    matches, calls = [fixture(i) for i in range(6)], []

    def response(url, params):
        calls.append(url)
        return (200, []) if url.endswith("/odds/multi") else (status, {})

    client_patch(monkeypatch, response)
    offers, stats, _ = asyncio.run(p.fetch_offers(matches))
    assert not offers
    assert stats["line_availability_audit"]["requests"] == 1
    assert len([url for url in calls if "/events/" in url]) == 1


def test_optional_cache_failure_does_not_discard_collected_quotes(tmp_path, monkeypatch):
    p = provider(monkeypatch, tmp_path)
    m = fixture()
    raw = payload(m)
    raw["bookmakers"] = {"Unibet": [{"name": "ML", "odds": [{"home": "1.9", "draw": "3.2", "away": "4.0"}]}]}
    client_patch(monkeypatch, lambda url, params: (200, [raw]))

    async def broken_cache(*args):
        raise OSError("test cache unavailable")

    monkeypatch.setattr("app.services.odds_line_audit.audit_empty_lines", broken_cache)
    offers, stats, _ = asyncio.run(p.fetch_offers([m]))
    assert m.match_key in offers
    assert stats["line_availability_audit"]["status"] == "audit_cache_error"


def test_malformed_odds_response_does_not_become_a_coverage_claim(tmp_path, monkeypatch):
    p = provider(monkeypatch, tmp_path)
    m = fixture()
    client_patch(monkeypatch, lambda url, params: (200, {"unexpected": []}))
    offers, stats, preview = asyncio.run(p.fetch_offers([m]))
    assert not offers and stats["response_errors"] > 0
    assert stats["line_availability_audit"]["requests"] == 0
    assert preview["line_diagnostics"][m.match_key]["reason"] == "incomplete_or_rejected_response"
