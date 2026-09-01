from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from scripts import send_harizon_telegram_run_report_v14 as report


def _payload() -> dict[str, Any]:
    return {
        "status": "published",
        "status_ru": "published",
        "top_reason": "main_pipeline_published",
        "coverage": {"matches_with_offers": 3},
        "funnel": {
            "raw_candidates": 0,
            "publishable_candidates": 0,
            "main_pipeline_published_count": 29,
            "fallback_published_count": 0,
        },
        "diagnostics": {},
    }


def _install_load(monkeypatch, *, debug: dict, picks: list[dict]) -> None:
    def fake_load(path: Path, default: Any = None) -> Any:
        if path == report.DEBUG:
            return debug
        if path.name == "latest-picks.json":
            return picks
        if path.name == "latest-pending-bets.json":
            return []
        return default if default is not None else {}

    monkeypatch.setattr(report.v13, "_load", fake_load)
    monkeypatch.setattr(report, "_read_text", lambda path: "")


def test_declared_zero_debug_counter_overrides_synthetic_ledger_rows(monkeypatch) -> None:
    now = datetime.now(UTC)
    _install_load(
        monkeypatch,
        debug={
            "summary": {
                "started_time_utc": (now - timedelta(minutes=10)).isoformat(),
                "telegram_messages_sent": 0,
                "published_to_telegram": 0,
            }
        },
        picks=[
            {
                "telegram_sent": True,
                "publication_lifecycle_status": "telegram_sent",
                "published_at_utc": (now - timedelta(minutes=2)).isoformat(),
            }
            for _ in range(29)
        ],
    )

    repaired = report.repair_payload(_payload(), now=now)

    assert repaired["funnel"]["main_pipeline_published_count"] == 0
    assert repaired["funnel"]["published_count"] == 0
    assert repaired["status"] != "published"
    diagnostics = repaired["funnel"]["main_pipeline_publication_counter_diagnostics"]
    assert diagnostics["debug_counter_declared"] is True
    assert diagnostics["published_at_utc_accepted_as_send_evidence"] is False


def test_explicit_current_send_timestamp_counts_when_debug_counter_absent(monkeypatch) -> None:
    now = datetime.now(UTC)
    _install_load(
        monkeypatch,
        debug={
            "summary": {
                "started_time_utc": (now - timedelta(minutes=10)).isoformat(),
            }
        },
        picks=[
            {
                "telegram_sent": True,
                "publication_lifecycle_status": "telegram_sent",
                "sent_at_utc": (now - timedelta(minutes=2)).isoformat(),
            }
        ],
    )

    repaired = report.repair_payload(_payload(), now=now)

    assert repaired["funnel"]["main_pipeline_published_count"] == 1
    assert repaired["status"] == "published"


def test_published_at_utc_alone_is_never_send_evidence(monkeypatch) -> None:
    now = datetime.now(UTC)
    _install_load(
        monkeypatch,
        debug={
            "summary": {
                "started_time_utc": (now - timedelta(minutes=10)).isoformat(),
            }
        },
        picks=[
            {
                "telegram_sent": True,
                "publication_lifecycle_status": "telegram_sent",
                "published_at_utc": (now - timedelta(minutes=2)).isoformat(),
            }
        ],
    )

    repaired = report.repair_payload(_payload(), now=now)

    assert repaired["funnel"]["main_pipeline_published_count"] == 0
    assert repaired["status"] != "published"


def test_timeout_rejects_stale_debug_funnel(monkeypatch) -> None:
    now = datetime.now(UTC)
    lifecycle_started = now - timedelta(minutes=12)
    stale_debug = {
        "summary": {
            "started_time_utc": (now - timedelta(days=89)).isoformat(),
            "telegram_messages_sent": 0,
            "raw_candidates": 5,
        },
        "candidates_before_quality": [{"match_key": "old"}],
    }

    def fake_load(path: Path, default: Any = None) -> Any:
        if path == report.LIFECYCLE:
            return {
                "status": "running",
                "started_at_utc": lifecycle_started.isoformat(),
                "github_run_id": "30097336629",
                "stale_debug_removed": True,
            }
        if path == report.DEBUG:
            return stale_debug
        if path.name in {"latest-picks.json", "latest-pending-bets.json"}:
            return []
        return default if default is not None else {}

    monkeypatch.setattr(report.v13, "_load", fake_load)
    monkeypatch.setattr(
        report,
        "_read_text",
        lambda path: "run bot failed or timed out with status 124" if path == report.STEP_STATUS else "",
    )
    payload = _payload()
    payload["funnel"].update(
        {
            "raw_candidates": 5,
            "candidates_before_quality": 5,
            "candidates_after_quality": 4,
            "publishable_candidates": 1,
        }
    )

    repaired = report.repair_payload(payload, now=now)

    assert repaired["status"] == "run_failed"
    assert repaired["top_reason"] == "runner_timeout"
    assert repaired["funnel"]["raw_candidates"] == 0
    assert repaired["funnel"]["candidates_before_quality"] == 0
    assert repaired["funnel"]["candidates_after_quality"] == 0
    assert repaired["funnel"]["publishable_candidates"] == 0
    diagnostics = repaired["diagnostics"]["main_run_lifecycle"]
    assert diagnostics["timed_out"] is True
    assert diagnostics["fresh_debug"] is False
    assert repaired["diagnostics"]["runner_error"] == "runner_timeout_status_124"
