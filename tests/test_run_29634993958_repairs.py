from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.providers.sstats_pari_parser import extract_odds, parse_offers
from app.schemas import Match
from app.services import clubelo_strict_match_patch as clubelo_patch
from app.services import daily_coverage_fixed_cohort_patch as cohort_patch
from app.services import daily_coverage_plan as coverage_plan
from app.services import daily_coverage_source_integrity_patch as source_patch
from app.services import sstats_pari_runtime_repair as pari_patch
from app.services import sstats_team_form_alias_patch as sstats_alias_patch


def _match(home: str = "Alpha FC", away: str = "Beta FC") -> Match:
    return Match(
        source="test",
        source_event_id="1",
        sport_key="soccer",
        league_name="Test League",
        home_team=home,
        away_team=away,
        commence_time=datetime.now(UTC) + timedelta(hours=6),
        home_team_norm=home.lower(),
        away_team_norm=away.lower(),
        league_key="test",
    )


def test_pari_nested_match_info_is_normalized() -> None:
    payload = {
        "status": "OK",
        "count": 1,
        "data": [
            {
                "matchInfo": {
                    "eventId": 77,
                    "startDate": (datetime.now(UTC) + timedelta(hours=6)).isoformat(),
                    "status": "NotStarted",
                    "tournament": {"name": "Test League"},
                    "homeTeam": {"name": "Alpha FC"},
                    "awayTeam": {"name": "Beta FC"},
                }
            }
        ],
    }
    rows = pari_patch._extract_list(payload)
    assert rows[0]["eventId"] == 77
    assert rows[0]["homeTeam"]["name"] == "Alpha FC"
    assert pari_patch._total_count(payload, 0) == 1


def test_pari_matcher_uses_nested_event_and_strict_score() -> None:
    match = _match()
    event = {
        "matchInfo": {
            "eventId": 77,
            "startDate": match.commence_time.isoformat(),
            "status": "NotStarted",
            "tournament": {"name": match.league_name},
            "homeTeam": {"name": match.home_team},
            "awayTeam": {"name": match.away_team},
        }
    }
    stats = {
        "rows_missing_match_info": 0,
        "events_skipped_finished": 0,
        "events_without_date_candidates": 0,
    }
    preview = {"matched": [], "unmatched": []}
    mapping = pari_patch._match_events([match], [event], stats, preview)
    assert match.match_key in mapping
    assert mapping[match.match_key][1]["eventId"] == 77


def test_pari_current_odds_are_parsed_as_full_time_offers() -> None:
    match = _match()
    payload = {
        "status": "OK",
        "data": {
            "matchInfo": {"eventId": 77},
            "currentOdds": [
                {
                    "id": 1,
                    "name": "Full Time. Goals Total: Over 2.50",
                    "value": 1.91,
                },
                {
                    "id": 2,
                    "name": "Full Time. Asian Handicap: Home -1.50",
                    "value": 2.05,
                },
                {
                    "id": 3,
                    "name": "Full Time. Индивидуальный тотал команды Хозяев: Over 1.00",
                    "value": 1.80,
                },
            ],
        },
    }
    offers = parse_offers(extract_odds(payload), match, "77")
    assert {(item.family, item.selection, item.point) for item in offers} == {
        ("totals", "Over", 2.5),
        ("spreads", match.home_team, -1.5),
    }


def test_coverage_filter_bridges_inventory_and_runtime_keys(monkeypatch) -> None:
    match = _match("Alpha FC", "Beta FC")
    local_date = match.commence_time.astimezone(coverage_plan.app_timezone()).date().isoformat()
    monkeypatch.setattr(
        coverage_plan,
        "load_plan",
        lambda: {
            "assignments": {
                "sstats": {"context": [f"{local_date}|Beta FC|Alpha FC"]}
            }
        },
    )
    assert coverage_plan.filter_matches("sstats", "fetch_context", [match]) == [match]


def test_sstats_team_resolver_rejects_short_and_phonetic_collisions() -> None:
    keys = {"os", "boulogne", "bologna", "logan roos"}
    assert (
        sstats_alias_patch._resolve_team_key_strict(
            None,
            "Logan Roos FC",
            keys,
            {},
        )
        == "logan roos"
    )
    assert (
        sstats_alias_patch._resolve_team_key_strict(None, "Bologna", keys, {})
        == "bologna"
    )
    assert (
        sstats_alias_patch._resolve_team_key_strict(
            None,
            "Unknown Bologna-ish",
            {"boulogne"},
            {},
        )
        is None
    )


def test_clubelo_rejects_unrelated_high_level_clubs() -> None:
    class Provider:
        pass

    rows = [
        {"Club": "Cottbus", "Elo": "1342"},
        {"Club": "Jablonec", "Elo": "1445"},
    ]
    assert clubelo_patch._find(Provider(), "Bay Olympic", rows) is None
    exact = rows + [{"Club": "Bay Olympic", "Elo": "1500"}]
    assert clubelo_patch._find(Provider(), "Bay Olympic", exact)["Elo"] == "1500"


def test_source_repair_collapses_sidecar_to_actual_source(monkeypatch, tmp_path: Path) -> None:
    date_key = "2026-07-18"
    evidence_path = tmp_path / "evidence.json"
    ledger_path = tmp_path / "ledger.json"
    evidence = {
        "date_local": date_key,
        "matches": {
            "m": {
                "context": {
                    "openligadb": {
                        "updated_at_utc": "2026-07-18T01:00:00+00:00",
                        "data": {"source": "clubelo", "payload": {}},
                    },
                    "clubelo": {
                        "updated_at_utc": "2026-07-18T02:00:00+00:00",
                        "data": {"source": "clubelo", "payload": {}},
                    },
                }
            }
        },
    }
    ledger = {
        "date_local": date_key,
        "matches": {"m": {"context_sources": ["openligadb", "clubelo"]}},
    }
    evidence_path.write_text(json.dumps(evidence), encoding="utf-8")
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    monkeypatch.setattr(source_patch, "EVIDENCE_PATH", evidence_path)
    monkeypatch.setattr(source_patch, "LEDGER_PATH", ledger_path)
    monkeypatch.setattr(source_patch, "evidence_path", lambda _date: evidence_path)
    monkeypatch.setattr(source_patch, "ledger_path", lambda _date: ledger_path)
    monkeypatch.setattr(source_patch, "target_date", lambda: date_key)
    result = source_patch.repair_state()
    repaired_evidence = json.loads(evidence_path.read_text(encoding="utf-8"))
    repaired_ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    assert result["entries_moved_to_actual_source"] == 1
    assert list(repaired_evidence["matches"]["m"]["context"]) == ["clubelo"]
    assert repaired_ledger["matches"]["m"]["context_sources"] == ["clubelo"]


def test_fixed_cohort_does_not_rotate_after_kickoff(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cohort_patch, "DAY_DIR", tmp_path)
    start = datetime(2026, 7, 18, 0, 0, tzinfo=UTC)
    rows = [
        {
            "match_key": f"m-{index}",
            "kickoff_utc": (start + timedelta(minutes=index)).isoformat(),
            "home_team": f"H{index}",
            "away_team": f"A{index}",
        }
        for index in range(300)
    ]
    monkeypatch.setattr(cohort_patch, "_ORIGINAL_RANK", lambda *_args: list(rows))
    first = cohort_patch._rank(rows, {"matches": {}}, start, "2026-07-18")
    second = cohort_patch._rank(
        list(reversed(rows)),
        {"matches": {}},
        start + timedelta(hours=12),
        "2026-07-18",
    )
    assert [row["match_key"] for row in first] == [row["match_key"] for row in second]
    assert len(second) == 300
    assert second[0]["hours_to_kickoff"] < 0
