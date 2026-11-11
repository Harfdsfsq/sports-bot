import asyncio
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.schemas import Offer
from app.services.daily_match_registry import DailyMatchRegistry, match_identity, read_json
from tests.test_native_data_expansion import fixture


def test_shared_verified_event_merges_aliases_and_survives_inventory_reload(tmp_path):
    now = datetime.now(UTC)
    m = fixture(now)
    m.metadata = {"day_inventory_source_ids": {"sstats_pari": "42"}}
    alias = replace(m, home_team="AS Arsenal", home_team_norm="as arsenal")
    r = DailyMatchRegistry(tmp_path / "registry.json", now)
    r.sync([m, alias])
    assert len(r.matches()) == 1 and r.data["aliases"]
    assert not r.has_role(r.matches()[0], "offers")  # An ID is not a price receipt.
    again = DailyMatchRegistry(tmp_path / "registry.json", now)
    again.sync([m, alias])
    assert len(again.matches()) == 1


@pytest.mark.parametrize("difference", ["provider_conflict", "squad", "kickoff"])
def test_shared_event_does_not_override_identity_conflicts(tmp_path, difference):
    now = datetime.now(UTC)
    m = fixture(now)
    m.metadata = {"day_inventory_source_ids": {"sstats_pari": "42", "sstats": "1"}}
    alias = replace(
        m,
        home_team="AS Arsenal",
        home_team_norm="as arsenal",
        metadata={"day_inventory_source_ids": {"sstats_pari": "42", "sstats": "1"}},
    )
    if difference == "provider_conflict":
        alias.metadata["day_inventory_source_ids"]["sstats"] = "2"
    if difference == "squad":
        alias.home_team = "Arsenal U21"
    if difference == "kickoff":
        alias.commence_time += timedelta(minutes=5)
    r = DailyMatchRegistry(tmp_path / "registry.json", now)
    r.sync([m, alias])
    assert len(r.matches()) == 2


def test_alias_merge_retains_publication_acknowledgement(tmp_path):
    now = datetime.now(UTC)
    m = fixture(now)
    alias = replace(m, home_team="AS Arsenal", home_team_norm="as arsenal")
    r = DailyMatchRegistry(tmp_path / "registry.json", now)
    r.sync([m, alias])
    b = match_identity(alias)
    for entry in r.data["matches"].values():
        entry["provider_ids"]["sstats_pari"] = "42"
    r.data["published"] = [{"match_id": b, "tier": "A"}]
    r.data["matches"][b]["publication"] = r.data["published"][0]
    r.sync([m, alias])
    assert len(r.matches()) == 1
    retained = next(iter(r.data["matches"]))
    assert r.data["published"][0]["match_id"] == retained
    assert r.data["matches"][retained]["publication"]["match_id"] == retained


def test_paired_prices_normalize_william_hill_and_count_books_once():
    from app.services.totals_strategy_lab import paired_lines

    now = datetime.now(UTC)
    offers = [
        Offer(source, book, "totals", side, 1.9, 2.5, metadata={"fetched_at_utc": now.isoformat()})
        for source, book in [("odds_api_io", "William Hill"), ("the_odds", "William Hill"), ("sstats_pari", "Pari")]
        for side in ["Over", "Under"]
    ]
    lines = paired_lines(offers, now, {"williamhill", "pari"})
    assert len(lines) == 1 and len(lines[0]["books"]) == 2
    assert {r["book"] for r in lines[0]["books"]} == {"william hill", "pari"}


@pytest.mark.parametrize("stale", [True, False])
def test_pari_closing_watch_is_independent_and_never_restamps_prices(tmp_path, monkeypatch, stale):
    from app.config import Settings
    from app.services import totals_strategy_clv
    from app.services.daily_price_watch import watch_pre_kickoff_prices
    from app.services.market_monitor import MarketMonitor

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("PUBLICATION_PROFILE", "daily_quality")
    now = datetime.now(UTC)
    m = replace(fixture(now), commence_time=now + timedelta(minutes=20))
    r = DailyMatchRegistry(tmp_path / "registry.json", now)
    r.sync([m])
    settings = Settings(_env_file=None)
    settings.storage_export_dir = str(tmp_path / "exports")
    monitor = MarketMonitor(settings)
    totals_strategy_clv.register(
        [
            {
                "id": "paper",
                "match_key": m.match_key,
                "kickoff": m.commence_time.isoformat(),
                "created_at": (now - timedelta(hours=1)).isoformat(),
                "picks": {"opponent": {"point": 2.5, "side": "over", "book": "pari", "odds": 2.0}},
            }
        ],
        now,
    )
    at = (now - timedelta(minutes=2) if stale else now - timedelta(seconds=50)).isoformat()
    quote = Offer("sstats_pari", "Pari", "totals", "Over", 1.9, 2.5, metadata={"fetched_at_utc": at})

    class Primary:
        async def fetch_offers(self, targets):
            raise OSError("unavailable")

    class Pari:
        async def watch_offers(self, targets):
            assert len(targets) == 1
            return {m.match_key: [quote]}, {"requests": 2}, {}

    report = asyncio.run(watch_pre_kickoff_prices(r, monitor, Primary(), pari_provider=Pari()))
    assert report["matches_with_quotes"] == (0 if stale else 1)
    assert report["provider_errors"]["primary_error_type"] == "OSError"
    assert quote.metadata["fetched_at_utc"] == at
    rows = read_json(totals_strategy_clv.PATH, {})
    assert bool(rows["paper|opponent"].get("reference_price")) is not stale
    if not stale:
        assert rows["paper|opponent"]["reference_at"] == at


@pytest.mark.parametrize("problem", ["none", "daily_limit", "drawdown"])
def test_validated_pilot_remains_small_and_stops_on_limits(tmp_path, monkeypatch, problem):
    from app.services.daily_quality_runner import DailyQualityRunner

    monkeypatch.setenv("DAILY_LIVE_A_ONLY", "true")
    now = datetime.now(UTC)
    r = DailyQualityRunner.__new__(DailyQualityRunner)
    r.settings = SimpleNamespace(publish_dry_run=False, max_picks_per_run=2)
    r.registry = DailyMatchRegistry(tmp_path / "registry.json", now)
    if problem == "daily_limit":
        r.registry.data["published"] = [{"tier": "A"}, {"tier": "A"}]
    r.daily_rejections = Counter()
    r._live_admission = lambda _: True
    r.state = SimpleNamespace(
        bankroll_summary=lambda _: {"current_balance": 940 if problem == "drawdown" else 1000, "peak_balance": 1000}
    )
    candidates = [
        SimpleNamespace(
            match_key=str(i),
            source_summary={"publication_tier": "A", "quality_score": 80},
            ev_pct=10,
            commence_time=now + timedelta(hours=1),
            stake_amount=100,
            bankroll_snapshot=1000,
        )
        for i in range(2)
    ]
    selected = r._select_publishable_candidates(candidates)
    assert len(selected) == (1 if problem == "none" else 0)
    if selected:
        assert selected[0].stake_pct == 0.25 and selected[0].stake_amount == 2.5


def test_live_publisher_rechecks_validation_before_any_network_call(monkeypatch):
    from app.services.daily_quality_runner import DailyTelegramPublisher
    from app.services.telegram import TelegramPublisher

    called = []

    async def send(*args, **kwargs):
        called.append(True)
        return 1, []

    monkeypatch.setattr(TelegramPublisher, "publish", send)
    p = DailyTelegramPublisher(SimpleNamespace(publish_dry_run=False))
    p.live_admission = lambda _: False
    rows = [SimpleNamespace(source_summary={})]
    assert asyncio.run(p.publish(rows)) == (0, [])
    assert not called and not rows


def test_quote_only_cache_respects_explicit_dry_run():
    from pathlib import Path

    import yaml

    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/run-bot-rules.yml").read_text())
    value = workflow["jobs"]["run"]["env"]["CACHE_DRY_RUN"]
    assert "inputs.dry_run" in value and "price_watch_only" not in value


def test_native_pari_collects_closing_reference_inside_publication_lead(tmp_path, monkeypatch):
    from app.providers.sstats_pari_daily import SStatsPariDailyProvider
    from tests.test_native_data_expansion import client_patch, pari_row

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAILY_PARI_ENABLED", "true")
    now = datetime.now(UTC)
    m = replace(fixture(now), commence_time=now + timedelta(minutes=20))
    row = pari_row(now)
    row["matchInfo"]["startDate"] = m.commence_time.isoformat()
    calls = []

    def response(url, params):
        calls.append(url)
        return 200, {"data": [row], "TotalCount": 1} if url.endswith("/matches") else {"data": row}

    client_patch(monkeypatch, "app.providers.sstats_pari_daily", response)
    p = SStatsPariDailyProvider(SimpleNamespace(sstats_api_key="test-only"))
    assert not asyncio.run(p.fetch_offers([m]))[0] and not calls
    assert asyncio.run(p.watch_offers([m]))[0] and len(calls) == 1


def test_native_pari_discovery_binds_ids_without_fabricating_second_quote(tmp_path, monkeypatch):
    from app.config import Settings
    from app.services.daily_quality_runner import DailyQualityRunner
    from tests.test_native_data_expansion import client_patch, pari_row

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("DAILY_PARI_ENABLED", "true")
    monkeypatch.setenv("PUBLICATION_PROFILE", "daily_quality")
    now = datetime.now(UTC)
    m = fixture(now)
    alias = replace(m, home_team="AS Arsenal", home_team_norm="as arsenal")
    row = pari_row(now)

    def response(url, params):
        return 200, {"data": [row], "TotalCount": 1} if url.endswith("/matches") else {"data": row}

    client_patch(monkeypatch, "app.providers.sstats_pari_daily", response)
    settings = Settings(_env_file=None)
    settings.sstats_api_key = "test-only"
    r = DailyQualityRunner(settings)
    r.registry.sync([m, alias])
    r.inventory_matches = r.registry.matches()
    # Exercise the native provider/registry boundary independently of legacy
    # collection-time wrappers installed on the base runner by unrelated tests.
    async def dispatch(self, provider, method, *args, empty_data):
        return await getattr(provider, method)(*args)

    monkeypatch.setattr(DailyQualityRunner.__mro__[1], "_fetch_provider", dispatch)
    result, _, _ = asyncio.run(r._fetch_provider(r.sstats_pari, "fetch_offers", r.inventory_matches, empty_data={}))
    assert len(result) == 1
    assert all(e["provider_ids"]["sstats_pari"] == "42" for e in r.registry.data["matches"].values())
    assert not r.registry.cached("sstats_pari", "offers", alias)
    r.registry.sync([m, alias])
    assert len(r.registry.matches()) == 1
