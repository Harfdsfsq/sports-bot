from __future__ import annotations

import asyncio
from collections import defaultdict

from app.services import provider_wall_clock_final_guard as guard
from app.services import runner as runner_module


def test_final_runner_boundary_times_out_bzzoiro(monkeypatch, tmp_path) -> None:
    class FakeRunner:
        def __init__(self) -> None:
            self.provider_runtime_errors = defaultdict(list)
            self.provider_status = {}

        def _provider_name(self, provider):
            return provider.name

        def _mark_provider_status(self, provider_name, **payload):
            self.provider_status[provider_name] = payload

        async def _fetch_provider(self, provider, method_name, *args, empty_data):
            await asyncio.sleep(1.0)
            return {"unexpected": True}, {}, {}

    class FakeProvider:
        name = "bzzoiro"
        api_key = "present"

    monkeypatch.setattr(runner_module, "PredictionRunner", FakeRunner)
    monkeypatch.setattr(guard, "_deadline_seconds", lambda: 0.01)
    monkeypatch.setattr(guard, "OUT", tmp_path / "guard.json")
    monkeypatch.setattr(guard, "ART", tmp_path / "artifact.json")
    monkeypatch.setattr(guard, "_hard_budget_snapshot", lambda: {"requests_claimed": 7})

    result = guard.install()
    data, stats, preview = asyncio.run(
        FakeRunner()._fetch_provider(FakeProvider(), "fetch_context", [], empty_data={})
    )

    assert result["status"] == "installed"
    assert data == {}
    assert stats["budget_exhausted"] is True
    assert stats["hard_budget_stop_reason"] == "runner_provider_deadline_exhausted"
    assert stats["requests"] == 7
    assert preview["deadline_exhausted"] is True


def test_non_bzzoiro_provider_is_not_deadline_wrapped(monkeypatch, tmp_path) -> None:
    class FakeRunner:
        def _provider_name(self, provider):
            return provider.name

        async def _fetch_provider(self, provider, method_name, *args, empty_data):
            return {"ok": True}, {"enabled": True}, {"provider": provider.name}

    class FakeProvider:
        name = "sstats"

    monkeypatch.setattr(runner_module, "PredictionRunner", FakeRunner)
    monkeypatch.setattr(guard, "OUT", tmp_path / "guard.json")
    monkeypatch.setattr(guard, "ART", tmp_path / "artifact.json")

    guard.install()
    data, stats, preview = asyncio.run(
        FakeRunner()._fetch_provider(FakeProvider(), "fetch_context", [], empty_data={})
    )

    assert data == {"ok": True}
    assert stats == {"enabled": True}
    assert preview == {"provider": "sstats"}
