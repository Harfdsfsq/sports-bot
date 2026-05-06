from __future__ import annotations

from datetime import datetime, timezone

from scripts import build_detailed_run_report as detailed
from scripts import publish_controlled_fallback as fallback
from scripts import send_enhanced_telegram_run_report as enhanced

UTC = timezone.utc


def payload(stamp: str):
    return {"created_at_utc": stamp}


def test_enhanced_report_uses_newest_payload_as_reference():
    old = payload("2026-04-27T01:26:52+00:00")
    new = payload("2026-05-05T19:07:33+00:00")

    reference = enhanced.newest_timestamp(old, new)

    assert reference == datetime(2026, 5, 5, 19, 7, 33, tzinfo=UTC)
    assert not enhanced.is_fresh(old, reference)
    assert enhanced.is_fresh(new, reference)


def test_detailed_report_marks_stale_fallback_unusable():
    old_fallback = payload("2026-04-26T22:32:02+00:00")
    current_run = payload("2026-05-05T19:07:33+00:00")
    reference = detailed.newest_timestamp(old_fallback, current_run)

    row = detailed.freshness_row("fallback", old_fallback, reference)

    assert row["present"] is True
    assert row["fresh"] is False
    assert row["age_minutes_vs_reference"] > 60 * 24


def test_controlled_fallback_rejects_stale_artifact_payload():
    old_fallback = payload("2026-04-26T22:32:02+00:00")
    current_run = payload("2026-05-05T19:07:33+00:00")
    reference = fallback.newest_timestamp(old_fallback, current_run)

    assert not fallback.is_payload_fresh(old_fallback, reference)
    assert fallback.is_payload_fresh(current_run, reference)
