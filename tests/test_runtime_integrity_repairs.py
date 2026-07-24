from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from app.services import focused_alpha_accumulation_runtime_patch_v2 as accumulation
from app.services import settlement_sstats_pagination_runtime_patch as settlement_patch
from app.services.settlement import SettlementService
from scripts import send_harizon_telegram_run_report_v14 as report


def test_daily_report_message_is_not_counted_as_forecast() -> None:
    count, declared = report._debug_main_publication_count(
        {
            "telegram_messages_sent": 1,
            "published_to_telegram": 0,
            "published": 0,
            "candidates_publishable": 0,
        }
    )

    assert declared is True
    assert count == 0


def test_explicit_forecast_counter_is_counted() -> None:
    count, declared = report._debug_main_publication_count(
        {
            "telegram_messages_sent": 2,
            "published_to_telegram": 1,
            "published": 1,
        }
    )

    assert declared is True
    assert count == 1


def test_sstats_settlement_continues_after_ambiguous_1000_count(monkeypatch) -> None:
    offsets: list[int] = []
    first_page = [
        {
            "id": index,
            "date": "2026-07-23T10:00:00+00:00",
            "HomeTeam": f"Home {index}",
            "AwayTeam": f"Away {index}",
        }
        for index in range(1000)
    ]
    second_page = [
        {
            "id": 1001,
            "date": "2026-07-23T16:00:00+00:00",
            "HomeTeam": "FC Dila Gori",
            "AwayTeam": "Apollon Limassol",
        }
    ]

    class FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._payload

    class FakeClient:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return False

        async def get(self, _url, *, params, headers):
            del headers
            offset = int(params["offset"])
            offsets.append(offset)
            batch = first_page if offset == 0 else second_page if offset == 1000 else []
            # SStats may report the current page size in count. count=1000 must
            # therefore not be treated as the complete multi-page total.
            return FakeResponse({"count": len(batch) if batch else 0, "data": batch})

    monkeypatch.setattr(settlement_patch.httpx, "AsyncClient", FakeClient)
    monkeypatch.setenv("SETTLEMENT_SSTATS_MAX_PAGES", "3")
    service = SettlementService(
        SimpleNamespace(
            sstats_timeout_seconds=5.0,
            sstats_api_key="test",
        )
    )

    rows = asyncio.run(
        settlement_patch._fetch_sstats_rows_paged(
            service,
            "2026-07-22",
            "2026-07-24",
        )
    )

    assert offsets == [0, 1000]
    assert len(rows) == 1001
    assert rows[-1]["HomeTeam"] == "FC Dila Gori"
    assert rows[-1]["_settlement_source"] == "sstats"


def test_single_entry_snapshot_is_not_fake_clv() -> None:
    selected_at = datetime(2026, 7, 24, 17, 8, tzinfo=UTC)
    kickoff = datetime(2026, 7, 24, 18, 0, tzinfo=UTC)
    selections = {
        "decision": {
            "decision_key": "decision",
            "selected_at_utc": selected_at.isoformat(),
            "kickoff_utc": kickoff.isoformat(),
            "odds": 2.72,
        }
    }
    observations = [
        {
            "decision_key": "decision",
            "snapshot_at_utc": selected_at.isoformat(),
            "odds": 2.72,
        }
    ]

    accumulation._refresh_closing_v2(
        selections,
        observations,
        kickoff + timedelta(hours=1),
    )

    row = selections["decision"]
    assert row["distinct_pre_kickoff_snapshots"] == 1
    assert row["closing_price_finalized"] is False
    assert row["closing_price_status"] == "insufficient_distinct_snapshots"
    assert row["clv_pct"] is None


def test_later_near_kickoff_snapshot_can_finalize_clv() -> None:
    selected_at = datetime(2026, 7, 24, 17, 0, tzinfo=UTC)
    closing_at = datetime(2026, 7, 24, 17, 50, tzinfo=UTC)
    kickoff = datetime(2026, 7, 24, 18, 0, tzinfo=UTC)
    selections = {
        "decision": {
            "decision_key": "decision",
            "selected_at_utc": selected_at.isoformat(),
            "kickoff_utc": kickoff.isoformat(),
            "odds": 2.10,
        }
    }
    observations = [
        {
            "decision_key": "decision",
            "snapshot_at_utc": selected_at.isoformat(),
            "odds": 2.10,
        },
        {
            "decision_key": "decision",
            "snapshot_at_utc": closing_at.isoformat(),
            "odds": 2.00,
        },
    ]

    accumulation._refresh_closing_v2(
        selections,
        observations,
        kickoff + timedelta(minutes=1),
    )

    row = selections["decision"]
    assert row["closing_price_finalized"] is True
    assert row["closing_price_status"] == "finalized_from_later_near_kickoff_snapshot"
    assert row["clv_pct"] == 5.0
