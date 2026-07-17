from __future__ import annotations

import asyncio
from types import SimpleNamespace

from app.services import bzzoiro_disabled_endpoint_guard as mod


def test_disabled_endpoint_guard_skips_metadata_and_preserves_odds(monkeypatch, tmp_path) -> None:
    calls: list[str] = []

    class Provider:
        async def _get_json(self, client, path, headers, params, stats):
            calls.append(path)
            return {"ok": True}

    fake_module = SimpleNamespace(BzzoiroContextProvider=Provider)
    monkeypatch.setattr("app.providers.bzzoiro_v2", fake_module, raising=False)
    monkeypatch.setattr(mod, "OUT", tmp_path / "guard.json")
    monkeypatch.setattr(mod, "_INSTALLED", False)
    mod.reset_for_tests()

    result = mod.install()
    provider = Provider()
    stats: dict = {}
    metadata = asyncio.run(provider._get_json(None, "/events/1/metadata/", {}, {}, stats))
    odds = asyncio.run(provider._get_json(None, "/events/1/odds/", {}, {}, stats))

    assert result["status"] == "installed"
    assert metadata is None
    assert odds == {"ok": True}
    assert calls == ["/events/1/odds/"]
    assert stats["disabled_endpoint_skips"]["metadata"] == 1
