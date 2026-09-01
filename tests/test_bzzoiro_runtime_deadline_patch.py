from __future__ import annotations

import asyncio

from app.providers import bzzoiro_v2
from app.services import bzzoiro_runtime_deadline_patch


def test_provider_coroutine_deadline_returns_control(monkeypatch) -> None:
    class FakeProvider:
        async def fetch_context(self, matches):
            await asyncio.sleep(1.0)
            return {"unexpected": True}, {}, {}

    monkeypatch.setattr(bzzoiro_v2, "BzzoiroContextProvider", FakeProvider)
    monkeypatch.setattr(bzzoiro_runtime_deadline_patch, "_deadline_seconds", lambda: 0.01)

    result = bzzoiro_runtime_deadline_patch.install()
    contexts, stats, preview = asyncio.run(FakeProvider().fetch_context([]))

    assert result["status"] == "installed"
    assert contexts == {}
    assert stats["budget_exhausted"] is True
    assert stats["hard_budget_stop_reason"] == "provider_coroutine_deadline_exhausted"
    assert preview["deadline_exhausted"] is True


def test_runtime_policy_uses_low_yield_safe_caps() -> None:
    policy = bzzoiro_runtime_deadline_patch.POLICY

    assert policy["BZZOIRO_RUNTIME_HARD_REQUEST_CAP"] == "48"
    assert policy["BZZOIRO_RUNTIME_HARD_SECONDS"] == "75"
    assert policy["BZZOIRO_RUNTIME_DETAIL_MATCH_LIMIT"] == "24"
    assert policy["BZZOIRO_RUNTIME_COMPARISON_MATCH_LIMIT"] == "12"
    assert policy["BZZOIRO_RUNTIME_HTTP_TIMEOUT_SECONDS"] == "5"
