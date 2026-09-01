from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from app.schemas import Match, MatchContext, Offer
from app.services import daily_coverage_common as common
from app.services import daily_coverage_ledger as ledger


def _match() -> Match:
    return Match(
        source="test", source_event_id="1", sport_key="soccer", league_name="League",
        home_team="Home", away_team="Away",
        commence_time=datetime(2026, 7, 18, 12, 0, tzinfo=UTC),
        home_team_norm="home", away_team_norm="away", league_key="league",
    )


def _paths(monkeypatch, tmp_path: Path) -> None:
    day_dir = tmp_path / ".data" / "day_inventory"
    export_dir = tmp_path / ".data" / "exports"
    day_dir.mkdir(parents=True)
    export_dir.mkdir(parents=True)
    monkeypatch.setenv("APP_TIMEZONE", "UTC")
    monkeypatch.setenv("DAY_INVENTORY_TARGET_DATE", "2026-07-18")
    monkeypatch.setattr(common, "DAY_DIR", day_dir)
    monkeypatch.setattr(common, "LEDGER_PATH", export_dir / "latest-daily-coverage-ledger.json")
    monkeypatch.setattr(common, "EVIDENCE_PATH", export_dir / "latest-daily-coverage-evidence.json")
    monkeypatch.setattr(ledger, "LEDGER_PATH", export_dir / "latest-daily-coverage-ledger.json")
    monkeypatch.setattr(ledger, "EVIDENCE_PATH", export_dir / "latest-daily-coverage-evidence.json")


def test_offers_and_contexts_are_reconstructed(monkeypatch, tmp_path: Path) -> None:
    _paths(monkeypatch, tmp_path)
    match = _match()
    offer = Offer(source="sstats_pari", bookmaker="Pari", family="totals", selection="Over", price=1.95, point=2.5)
    context = MatchContext(source="clubelo", payload={}, expected_home=1.4, expected_away=1.0)
    ledger.record_provider_result("sstats_pari_odds", "fetch_offers", {match.match_key: [offer]})
    ledger.record_provider_result("clubelo", "fetch_context", {match.match_key: context})
    cached_offers = ledger.cached_provider_data("sstats_pari", "fetch_offers", [match])
    cached_contexts = ledger.cached_provider_data("clubelo", "fetch_context", [match])
    assert cached_offers[match.match_key][0].source == "sstats_pari"
    assert cached_contexts[match.match_key].expected_home == 1.4
