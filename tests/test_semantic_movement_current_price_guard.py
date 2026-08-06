from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from scripts import patch_semantic_movement_current_price_guard as guard


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _reset(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    offers: list[dict[str, Any]],
    lines: dict[str, Any],
) -> None:
    offer_path = tmp_path / "offers.json"
    history_path = tmp_path / "line-history.json"
    rescue_path = tmp_path / "rescue.json"
    out_path = tmp_path / "report.json"
    _write(
        offer_path,
        {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "offers": offers,
        },
    )
    _write(history_path, {"updated_at_utc": datetime.now(UTC).isoformat(), "lines": lines})
    _write(rescue_path, [])
    monkeypatch.setattr(guard, "OFFER_PATHS", (offer_path,))
    monkeypatch.setattr(guard, "LINE_HISTORY_PATHS", (history_path,))
    monkeypatch.setattr(guard, "RESCUE_PATH", rescue_path)
    monkeypatch.setattr(guard, "OUT", out_path)
    monkeypatch.setattr(guard, "_INSTALLED", False)
    monkeypatch.setattr(guard, "_OFFER_INDEX_CACHE", None)
    monkeypatch.setattr(guard, "_LINE_INDEX_CACHE", None)
    guard._CACHE.clear()


def _offer(
    home: str,
    away: str,
    kickoff: str,
    bookmaker: str,
    price: float,
) -> dict[str, Any]:
    return {
        "home_team": home,
        "away_team": away,
        "commence_time": kickoff,
        "family": "totals",
        "selection": "under",
        "point": 2.5,
        "bookmaker": bookmaker,
        "price": price,
    }


def _line(
    match_key: str,
    home: str,
    away: str,
    kickoff: str,
    odds: float,
    status: str,
    passed: bool,
) -> dict[str, Any]:
    return {
        "last_snapshot": {
            "match_key": match_key,
            "home_team": home,
            "away_team": away,
            "kickoff_utc": kickoff,
            "captured_at_utc": datetime.now(UTC).isoformat(),
            "family": "totals",
            "selection": "Меньше",
            "selection_key": "under",
            "point": 2.5,
            "bookmaker": "betfair_exchange",
            "odds": odds,
        },
        "last_guard": {
            "line_movement_lifecycle_status": status,
            "passed": passed,
            "final_pre_kickoff_check": True,
            "no_more_cron_before_kickoff": True,
            "current_odds": odds,
            "line_move_pct": -12.766 if not passed else 0.0,
            "reasons": ["line_moved_against_candidate:-12.8%"] if not passed else [],
        },
    }


def test_twente_alias_conflict_and_stale_selected_price_are_rejected(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    kickoff = "2026-07-23T18:00:00+00:00"
    offers = [
        _offer("FC Twente Enschede", "Ferencvarosi Budapest", kickoff, "Bet365", 2.20),
        _offer("FC Twente Enschede", "Ferencvarosi Budapest", kickoff, "Betfair Exchange", 2.35),
        _offer("FC Twente Enschede", "Ferencvarosi Budapest", kickoff, "Sbobet", 2.21),
    ]
    lines = {
        "soccer|fc_twente_enschede|ferencvarosi_budapest|2026-07-23|totals|under|2.5|betfair_exchange": _line(
            "soccer|fc_twente_enschede|ferencvarosi_budapest|2026-07-23",
            "FC Twente Enschede",
            "Ferencvarosi Budapest",
            kickoff,
            2.05,
            "movement_failed",
            False,
        ),
        "soccer|fc_twente|ferencv_ros_tc|2026-07-23|totals|under|2.5|betfair_exchange": _line(
            "soccer|fc_twente|ferencv_ros_tc|2026-07-23",
            "FC Twente",
            "Ferencváros TC",
            kickoff,
            2.16,
            "movement_confirmed",
            True,
        ),
    }
    _reset(monkeypatch, tmp_path, offers, lines)
    candidate = {
        "match_key": "soccer|fc_twente|ferencv_ros_tc|2026-07-23",
        "home_team": "FC Twente",
        "away_team": "Ferencváros TC",
        "commence_time": kickoff,
        "family": "totals",
        "selection": "Меньше",
        "point": 2.5,
        "bookmaker": "betfair_exchange",
        "odds": 2.16,
    }

    diagnostics: dict[str, Any] = {}
    reasons = guard.semantic_integrity_reasons(candidate, diagnostics)

    assert "semantic_selected_price_not_current:2.160/2.350" in reasons
    assert "semantic_line_movement_alias_conflict" in reasons
    assert diagnostics["semantic_current_price_guard"]["current_price"] == 2.35
    assert diagnostics["semantic_line_movement_guard"]["matching_entries"] == 2


def test_zilina_current_price_and_confirmed_movement_pass(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    kickoff = "2026-07-23T18:30:00+00:00"
    offers = [
        _offer("MSK Zilina", "GKS Katowice", kickoff, "Bet365", 1.975),
        _offer("MSK Zilina", "GKS Katowice", kickoff, "Betfair Exchange", 2.10),
    ]
    key = "soccer|msk_zilina|gks_katowice|2026-07-23|totals|under|2.5|betfair_exchange"
    lines = {
        key: _line(
            "soccer|msk_zilina|gks_katowice|2026-07-23",
            "MSK Zilina",
            "GKS Katowice",
            kickoff,
            2.10,
            "movement_confirmed",
            True,
        )
    }
    _reset(monkeypatch, tmp_path, offers, lines)
    candidate = {
        "match_key": "soccer|msk_zilina|gks_katowice|2026-07-23",
        "home_team": "MSK Zilina",
        "away_team": "GKS Katowice",
        "commence_time": kickoff,
        "family": "totals",
        "selection": "Меньше",
        "point": 2.5,
        "bookmaker": "betfair_exchange",
        "odds": 2.10,
    }

    assert guard.semantic_integrity_reasons(candidate, {}) == []


def test_missing_selected_book_reports_current_price_and_not_publishable_reason(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    kickoff = "2026-08-06T23:00:00+00:00"
    offers = [
        _offer("SC Corinthians SP", "SC Internacional RS", kickoff, "Unibet", 2.30),
    ]
    key = "soccer|corinthians sp|internacional rs|2026-08-06|totals|over|2.5|bet365"
    lines = {
        key: {
            "last_snapshot": {
                "match_key": "soccer|corinthians sp|internacional rs|2026-08-06",
                "home_team": "SC Corinthians SP",
                "away_team": "SC Internacional RS",
                "kickoff_utc": kickoff,
                "captured_at_utc": datetime.now(UTC).isoformat(),
                "family": "totals",
                "selection": "Больше 2.5",
                "selection_key": "over",
                "point": 2.5,
                "bookmaker": "bet365",
                "odds": 2.60,
            },
            "last_guard": {
                "line_movement_lifecycle_status": "not_publishable",
                "passed": False,
                "final_pre_kickoff_check": True,
                "no_more_cron_before_kickoff": True,
                "current_odds": 2.60,
                "line_move_pct": 0.0,
                "reasons": ["current_edge_below_floor:1.4<1.4"],
            },
        }
    }
    _reset(monkeypatch, tmp_path, offers, lines)
    candidate = {
        "match_key": "soccer|corinthians sp|internacional rs|2026-08-06",
        "home_team": "SC Corinthians SP",
        "away_team": "SC Internacional RS",
        "commence_time": kickoff,
        "family": "totals",
        "selection": "Больше 2.5",
        "point": 2.5,
        "bookmaker": "bet365",
        "metrics": {"odds": 2.60},
    }

    diagnostics: dict[str, Any] = {}
    reasons = guard.semantic_integrity_reasons(candidate, diagnostics)

    assert "semantic_selected_book_current_price_missing" in reasons
    assert "semantic_selected_price_not_current:2.600/2.300" in reasons
    assert "semantic_line_movement_failed" not in reasons
    assert "semantic_line_movement_not_publishable:current_edge_below_floor:1.4<1.4" in reasons
    price_guard = diagnostics["semantic_current_price_guard"]
    assert price_guard["selected_book_missing_from_current_snapshot"] is True
    assert price_guard["current_bookmaker"] == "Unibet"
    assert price_guard["current_price"] == 2.3


def test_match_total_does_not_use_team_total_price(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    kickoff = "2026-07-31T00:00:00+00:00"
    offers = [
        {
            "home_team": "Independiente Rivadavia",
            "away_team": "CA Huracan",
            "commence_time": kickoff,
            "family": "totals",
            "market_name": "total",
            "selection": "over",
            "point": 1.5,
            "bookmaker": "Unibet",
            "price": 1.60,
        },
        {
            "home_team": "Independiente Rivadavia",
            "away_team": "CA Huracan",
            "commence_time": kickoff,
            "family": "teamTotals",
            "market_name": "team total away",
            "selection": "over",
            "point": 1.5,
            "bookmaker": "Unibet",
            "price": 4.60,
        },
    ]
    _reset(monkeypatch, tmp_path, offers, {})
    monkeypatch.setenv("PUBLISH_REQUIRE_LINE_MOVEMENT", "false")
    candidate = {
        "match_key": "soccer|independiente_rivadavia|ca_huracan|2026-07-31",
        "home_team": "Independiente Rivadavia",
        "away_team": "CA Huracan",
        "commence_time": kickoff,
        "family": "totals",
        "selection": "Больше",
        "point": 1.5,
        "bookmaker": "Unibet",
        "odds": 1.60,
    }

    diagnostics: dict[str, Any] = {}
    reasons = guard.semantic_integrity_reasons(candidate, diagnostics)

    assert not any(
        reason.startswith("semantic_selected_price_not_current") for reason in reasons
    )
    assert diagnostics["semantic_current_price_guard"]["matching_offers"] == 1
    assert diagnostics["semantic_current_price_guard"]["current_price"] == 1.60


def test_install_sanitizes_rescue_and_deduplicates_semantic_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    kickoff = "2026-07-23T18:00:00+00:00"
    offers = [
        _offer("FC Twente Enschede", "Ferencvarosi Budapest", kickoff, "Betfair Exchange", 2.35)
    ]
    lines = {
        "soccer|fc_twente_enschede|ferencvarosi_budapest|2026-07-23|totals|under|2.5|betfair_exchange": _line(
            "soccer|fc_twente_enschede|ferencvarosi_budapest|2026-07-23",
            "FC Twente Enschede",
            "Ferencvarosi Budapest",
            kickoff,
            2.05,
            "movement_failed",
            False,
        )
    }
    _reset(monkeypatch, tmp_path, offers, lines)
    aliases = [
        {
            "match_key": "soccer|fc_twente|ferencv_ros_tc|2026-07-23",
            "home_team": "FC Twente",
            "away_team": "Ferencváros TC",
            "commence_time": kickoff,
            "family": "totals",
            "selection": "Меньше",
            "point": 2.5,
            "bookmaker": "betfair_exchange",
            "odds": 2.16,
        },
        {
            "match_key": "soccer|fc_twente_enschede|ferencvarosi_budapest|2026-07-23",
            "home_team": "FC Twente Enschede",
            "away_team": "Ferencvarosi Budapest",
            "commence_time": kickoff,
            "family": "totals",
            "selection": "Меньше",
            "point": 2.5,
            "bookmaker": "betfair_exchange",
            "odds": 2.05,
        },
    ]
    _write(guard.RESCUE_PATH, aliases)
    guard._CACHE.clear()

    base = SimpleNamespace(
        hard_reject_reasons=lambda _candidate, _metrics, _sent: [],
        canonical_publication_key=lambda row: str(row.get("match_key")),
        select_top_picks=lambda _viable, _bankroll: [
            (aliases[0], {}, "уровень A", 5.0),
            (aliases[1], {}, "уровень A", 5.0),
        ],
    )
    result = guard.install(base)

    assert result["sanitizer"]["rescue_rows_removed"] == 2
    assert json.loads(guard.RESCUE_PATH.read_text(encoding="utf-8")) == []
    assert base.canonical_publication_key(aliases[0]) == base.canonical_publication_key(aliases[1])
    assert len(base.select_top_picks([], {})) == 1
