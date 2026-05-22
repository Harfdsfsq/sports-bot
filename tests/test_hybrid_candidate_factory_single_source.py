
from __future__ import annotations

import importlib


def test_core_line_patch_allows_single_source_when_hybrid_enabled(monkeypatch):
    monkeypatch.setenv("CONTROLLED_FALLBACK_SINGLE_LINE_CONTEXT_MODE_ENABLED", "true")
    monkeypatch.delenv("CORE_LINE_BOOKMAKER_UNIVERSE_ALLOW_SINGLE_SOURCE", raising=False)
    mod = importlib.import_module("app.services.core_line_bookmaker_universe_patch")
    assert mod._truthy(__import__("os").getenv(mod.SINGLE_LINE_ENV), mod._truthy(__import__("os").getenv(mod.HYBRID_ENV), False)) is True


def test_sstats_is_not_core_line_source():
    mod = importlib.import_module("app.services.core_line_bookmaker_universe_patch")
    assert "sstats" not in mod.CORE_LINE_SOURCES
    assert "bzzoiro" in mod.CORE_LINE_SOURCES
