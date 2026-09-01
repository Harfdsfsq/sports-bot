from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from scripts import send_harizon_telegram_run_report_v13 as report


def test_repairs_false_sportlogic_disabled_line(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(report, "EXPORT", tmp_path)
    (tmp_path / "latest-sportlogic-coverage-probe.json").write_text(
        json.dumps(
            {
                "created_at_utc": datetime.now(UTC).isoformat(),
                "requests": 3,
                "current_games": 0,
                "matched_games": 0,
                "diagnosis": "active_odds_stale_only_no_current_fixture",
                "http_statuses": [200, 200, 200],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "latest-sportlogic-debug.json").write_text(
        json.dumps(
            {
                "stats": {
                    "enabled": 2,
                    "requests": 3,
                    "fixtures_fetched": 50,
                    "events_matched": 0,
                    "odds_requests": 0,
                    "offers_parsed": 0,
                    "response_errors": 0,
                    "diagnosis": "active_odds_stale_only_no_current_fixture",
                    "http_statuses": [200, 200, 200],
                }
            }
        ),
        encoding="utf-8",
    )

    text = (
        "📡 Провайдеры\n"
        "• SportLogic: disabled_by_env; запросы 0; fixtures 0; matched 0; "
        "odds req 0; offers 0; ошибок 0; diag disabled_by_env.\n"
    )
    repaired = report._repair_sportlogic_runtime_line(text)

    assert "SportLogic: enabled_runtime" in repaired
    assert "запросы 3" in repaired
    assert "rows 50" in repaired
    assert "current fixtures 0" in repaired
    assert "active_odds_stale_only_no_current_fixture" in repaired


def test_does_not_use_stale_sportlogic_artifact(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(report, "EXPORT", tmp_path)
    (tmp_path / "latest-sportlogic-coverage-probe.json").write_text(
        json.dumps(
            {
                "created_at_utc": "2026-01-01T00:00:00+00:00",
                "requests": 3,
                "http_statuses": [200],
            }
        ),
        encoding="utf-8",
    )
    text = "• SportLogic: disabled_by_env; запросы 0.\n"

    assert report._repair_sportlogic_runtime_line(text) == text


def test_uses_current_report_payload_before_stale_probe(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(report, "EXPORT", tmp_path)
    (tmp_path / "latest-sportlogic-coverage-probe.json").write_text(
        json.dumps(
            {
                "created_at_utc": "2026-01-01T00:00:00+00:00",
                "requests": 3,
                "http_statuses": [200],
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "status": "candidates_but_quality_rejected",
        "api": {
            "sportlogic": {
                "enabled": True,
                "requests": 5,
                "fixtures": 100,
                "matched": 0,
                "odds_requests": 0,
                "offers": 0,
                "errors": 0,
                "diagnosis": "active_odds_stale_only_no_current_fixture",
            }
        },
    }
    text = "• SportLogic: disabled_by_env; запросы 0. \n"

    repaired = report._repair_sportlogic_runtime_line(text, payload)

    assert "SportLogic: enabled_runtime" in repaired
    assert "запросы 5" in repaired
    assert "rows 100" in repaired
    assert "current fixtures 100" in repaired
    assert "active_odds_stale_only_no_current_fixture" in repaired


def test_current_report_uses_fresh_probe_after_nested_stale_sportlogic_sample(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setattr(report, "EXPORT", tmp_path)
    (tmp_path / "latest-sportlogic-coverage-probe.json").write_text(
        json.dumps(
            {
                "created_at_utc": datetime.now(UTC).isoformat(),
                "requests": 3,
                "active_odds_rows_seen": 50,
                "current_games": 0,
                "matched_games": 0,
                "diagnosis": "active_odds_stale_only_no_current_fixture",
                "http_statuses": [200, 200, 200],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "latest-sportlogic-debug.json").write_text(
        json.dumps(
            {
                "stats": {
                    "enabled": 2,
                    "requests": 3,
                    "active_odds_rows_seen": 50,
                    "fixtures_fetched": 50,
                    "matches_built": 0,
                    "events_matched": 0,
                    "response_errors": 0,
                    "diagnosis": "active_odds_stale_only_no_current_fixture",
                    "http_statuses": [200, 200, 200],
                }
            }
        ),
        encoding="utf-8",
    )
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "lines_but_no_raw_candidates",
        "api": {
            "sportlogic": {
                "enabled": True,
                "requests": 5,
                "fixtures": 100,
                "matched": 0,
                "stale_sample": True,
                "sample_dates": ["2026-05-02"],
                "diagnosis": "active_odds_stale_only_no_current_fixture",
            }
        },
    }
    text = "• SportLogic: disabled_by_env; запросы 0.\n"

    repaired = report._repair_sportlogic_runtime_line(text, payload)

    assert "SportLogic: enabled_runtime" in repaired
    assert "запросы 3" in repaired
    assert "rows 50" in repaired
    assert "current fixtures 0" in repaired
    assert "active_odds_stale_only_no_current_fixture" in repaired
