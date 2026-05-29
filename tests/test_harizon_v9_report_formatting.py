from scripts.send_harizon_telegram_run_report_v9 import (
    _reason_ru_patched,
    _render_samples_with_points,
    _selection_with_point,
)


def test_selection_with_point_adds_total_line() -> None:
    row = {"family": "totals", "selection": "Меньше", "point": 2.5}
    assert _selection_with_point(row) == "Меньше 2.5"


def test_high_odds_reason_is_translated() -> None:
    text = _reason_ru_patched("quality_quality_high_odds_totals_xg_headroom_guard")
    assert "высокий коэффициент" in text
    assert "xG" in text


def test_render_samples_includes_point_and_translated_reason() -> None:
    payload = {
        "samples": {
            "fallback_evaluated": [
                {
                    "home_team": "A",
                    "away_team": "B",
                    "family": "totals",
                    "selection": "Меньше",
                    "point": 2.5,
                    "reject_reasons": ["quality_high_odds_totals_xg_headroom_guard"],
                    "metrics": {"odds": 3.5, "canonical_ev_pct": 34.1, "canonical_edge_pp": 9.7, "quality_score": 100},
                }
            ]
        }
    }
    lines = _render_samples_with_points(payload)
    joined = "\n".join(lines)
    assert "Меньше 2.5 @3.50" in joined
    assert "высокий коэффициент" in joined
