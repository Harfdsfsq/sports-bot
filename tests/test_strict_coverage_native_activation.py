from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.schemas import MatchContext, Offer
from app.services import strict_coverage_native_activation as activation


def test_bzzoiro_market_codes_map_to_decimal_total_points() -> None:
    original = lambda *values: 99.0  # noqa: E731
    fixed = activation._fixed_line_from_factory(original)
    assert fixed("over_under_15", "over") == 1.5
    assert fixed("over_under_25", "under") == 2.5
    assert fixed("over_under_35", "over") == 3.5
    assert fixed("other", "2.5") == 99.0


def test_synthetic_bzzoiro_books_do_not_count_as_real_bookmakers() -> None:
    assert activation._synthetic_book("oddssafari-consensus") is True
    assert activation._synthetic_book("Bzzoiro Consensus") is True
    assert activation._synthetic_book("Pinnacle") is False


def test_persistent_context_factory_records_real_provider_evidence(monkeypatch) -> None:
    context = MatchContext(source="bzzoiro", payload={}, expected_home=1.4, expected_away=1.0)
    calls: list[tuple[str, str, dict[str, MatchContext]]] = []

    async def batch_fetch(_self, _matches):
        return {"match": context}, {"requests": 2}, {"sample": True}

    def record(provider, method, data, stats):
        assert stats["requests"] == 2
        calls.append((provider, method, data))

    monkeypatch.setattr(activation.coverage_ledger, "record_provider_result", record)
    wrapped = activation._persistent_context_factory(batch_fetch)
    data, _, _ = asyncio.run(wrapped(SimpleNamespace(), []))
    assert data["match"].expected_home == 1.4
    assert calls == [("bzzoiro", "fetch_context", {"match": context})]


def test_persistent_odds_factory_stamps_freshness_and_records(monkeypatch) -> None:
    offer = Offer(
        source="bzzoiro",
        bookmaker="pinnacle",
        family="totals",
        selection="Over",
        price=1.91,
        point=2.5,
    )
    calls: list[tuple[str, str, dict[str, list[Offer]]]] = []

    async def fetch(_settings, _matches, _base, _amap):
        return {"match": [offer]}, {"requests": 1}

    def record(provider, method, data, _stats):
        calls.append((provider, method, data))

    monkeypatch.setattr(activation.coverage_ledger, "record_provider_result", record)
    wrapped = activation._persistent_bzzoiro_odds_factory(fetch)
    data, _ = asyncio.run(wrapped(None, [], {}, {}))
    assert data["match"][0].metadata["provider_source"] == "bzzoiro"
    assert data["match"][0].metadata["fetched_at_utc"]
    assert calls == [("bzzoiro", "fetch_offers", {"match": [offer]})]


def test_cached_bzzoiro_offers_are_injected_into_runtime_pool(monkeypatch) -> None:
    primary = Offer(
        source="odds_api_io",
        bookmaker="bet365",
        family="totals",
        selection="Over",
        price=1.90,
        point=2.5,
    )
    cached = Offer(
        source="bzzoiro",
        bookmaker="pinnacle",
        family="totals",
        selection="Over",
        price=1.92,
        point=2.5,
    )

    async def fetch(_self, _matches):
        return {"match": [primary]}, {}, {}

    def merge(pool, extra):
        pool.setdefault("match", []).extend(extra.get("match", []))
        return len(extra.get("match", []))

    monkeypatch.setattr(
        activation.coverage_ledger,
        "cached_provider_data",
        lambda *_args, **_kwargs: {"match": [cached]},
    )
    wrapped = activation._cached_bzzoiro_offer_factory(fetch, merge)
    data, stats, preview = asyncio.run(wrapped(SimpleNamespace(), []))
    assert {row.source for row in data["match"]} == {"odds_api_io", "bzzoiro"}
    assert stats["bzzoiro_cached_evidence_offers_added"] == 1
    assert preview["bzzoiro_cached_evidence"]["matches"] == 1
