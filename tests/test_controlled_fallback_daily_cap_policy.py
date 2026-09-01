from __future__ import annotations

from scripts import publish_controlled_fallback_guarded_v19 as policy


def _clear(monkeypatch) -> None:
    for name in (
        "CONTROLLED_FALLBACK_DISABLE_DAILY_CAP_FLOOR",
        "CONTROLLED_FALLBACK_DAILY_MAX_FLOOR",
        "HARIZON_TARGET_DAILY_MAX_PICKS",
        "CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED",
        "CONTROLLED_FALLBACK_DAILY_MAX_B_TIER",
        "CONTROLLED_FALLBACK_RESERVED_DAILY_SLOTS",
    ):
        monkeypatch.delenv(name, raising=False)


def test_explicit_three_pick_caps_are_not_raised_to_internal_target(monkeypatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("HARIZON_TARGET_DAILY_MAX_PICKS", "5")
    monkeypatch.setenv("CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED", "3")
    monkeypatch.setenv("CONTROLLED_FALLBACK_DAILY_MAX_B_TIER", "3")

    result = policy._apply_daily_slot_target_policy()

    assert result["effective_published_limit"] == 3
    assert result["effective_b_tier_limit"] == 3
    assert result["explicit_published_preserved"] is True
    assert result["explicit_b_tier_preserved"] is True
    assert result["after"]["CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED"] == "3"
    assert result["after"]["CONTROLLED_FALLBACK_DAILY_MAX_B_TIER"] == "3"


def test_internal_target_is_only_a_default_when_caps_are_absent(monkeypatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("HARIZON_TARGET_DAILY_MAX_PICKS", "5")

    result = policy._apply_daily_slot_target_policy()

    assert result["effective_published_limit"] == 5
    assert result["effective_b_tier_limit"] == 5
    assert result["explicit_published_preserved"] is False
    assert result["explicit_b_tier_preserved"] is False
    assert result["after"]["CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED"] == "5"
    assert result["after"]["CONTROLLED_FALLBACK_DAILY_MAX_B_TIER"] == "5"


def test_missing_b_tier_cap_inherits_explicit_total_cap(monkeypatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("HARIZON_TARGET_DAILY_MAX_PICKS", "5")
    monkeypatch.setenv("CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED", "4")

    result = policy._apply_daily_slot_target_policy()

    assert result["effective_published_limit"] == 4
    assert result["effective_b_tier_limit"] == 4
    assert result["after"]["CONTROLLED_FALLBACK_DAILY_MAX_B_TIER"] == "4"


def test_daily_cap_policy_can_be_disabled_without_mutating_caps(monkeypatch) -> None:
    _clear(monkeypatch)
    monkeypatch.setenv("CONTROLLED_FALLBACK_DISABLE_DAILY_CAP_FLOOR", "true")
    monkeypatch.setenv("CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED", "3")

    result = policy._apply_daily_slot_target_policy()

    assert result == {"status": "disabled"}
    assert policy.os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_PUBLISHED") == "3"
    assert policy.os.getenv("CONTROLLED_FALLBACK_DAILY_MAX_B_TIER") is None
