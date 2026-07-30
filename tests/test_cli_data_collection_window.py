from __future__ import annotations

from types import SimpleNamespace

from app import cli


def test_data_collection_window_does_not_widen_or_disable_publication(
    monkeypatch,
) -> None:
    for name in (
        "DATA_COLLECTION_WINDOW_HOURS",
        "RUNTIME_DATA_COLLECTION_WINDOW_HOURS",
        "HARIZON_COVERAGE_UPLIFT_NEAR_WINDOW_HOURS",
        "DAY_INVENTORY_NEAR_WINDOW_HOURS",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("HARIZON_DATA_COLLECTION_WINDOW_HOURS", "36")
    monkeypatch.setenv("HARIZON_DISABLE_MAIN_PUBLICATION_FOR_DATA_WINDOW", "false")
    monkeypatch.delenv("PUBLISH_WINDOW_HOURS", raising=False)
    monkeypatch.delenv("PREDICTION_PUBLICATION_ENABLED", raising=False)

    settings = SimpleNamespace(
        publish_window_hours=2,
        prediction_publication_enabled=True,
    )

    result = cli._apply_runtime_env_overrides(settings)

    assert result.publish_window_hours == 2
    assert result.prediction_publication_enabled is True
    assert cli.os.environ["HARIZON_EFFECTIVE_DATA_COLLECTION_WINDOW_HOURS"] == "36"
    assert cli.os.environ["HARIZON_MAIN_PUBLICATION_DISABLED_FOR_DATA_WINDOW"] == "false"


def test_explicit_collection_only_mode_can_disable_publication(monkeypatch) -> None:
    monkeypatch.setenv("HARIZON_DATA_COLLECTION_WINDOW_HOURS", "36")
    monkeypatch.setenv("HARIZON_DISABLE_MAIN_PUBLICATION_FOR_DATA_WINDOW", "true")
    monkeypatch.delenv("PUBLISH_WINDOW_HOURS", raising=False)
    monkeypatch.delenv("PREDICTION_PUBLICATION_ENABLED", raising=False)
    settings = SimpleNamespace(
        publish_window_hours=2,
        prediction_publication_enabled=True,
    )

    result = cli._apply_runtime_env_overrides(settings)

    assert result.publish_window_hours == 2
    assert result.prediction_publication_enabled is False
    assert cli.os.environ["HARIZON_MAIN_PUBLICATION_DISABLED_FOR_DATA_WINDOW"] == "true"
