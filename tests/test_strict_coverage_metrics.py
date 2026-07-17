from __future__ import annotations

from app.services.coverage_planner import MatchCoverageRow
from app.services.strict_coverage_metrics import install


def test_ready_for_publish_requires_two_independent_sources() -> None:
    install()
    row = MatchCoverageRow(
        match_key="m", kickoff_utc="2026-07-18T12:00:00+00:00",
        league_name="League", home_team="Home", away_team="Away",
    )
    row.odds_sources.add("odds_api_io")
    row.books.update({"book-a", "book-b"})
    row.context_sources.add("sstats")
    assert row.as_dict()["ready_for_publish"] is False
    row.odds_sources.add("sstats_pari")
    row.context_sources.add("clubelo")
    assert row.as_dict()["ready_for_publish"] is True
