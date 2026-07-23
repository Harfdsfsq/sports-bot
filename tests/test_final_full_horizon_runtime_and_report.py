from __future__ import annotations

import asyncio
import importlib.util
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.services import daily_coverage_full_inventory_provider_patch as patch


def _write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _row(home: str, away: str, kickoff: datetime) -> dict[str, object]:
    return {
        "match_key": f"{kickoff.date().isoformat()}|{home.lower()}|{away.lower()}",
        "sport_key": "soccer",
        "league_name": "Test League",
        "home_team": home,
        "away_team": away,
        "kickoff_utc": kickoff.isoformat(),
        "source_ids": {"odds_api_io": f"{home}-{away}"},
    }


def test_persisted_cohort_includes_tomorrow_and_rejects_outside_horizon(
    tmp_path: Path, monkeypatch
) -> None:
    now = datetime(2026, 7, 23, 6, tzinfo=UTC)
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-07-23")
    monkeypatch.setenv("RUN_DAYS_AHEAD", "2")
    monkeypatch.setattr(patch, "EXPORT", tmp_path / "exports")
    monkeypatch.setattr(patch, "DAY_DIR", tmp_path / "day_inventory")
    rows = [
        _row("Today A", "Today B", now + timedelta(hours=2)),
        _row("Tomorrow A", "Tomorrow B", now + timedelta(days=1, hours=2)),
        _row("Past A", "Past B", now - timedelta(hours=2)),
        _row("Outside A", "Outside B", now + timedelta(days=2, hours=8)),
        {
            "match_key": "2026-07-23|identity|only",
            "league_name": "Test League",
        },
    ]
    _write(
        patch.EXPORT / "latest-daily-coverage-cohort.json",
        {
            "date_local": "2026-07-23",
            "created_at_utc": now.isoformat(),
            "matches": rows,
        },
    )
    runner = SimpleNamespace(settings=SimpleNamespace(tzinfo=ZoneInfo("Europe/Moscow")))

    matches, source, rows_seen = patch._persisted_cohort_matches(runner, now)

    assert rows_seen == 5
    assert source.endswith("latest-daily-coverage-cohort.json")
    assert {match.home_team for match in matches} == {"Today A", "Tomorrow A"}
    assert all(match.metadata["daily_coverage_cohort"] for match in matches)


def test_install_reasserts_after_runtime_replaces_runner_methods(monkeypatch) -> None:
    monkeypatch.setattr(patch, "_INSTALLED", False)
    monkeypatch.setattr(patch, "filter_matches", lambda _p, _m, rows: rows)

    class Runner:
        def _filter_matches(self, matches, _now):
            return matches

        async def _fetch_provider(
            self, _provider, _method, matches, *, empty_data
        ):
            return empty_data, {"received": len(matches)}, {}

    first = patch.install(Runner)
    assert first["status"] == "installed"
    assert getattr(Runner._fetch_provider, "_harizon_full_inventory_provider_patch")

    async def replacement(self, _provider, _method, matches, *, empty_data):
        return empty_data, {"received": len(matches)}, {}

    def replacement_filter(self, matches, _now):
        return matches

    Runner._fetch_provider = replacement
    Runner._filter_matches = replacement_filter
    second = patch.install(Runner)

    assert second["status"] == "reasserted"
    assert getattr(Runner._fetch_provider, "_harizon_full_inventory_provider_patch")
    assert getattr(Runner._filter_matches, "_harizon_full_inventory_filter_capture")

    runner = Runner()
    runner.settings = SimpleNamespace(tzinfo=UTC)
    runner._harizon_full_horizon_coverage_matches = []
    provider = SimpleNamespace(
        __class__=SimpleNamespace(__module__="app.providers.odds_api_io")
    )
    _, stats, _ = asyncio.run(
        runner._fetch_provider(provider, "fetch_offers", [], empty_data={})
    )
    assert stats["full_horizon_runtime_reasserted"] is True
    assert stats["publication_window_relaxed"] is False


def _load_report_module() -> object:
    path = Path(__file__).resolve().parents[1] / "scripts" / "send_harizon_telegram_run_report_v13.py"
    spec = importlib.util.spec_from_file_location("report_v13_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_replaces_stale_debug_numbers_with_fresh_runtime_files(
    tmp_path: Path, monkeypatch
) -> None:
    report = _load_report_module()
    monkeypatch.setattr(report, "EXPORT", tmp_path)
    now = datetime.now(UTC)
    _write(
        tmp_path / "latest-daily-coverage-cohort.json",
        {"created_at_utc": now.isoformat(), "matches": [{} for _ in range(300)]},
    )
    _write(
        tmp_path / "latest-odds-api-io-offer-snapshot.json",
        {
            "created_at_utc": now.isoformat(),
            "rows_count": 9813,
            "matches_count": 83,
            "matches_with_2plus_books_same_side_market": 67,
            "stats": {"events_matched": 106, "offers_parsed": 9813},
        },
    )
    _write(
        tmp_path / "latest-daily-coverage-ledger.json",
        {
            "updated_at_utc": now.isoformat(),
            "provider_runs": [
                {
                    "provider": "odds_api_io",
                    "method": "fetch_offers",
                    "observed_at_utc": now.isoformat(),
                    "matched": 82,
                    "stats": {
                        "event_requests": 0,
                        "odds_requests": 22,
                        "events_matched": 106,
                        "offers_parsed": 10262,
                        "response_errors": 0,
                    },
                },
                {
                    "provider": "sstats",
                    "method": "fetch_context",
                    "observed_at_utc": now.isoformat(),
                    "matched": 75,
                    "stats": {
                        "requests": 2,
                        "rows_fetched": 9255,
                        "contexts_built": 75,
                        "response_errors": 0,
                    },
                },
            ],
        },
    )
    _write(
        tmp_path / "latest-sstats-deep-inventory-enrichment.json",
        {"created_at_utc": now.isoformat(), "enriched_matches": 48},
    )
    text = (
        "• Инвентарь дня: собрано 300/300. Runtime rows processed: 36 "
        "(это не размер inventory).\n"
        "• odds-api.io: events req 0, odds req 4; смэтчил матчей 36; "
        "offers 881; 2+ букмекера 0; ошибок 0.\n"
        "• SStats: запросы 31; сырых строк 26487; контекстов 30; "
        "deep-enriched 48; team-form 0; direct 0; ошибок 0."
    )

    text = report._repair_runtime_scope(text)
    text = report._repair_odds_api_line(text)
    text = report._repair_sstats_line(text)
    text = report._repair_api_football_line(text)

    assert "Provider coverage horizon: 300 real fixtures" in text
    assert "events matched 106" in text
    assert "offers 9813" in text
    assert "контекстов 75" in text
    assert "API-Football: свежего runtime evidence нет" in text
    assert "смэтчил матчей 36" not in text


def test_report_does_not_use_stale_provider_snapshot(
    tmp_path: Path, monkeypatch
) -> None:
    report = _load_report_module()
    monkeypatch.setattr(report, "EXPORT", tmp_path)
    stale = datetime.now(UTC) - timedelta(days=2)
    _write(
        tmp_path / "latest-odds-api-io-offer-snapshot.json",
        {
            "created_at_utc": stale.isoformat(),
            "rows_count": 99999,
            "stats": {"events_matched": 999},
        },
    )
    original = "• odds-api.io: old truthful line"
    assert report._repair_odds_api_line(original) == original
