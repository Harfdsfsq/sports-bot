from scripts.build_day_inventory_coverage_truth import generated_match_key_variants, row_key_variants


def test_row_key_variants_bridge_date_key_and_runtime_key():
    row = {
        "match_key": "2026-05-30|arsenal ceska lipa|varnsdorf",
        "home_team": "FK Arsenal Ceska Lipa",
        "away_team": "FK Varnsdorf",
        "kickoff_utc": "2026-05-30T15:00:00+00:00",
    }
    variants = row_key_variants(row)
    assert "2026-05-30|arsenal ceska lipa|varnsdorf" in variants
    assert "soccer|arsenal ceska lipa|varnsdorf|2026-05-30" in variants
    assert "soccer|varnsdorf|arsenal ceska lipa|2026-05-30" in variants


def test_generated_match_key_variants_include_sorted_runtime_key():
    row = {
        "home_team": "FC Banik Ostrava",
        "away_team": "1. FC Slovacko",
        "kickoff_utc": "2026-05-30T09:00:00+00:00",
    }
    variants = generated_match_key_variants(row)
    assert "soccer|1 slovacko|banik ostrava|2026-05-30" in variants
