from __future__ import annotations

import asyncio
from types import SimpleNamespace


def test_provider_startup_installs_flat_autonomous_persistence() -> None:
    import app.providers  # noqa: F401
    from app.providers import bzzoiro_v2
    from app.services import autonomous_accumulation_persistence as persistence
    from app.services import autonomous_accumulation_runtime as runtime

    assert runtime.COVERAGE == persistence.COVERAGE
    assert runtime.COVERAGE_LEDGER == persistence.COVERAGE_LEDGER
    assert runtime.PREDICTION_LEDGER == persistence.PREDICTION_LEDGER
    assert getattr(bzzoiro_v2.BzzoiroContextProvider, "_harizon_runtime_hard_budget_patched", False)


def test_bzzoiro_v2_denies_http_after_shared_cap(monkeypatch) -> None:
    monkeypatch.setenv("BZZOIRO_RUNTIME_HARD_REQUEST_CAP", "1")
    monkeypatch.setenv("BZZOIRO_RUNTIME_ABSOLUTE_MAX_REQUESTS", "1")
    monkeypatch.setenv("BZZOIRO_RUNTIME_HARD_SECONDS", "60")
    monkeypatch.setenv("BZZOIRO_RUNTIME_ABSOLUTE_MAX_SECONDS", "60")
    monkeypatch.setenv("BZZOIRO_API_KEY", "test-key")

    import app.providers  # noqa: F401
    from app.providers import bzzoiro_v2
    from app.services import bzzoiro_runtime_budget_patch as patch

    patch.reset_for_tests()

    class FakeResponse:
        status_code = 200
        text = '{"results": []}'

        @staticmethod
        def json():
            return {"results": []}

    class FakeClient:
        def __init__(self) -> None:
            self.calls = 0

        async def get(self, *args, **kwargs):
            self.calls += 1
            return FakeResponse()

    provider = bzzoiro_v2.BzzoiroContextProvider(
        SimpleNamespace(bzzoiro_api_key="test-key", bzzoiro_timeout_seconds=1.0)
    )
    client = FakeClient()
    stats = {
        "requests": 0,
        "response_errors": 0,
        "retry_attempts": 0,
        "http_statuses": [],
        "payload_shapes": [],
        "last_url": None,
        "last_error": None,
        "last_body_preview": None,
    }

    async def exercise() -> tuple[object, object]:
        first = await provider._get_json(client, "/events/", {}, {}, stats)
        second = await provider._get_json(client, "/events/1/stats/", {}, {}, stats)
        return first, second

    first, second = asyncio.run(exercise())

    assert first == {"results": []}
    assert second is None
    assert client.calls == 1
    assert stats["budget_exhausted"] is True
    snap = patch.snapshot()
    assert snap["requests_claimed"] == 1
    assert snap["requests_denied"] == 1
    assert snap["last_stop_reason"] == "request_budget_exhausted"
