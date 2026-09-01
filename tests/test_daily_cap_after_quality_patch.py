from __future__ import annotations

from types import SimpleNamespace

from scripts.patch_daily_cap_after_quality import install


def _fixture():
    calls: list[str] = []
    v18 = SimpleNamespace()

    def daily(_candidate, _metrics):
        calls.append("daily")
        return ["controlled_fallback_daily_limit_reached:5/3"]

    def hard(_candidate, _metrics, _sent_index):
        # Simulate the v18 hard wrapper resolving the daily function dynamically.
        return ["base_hard"] + list(v18._daily_limit_reasons({}, {}))

    def final(_candidate, _metrics, _tier):
        calls.append("final")
        return []

    v18._daily_limit_reasons = daily
    v18.base = SimpleNamespace(
        hard_reject_reasons=hard,
        final_publish_guard_reasons=final,
    )
    return v18, calls


def test_daily_cap_is_removed_from_hard_reject_stage() -> None:
    v18, calls = _fixture()
    result = install(v18)

    reasons = v18.base.hard_reject_reasons({}, {}, {})

    assert result["status"] == "installed"
    assert reasons == ["base_hard"]
    assert calls == []


def test_daily_cap_is_added_by_final_publication_guard() -> None:
    v18, calls = _fixture()
    install(v18)

    reasons = v18.base.final_publish_guard_reasons({}, {}, "уровень B")

    assert reasons == ["controlled_fallback_daily_limit_reached:5/3"]
    assert calls == ["final", "daily"]


def test_existing_final_reasons_are_preserved_with_cap_reason() -> None:
    v18, calls = _fixture()
    v18.base.final_publish_guard_reasons = lambda _candidate, _metrics, _tier: ["proxy_single_source_edge_below_min"]
    install(v18)

    reasons = v18.base.final_publish_guard_reasons({}, {}, "уровень B")

    assert reasons == [
        "proxy_single_source_edge_below_min",
        "controlled_fallback_daily_limit_reached:5/3",
    ]
    assert calls == ["daily"]


def test_install_is_idempotent() -> None:
    v18, _calls = _fixture()

    assert install(v18)["status"] == "installed"
    assert install(v18)["status"] == "already_installed"
