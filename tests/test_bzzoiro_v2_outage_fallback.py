from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services import bzzoiro_v2_outage_fallback as fallback


class DummyContext:
    def __init__(self, payload: Any) -> None:
        self.payload = payload
        self.details: dict[str, Any] = {}


class DummyMatch:
    def __init__(self, key: str) -> None:
        self.match_key = key


def reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fallback, "_V2_CIRCUIT_OPEN", False)
    monkeypatch.setattr(fallback, "_V2_CIRCUIT_REASON", "")
    monkeypatch.setattr(fallback, "_V2_CIRCUIT_OPENED_AT", 0.0)
    monkeypatch.setattr(fallback, "_V1_ODDS_FAILED", False)
    monkeypatch.setattr(fallback, "_V1_CONTEXT_FAILED", False)
    fallback._OFFER_CACHE.clear()
    fallback._OFFER_CACHE_STATS.clear()
    monkeypatch.setattr(fallback, "_write", lambda *_args, **_kwargs: None)


def test_server_failure_requires_server_side_failure() -> None:
    assert fallback._server_failure({"http_statuses": [502, 503]}) is True
    assert fallback._server_failure({"last_error": "http_status=502"}) is True
    assert fallback._server_failure({"http_statuses": [200, 502]}) is False
    assert fallback._server_failure({"http_statuses": [401]}) is False


def test_hard_context_rejects_odds_only_payload() -> None:
    prediction = DummyContext(
        {"prediction": {"markets": {"expected_goals": {"home": 1.4, "away": 0.9}}}}
    )
    stats = DummyContext({"stats": {"home_xg": 1.2, "away_xg": 0.8}})
    odds_only = DummyContext({"odds": {"home_win": 1.8, "over_25_goals": 2.0}})

    assert fallback._hard_context(prediction) is True
    assert fallback._hard_context(stats) is True
    assert fallback._hard_context(odds_only) is False


@pytest.mark.asyncio
async def test_context_uses_v1_only_after_v2_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_state(monkeypatch)
    recorded: list[tuple[str, str, dict[str, Any]]] = []

    async def primary(_self: Any, _matches: list[Any]):
        return {}, {"http_statuses": [502], "last_error": "http_status=502"}, {}

    async def v1(_settings: Any, _matches: list[Any]):
        return {"m1": DummyContext({"prediction": {"markets": {"expected_goals": {}}}})}, {
            "hard_contexts_kept": 1
        }, {}

    monkeypatch.setattr(fallback, "_v1_context_fallback", v1)
    monkeypatch.setattr(
        fallback.coverage_ledger,
        "record_provider_result",
        lambda provider, method, data, _stats: recorded.append((provider, method, data)),
    )

    wrapped = fallback._context_factory(primary)
    contexts, stats, _preview = await wrapped(SimpleNamespace(settings=object()), [DummyMatch("m1")])

    assert set(contexts) == {"m1"}
    assert stats["contexts_built_from_v1_fallback"] == 1
    assert fallback._V2_CIRCUIT_OPEN is True
    assert recorded and recorded[0][0:2] == ("bzzoiro", "fetch_context")


@pytest.mark.asyncio
async def test_offer_cache_prevents_duplicate_network_call(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_state(monkeypatch)
    calls = 0

    async def primary(_settings: Any, _matches: list[Any], _base: dict[str, list[Any]], _amap: dict[str, dict[str, str]]):
        nonlocal calls
        calls += 1
        return {"m1": [object()]}, {"http_statuses": [200]}

    class MergeModule:
        @staticmethod
        def merge(base: dict[str, list[Any]], extra: dict[str, list[Any]]) -> int:
            added = 0
            for key, rows in extra.items():
                bucket = base.setdefault(key, [])
                bucket.extend(rows)
                added += len(rows)
            return added

    monkeypatch.setattr(fallback.coverage_ledger, "record_provider_result", lambda *_args, **_kwargs: None)
    wrapped = fallback._merge_fetch_factory(primary, MergeModule)
    match = DummyMatch("m1")

    first, _ = await wrapped(object(), [match], {}, {})
    second, second_stats = await wrapped(object(), [match], {}, {})

    assert calls == 1
    assert first["m1"]
    assert second["m1"]
    assert second_stats["network_requests"] == 0


@pytest.mark.asyncio
async def test_offer_v1_fallback_after_v2_5xx(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_state(monkeypatch)

    async def primary(_settings: Any, _matches: list[Any], _base: dict[str, list[Any]], _amap: dict[str, dict[str, str]]):
        return {}, {"http_statuses": [502], "last_error": "http_status=502"}

    async def v1(_merge: Any, _settings: Any, _matches: list[Any], _base: dict[str, list[Any]]):
        return {"m1": [object()]}, {"http_statuses": [200], "offers": 1}

    class MergeModule:
        @staticmethod
        def merge(base: dict[str, list[Any]], extra: dict[str, list[Any]]) -> int:
            for key, rows in extra.items():
                base.setdefault(key, []).extend(rows)
            return sum(len(rows) for rows in extra.values())

    monkeypatch.setattr(fallback, "_v1_best_odds", v1)
    monkeypatch.setattr(fallback.coverage_ledger, "record_provider_result", lambda *_args, **_kwargs: None)
    wrapped = fallback._merge_fetch_factory(primary, MergeModule)

    offers, stats = await wrapped(object(), [DummyMatch("m1")], {}, {})

    assert offers["m1"]
    assert fallback._V2_CIRCUIT_OPEN is True
    assert stats["v1_server_failure_fallback"]["offers"] == 1
