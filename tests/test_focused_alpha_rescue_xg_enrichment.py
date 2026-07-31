from __future__ import annotations

import json
from pathlib import Path

from scripts import enrich_rescue_candidates_xg_confirmation as enrichment
from scripts import publish_controlled_fallback as fallback


def test_alias_identity_matches_runtime_team_variants_and_reversed_key() -> None:
    las_palmas_candidate = {
        "canonical_match_id": "soccer|ud_las_palmas|neom_sc|2026-07-31",
        "home_team": "UD Las Palmas",
        "away_team": "Neom SC",
    }
    las_palmas_context = {
        "match_key": "soccer|las palmas|neom|2026-07-31",
        "provider": "sstats_form",
    }
    north_star_candidate = {
        "canonical_match_id": "soccer|north_star_u23|capalaba_fc_u23|2026-07-31",
        "home_team": "North Star U23",
        "away_team": "Capalaba FC U23",
    }
    north_star_context = {
        "match_key": "soccer|capalaba|north star|2026-07-31",
        "provider": "sstats_form",
    }

    assert enrichment.key_of(las_palmas_candidate) == enrichment.key_of(las_palmas_context)
    assert enrichment.key_of(north_star_candidate) == enrichment.key_of(north_star_context)


def test_market_implied_flat_values_are_not_reclassified_as_provider_xg() -> None:
    candidate = {
        "expected_home": 1.9879,
        "expected_away": 1.9879,
        "source_summary": {
            "context_sources": ["sstats"],
            "xg": {
                "home": 1.9879,
                "away": 1.9879,
                "source": "market_implied_total_xg",
                "context_path": "market_probability_from_candidate",
            },
        },
        "diagnostics": {
            "xg_enrichment": {
                "source": "market_implied_total_xg",
                "source_mode": "market_implied_total_xg",
            }
        },
    }

    assert enrichment.xg_from_context(candidate) == {}


def test_current_sstats_xg_replaces_market_anchor_for_alias_match(
    tmp_path: Path,
    monkeypatch,
) -> None:
    exports = tmp_path / ".data" / "exports"
    rescue_path = exports / "latest-rescue-candidates.json"
    context_path = exports / "latest-context-observations.json"
    out_path = exports / "latest-rescue-xg-confirmation-enrichment.json"
    exports.mkdir(parents=True)

    candidate = {
        "canonical_match_id": "soccer|north_star_u23|capalaba_fc_u23|2026-07-31",
        "match_key": "soccer|north_star_u23|capalaba_fc_u23|2026-07-31",
        "home_team": "North Star U23",
        "away_team": "Capalaba FC U23",
        "commence_time": "2026-07-31T08:30:00+00:00",
        "family": "totals",
        "selection": "Больше",
        "selection_key": "over",
        "point": 3.5,
        "books_count": 2,
        "market_probability": 0.561798,
        "adjusted_probability": 0.597303,
        "odds": 1.9,
        "expected_home": 1.9879,
        "expected_away": 1.9879,
        "source_summary": {
            "context_sources": ["day_inventory", "inventory_context", "sstats"],
            "xg": {
                "home": 1.9879,
                "away": 1.9879,
                "total_xg": 3.9759,
                "source": "market_implied_total_xg",
                "context_path": "market_probability_from_candidate",
            },
        },
        "diagnostics": {
            "xg_enrichment": {
                "source": "market_implied_total_xg",
                "source_mode": "market_implied_total_xg",
                "context_path": "market_probability_from_candidate",
            }
        },
    }
    context = {
        "match_key": "soccer|capalaba|north star|2026-07-31",
        "provider": "sstats_form",
        "observed_at": "2026-07-31T08:00:29.110673+00:00",
        "metrics": {
            "expected_home": 1.454,
            "expected_away": 1.623,
        },
    }
    rescue_path.write_text(json.dumps([candidate]), encoding="utf-8")
    context_path.write_text(json.dumps([context]), encoding="utf-8")

    monkeypatch.setattr(enrichment, "CANDIDATE_PATHS", [rescue_path])
    monkeypatch.setattr(enrichment, "CONTEXT_PATHS", [context_path])
    monkeypatch.setattr(enrichment, "OUT", out_path)

    assert enrichment.main() == 0

    saved = json.loads(rescue_path.read_text(encoding="utf-8"))[0]
    report = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["expected_home"] == 1.454
    assert saved["expected_away"] == 1.623
    assert saved["source_summary"]["xg"]["source"] == "sstats_form"
    assert saved["diagnostics"]["xg_enrichment"]["source_mode"] == "sstats_form"
    assert report["xg_added"] == 1
    assert report["market_implied_xg_added"] == 0
    assert report["missing_context_match"] == 0

    xg_sanity = fallback.candidate_metrics(saved)["xg_sanity"]
    assert xg_sanity["xg_source"] == "sstats_form"
    assert xg_sanity["xg_hard_confirmation"] is True
    assert xg_sanity["xg_direction_evaluated"] is True
    assert xg_sanity["xg_direction_ok"] is False
