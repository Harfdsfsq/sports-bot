from scripts.send_harizon_telegram_run_report_v8 import render


def test_report_separates_provider_backlog_from_model_scope() -> None:
    text = render(
        {
            "coverage": {"day_inventory_total": 300, "matches_seen": 6},
            "diagnostics": {
                "provider_coverage_routing": {
                    "provider_targets": 190,
                    "model_targets": 24,
                    "role_assignments": 427,
                    "publication_scope_widened": False,
                }
            },
            "top_reason": "no viable controlled fallback",
        }
    )

    assert "Очередь provider-enrichment: 190 активных матчей" in text
    assert "model scope 24" in text
    assert "не расширяет публикационный scope" in text
