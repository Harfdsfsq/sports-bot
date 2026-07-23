from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from app.services import bzzoiro_empty_prediction_fallback as empty
from app.services import bzzoiro_v2_outage_fallback as outage


class DummyContext:
    def __init__(self) -> None:
        self.payload = {
            "prediction": {"markets": {"expected_goals": {"home": 1.4, "away": 0.9}}}
        }
        self.details: dict[str, Any] = {}


class DummyMatch:
    def __init__(self, key: str) -> None:
        self.match_key = key


def reset_state(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(empty, "_ATTEMPTED", False)
    empty._CACHE.clear()
    empty._LAST_FALLBACK_STATS.clear()
    monkeypatch.setattr(empty, "_write", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(outage, "_V2_CIRCUIT_OPEN", False)


def test_detects_only_healthy_empty_prediction_batch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_state(monkeypatch)
    healthy_empty = {
        "http_statuses": [200, 200],
        "events_fetched": 645,
        "predictions_fetched": 0,
        "contexts_built": 0,
        "response_errors": 0,
        "last_error": None,
    }
    assert empty._successful_empty_v2({}, healthy_empty) is True
    assert empty._successful_empty_v2({"m1": DummyContext()}, healthy_empty) is False
    assert empty._successful_empty_v2({}, {**healthy_empty, "events_fetched": 0}) is False
    assert empty._successful_empty_v2({}, {**healthy_empty, "http_statuses": [502]}) is False


@pytest.mark.asyncio
async def test_empty_v2_uses_v1_once_and_reuses_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reset_state(monkeypatch)
    primary_calls = 0
    fallback_calls = 0
    recorded: list[tuple[str, str, dict[str, Any]]] = []

    async def primary(_self: Any, _matches: list[Any]):
        nonlocal primary_calls
        primary_calls += 1
        return (
            {},
            {
                "http_statuses": [200, 200],
                "events_fetched": 645,
                "predictions_fetched": 0,
                "contexts_built": 0,
                "response_errors": 0,
                "last_error": None,
            },
            {},
        )

    async def v1(_settings: Any, _matches: list[Any]):
        nonlocal fallback_calls
        fallback_calls += 1
        return {"m1": DummyContext()}, {"hard_contexts_kept": 1}, {}

    monkeypatch.setattr(outage, "_v1_context_fallback", v1)
    monkeypatch.setattr(
        empty.coverage_ledger,
        "record_provider_result",
        lambda provider, method, data, _stats: recorded.append((provider, method, data)),
    )
    wrapped = empty._context_factory(primary)
    owner = SimpleNamespace(settings=object())
    matches = [DummyMatch("m1")]

    first, first_stats, _ = await wrapped(owner, matches)
    second, second_stats, _ = await wrapped(owner, matches)

    assert primary_calls == 2
    assert fallback_calls == 1
    assert set(first) == {"m1"}
    assert set(second) == {"m1"}
    assert first_stats["contexts_built_from_v1_empty_prediction_fallback"] == 1
    assert second_stats["contexts_built_from_v1_empty_prediction_fallback"] == 1
    assert second_stats["v1_empty_prediction_fallback"]["cache_hit_contexts"] == 1
    assert first["m1"].details["bzzoiro_api_fallback"] == "v1_after_v2_empty_predictions"
    assert outage._V2_CIRCUIT_OPEN is False
    assert recorded and recorded[0][0:2] == ("bzzoiro", "fetch_context")


@pytest.mark.asyncio
async def test_nonempty_v2_does_not_call_v1(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_state(monkeypatch)

    async def primary(_self: Any, _matches: list[Any]):
        return {"m1": DummyContext()}, {"http_statuses": [200], "events_fetched": 1}, {}

    async def v1(_settings: Any, _matches: list[Any]):
        raise AssertionError("v1 fallback must not run when v2 returned context")

    monkeypatch.setattr(outage, "_v1_context_fallback", v1)
    wrapped = empty._context_factory(primary)
    contexts, _stats, _preview = await wrapped(
        SimpleNamespace(settings=object()), [DummyMatch("m1")]
    )

    assert set(contexts) == {"m1"}
    assert empty._ATTEMPTED is False
