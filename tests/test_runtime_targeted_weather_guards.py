from __future__ import annotations


def test_runtime_chain_installs_targeted_patch():
    from app.services import runtime_startup_chain

    assert "app.services.targeted_enrichment_runtime_patch" in runtime_startup_chain.MODULES
    assert runtime_startup_chain.MODULES.index("app.services.weather_location_guard_runtime_patch") < runtime_startup_chain.MODULES.index("app.services.api_matching_quality_runtime_guard")


def test_weather_guard_rejects_team_name_fallback(monkeypatch):
    from types import SimpleNamespace
    from app.services import weather_location_guard_runtime_patch
    from app.providers.weather_common import WeatherContextEnricher

    weather_location_guard_runtime_patch.install()
    enricher = WeatherContextEnricher(SimpleNamespace())
    match = SimpleNamespace(home_team="Ferroviaria SP", away_team="UDA AL")

    assert enricher._location_from_fixture(match, {"league": {}}) is None
    assert enricher._location_from_fixture(match, {"fixture": {"venue": {"city": "Sao Paulo"}}, "league": {"country": "Brazil"}})["query"] == "Sao Paulo, Brazil"
