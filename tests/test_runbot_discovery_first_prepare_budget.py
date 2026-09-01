from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import runbot_discovery_first_prepare as mod

UTC = timezone.utc


def test_previous_full_prepare_is_reused_for_same_day(monkeypatch, tmp_path: Path) -> None:
    now = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
    latest = tmp_path / "latest.json"
    mod.write(
        latest,
        {
            "created_at_utc": (now - timedelta(minutes=30)).isoformat(),
            "target_date": "2026-07-17",
            "mode": "runbot_discovery_first_prepare_v5_full_bounded",
            "status": "ok",
        },
    )
    monkeypatch.setattr(mod, "LATEST_JSON_OUT", latest)
    monkeypatch.setattr(mod, "inventory_matches", lambda: 300)
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-07-17")
    monkeypatch.setenv("RUNBOT_DISCOVERY_FIRST_FULL_REFRESH_INTERVAL_MINUTES", "360")

    result = mod.previous_full_prepare(now)

    assert result["reusable"] is True
    assert result["age_minutes"] == 30.0


def test_previous_full_prepare_expires_and_incremental_is_not_reused(monkeypatch, tmp_path: Path) -> None:
    now = datetime(2026, 7, 17, 10, 0, tzinfo=UTC)
    latest = tmp_path / "latest.json"
    mod.write(
        latest,
        {
            "created_at_utc": (now - timedelta(hours=7)).isoformat(),
            "target_date": "2026-07-17",
            "mode": "runbot_discovery_first_prepare_v5_full_bounded",
            "status": "ok",
        },
    )
    monkeypatch.setattr(mod, "LATEST_JSON_OUT", latest)
    monkeypatch.setattr(mod, "inventory_matches", lambda: 300)
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-07-17")
    monkeypatch.setenv("RUNBOT_DISCOVERY_FIRST_FULL_REFRESH_INTERVAL_MINUTES", "360")

    assert mod.previous_full_prepare(now)["reusable"] is False

    mod.write(
        latest,
        {
            "created_at_utc": (now - timedelta(minutes=10)).isoformat(),
            "target_date": "2026-07-17",
            "mode": "runbot_discovery_first_prepare_v5_incremental_reuse",
            "status": "ok",
        },
    )
    assert mod.previous_full_prepare(now)["reusable"] is False


def test_maybe_expand_skips_expensive_repeat_when_inventory_is_full(monkeypatch) -> None:
    called = False

    def expand() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(mod, "inventory_matches", lambda: 617)
    monkeypatch.setenv("DAY_INVENTORY_TARGET_SIZE", "300")

    result = mod._maybe_expand("expand", expand)

    assert result["status"] == "skipped"
    assert called is False
