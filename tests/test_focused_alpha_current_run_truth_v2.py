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
