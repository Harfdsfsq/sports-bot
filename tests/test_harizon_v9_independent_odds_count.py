from scripts.send_harizon_telegram_run_report_v9 import _independent_odds_count


def test_independent_odds_count_reads_line_sources_from_nested_summary() -> None:
    row = {
        "metrics": {
            "source_summary": {
                "line_sources": ["bzzoiro", "odds_api_io"],
            }
        },
    }

    assert _independent_odds_count(row) == 2
