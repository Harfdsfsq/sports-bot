from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services import focused_alpha_filter_contract_patch as filter_contract
from scripts import send_harizon_telegram_run_report_v14 as report_v14


def test_filter_contract_preserves_tuple_metadata(monkeypatch) -> None:
    from app.services import daily_coverage_full_inventory_provider_patch as scope

    class Runner:
        settings = SimpleNamespace(tzinfo=UTC)

        def _filter_matches(self, matches, now_utc):
            return list(matches), {"native": True}

    original = Runner._filter_matches
    monkeypatch.setattr(scope, "_ORIGINAL_FILTER", original)
    monkeypatch.setattr(scope, "_coverage_horizon_matches", lambda runner, rows, now: rows)
    monkeypatch.setattr(
        scope,
        "_focused_model_scope",
        lambda runner, rows: (rows[:1], True),
    )
    monkeypatch.setattr(filter_contract, "_INSTALLED", False)

    result = filter_contract.install(Runner)
    rows, metadata = Runner()._filter_matches(["a", "b"], datetime.now(UTC))

    assert result["status"] == "installed"
    assert rows == ["a"]
    assert metadata["native"] is True
    assert metadata["focused_alpha_model_targets"] == 1
    assert metadata["focused_alpha_original_window_targets"] == 2


def test_filter_contract_preserves_legacy_list_shape() -> None:
    rows, metadata, returned_pair = filter_contract.split_filter_result(["a", "b"])

    assert rows == ["a", "b"]
    assert metadata is None
    assert returned_pair is False
    assert filter_contract.restore_filter_result(
        rows,
        metadata,
        returned_pair,
        focused_declared=True,
        original_rows=2,
    ) == ["a", "b"]


def test_report_counts_odds_event_bootstrap_when_offer_stage_is_skipped() -> None:
    payload = {
        "api": {
            "odds_api_io": {
                "events_req": 0,
                "odds_req": 0,
                "errors": 0,
            }
        }
    }
    summary = {
        "source_stats": {
            "odds_api_io": {
                "planned_skip": True,
                "response_errors": 0,
            },
            "odds_api_io_bootstrap": {
                "event_requests": 10,
                "events_fetched": 1000,
                "matches_built": 651,
                "response_errors": 0,
            },
        }
    }

    report_v14._repair_odds_event_bootstrap_api(payload, summary)

    odds = payload["api"]["odds_api_io"]
    assert odds["events_req"] == 10
    assert odds["bootstrap_events_fetched"] == 1000
    assert odds["bootstrap_matches_built"] == 651
    assert odds["odds_req"] == 0


def _write(path, payload) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _payload(old_main: int = 88) -> dict:
    return {
        "status": "published",
        "status_ru": "✅ прогноз опубликован",
        "top_reason": "main_pipeline_published",
        "coverage": {"matches_with_offers": 0},
        "funnel": {
            "raw_candidates": 0,
            "publishable_candidates": 0,
            "main_pipeline_published_count": old_main,
            "main_pipeline_published": old_main > 0,
            "fallback_published_count": 0,
            "fallback_published": False,
            "fallback_status": "no_viable_controlled_fallback",
            "published_count": old_main,
            "final_publication_status": "published",
        },
        "diagnostics": {},
    }


def test_report_counts_only_current_run_sent_rows(tmp_path, monkeypatch) -> None:
    now = datetime(2026, 7, 24, 10, tzinfo=UTC)
    monkeypatch.setattr(report_v14, "EXPORT", tmp_path)
    monkeypatch.setattr(report_v14, "DEBUG", tmp_path / "debug.json")
    _write(
        tmp_path / "latest-runbot-discovery-first-prepare.json",
        {"created_at_utc": (now - timedelta(minutes=12)).isoformat()},
    )
    _write(
        tmp_path / "latest-picks.json",
        [
            {
                "telegram_sent": True,
                "sent_at": (now - timedelta(days=20)).isoformat(),
            },
            {
                "telegram_sent": True,
                "sent_at": (now - timedelta(minutes=4)).isoformat(),
            },
        ],
    )
    _write(
        tmp_path / "latest-pending-bets.json",
        [{"telegram_sent": True, "sent_at": (now - timedelta(days=1)).isoformat()}],
    )
    _write(tmp_path / "debug.json", {})

    repaired = report_v14.repair_payload(_payload(), now=now)
    funnel = repaired["funnel"]
    diagnostics = funnel["main_pipeline_publication_counter_diagnostics"]

    assert repaired["status"] == "published"
    assert funnel["main_pipeline_published_count"] == 1
    assert funnel["published_count"] == 1
    assert diagnostics["cumulative_sent_picks_count"] == 2
    assert diagnostics["ignored_cumulative_sent_picks_count"] == 1
    assert diagnostics["ignored_ledger_sent_pending_count"] == 1


def test_report_surfaces_runner_error_instead_of_historical_publication(
    tmp_path,
    monkeypatch,
) -> None:
    now = datetime(2026, 7, 24, 10, tzinfo=UTC)
    monkeypatch.setattr(report_v14, "EXPORT", tmp_path)
    monkeypatch.setattr(report_v14, "DEBUG", tmp_path / "debug.json")
    _write(
        tmp_path / "latest-runbot-discovery-first-prepare.json",
        {"created_at_utc": (now - timedelta(minutes=10)).isoformat()},
    )
    _write(
        tmp_path / "latest-picks.json",
        [{"telegram_sent": True, "sent_at": (now - timedelta(days=40)).isoformat()}],
    )
    _write(tmp_path / "latest-pending-bets.json", [])
    _write(
        tmp_path / "debug.json",
        {"error": "ValueError: not enough values to unpack (expected 2, got 0)"},
    )

    repaired = report_v14.repair_payload(_payload(), now=now)

    assert repaired["status"] == "run_failed"
    assert repaired["top_reason"] == "runner_error"
    assert repaired["funnel"]["main_pipeline_published_count"] == 0
    assert repaired["funnel"]["published_count"] == 0
    assert "not enough values to unpack" in repaired["diagnostics"]["runner_error"]
