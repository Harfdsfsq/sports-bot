from scripts.controlled_fallback_prepublish_guard import _should_block_send
from scripts.repair_inventory_source_counts import candidate_evidence


def test_controlled_fallback_prepublish_guard_allows_no_pick_report():
    text = """🧾 Отчёт по запуску бота\n❌ Прогнозов не было.\n\nОсновной слой качества не нашёл чистую ставку."""
    blocked, reason, details = _should_block_send(text, {})
    assert blocked is False
    assert reason == "non_pick_report"
    assert details["text_kind"] == "no_pick_or_diagnostic_report"


def test_controlled_fallback_prepublish_guard_still_blocks_real_pick_without_sources():
    text = """🔥 1 контролируемый прогноз на ближайшие 12 часов\n🎯 Ставка: Тотал — Меньше (3.5)\n💰 Сумма ставки: 5.00\nodds sources: 1\nконтекст: 2\nкачество 88.0\nуровень A"""
    blocked, reason, details = _should_block_send(text, {})
    assert blocked is True
    assert reason.startswith("telegram_price_odds_sources_below_min")
    assert details["odds_sources"] == 1


def test_controlled_fallback_prepublish_guard_requires_two_b_tier_contexts():
    text = "уровень B\nodds sources: 1\nконтекст: 1\nкачество 88.0"
    blocked, reason, details = _should_block_send(text, {})
    assert blocked is True
    assert reason == "telegram_context_sources_below_min:1/2"
    assert details["min_odds_sources"] == 1
    assert details["min_context_sources"] == 2

    selected = {
        "tier": "B",
        "independent_odds_sources_count": 1,
        "confirmation_sources_count": 2,
        "quality_score": 88.0,
    }
    blocked, reason, details = _should_block_send("уровень B", selected)
    assert blocked is False
    assert reason == "ok"
    assert details["odds_sources"] == 1
    assert details["context_sources"] == 2


def test_source_repair_uses_runtime_audit_sample_counts():
    candidate = {
        "match_key": "soccer|columbus crew 2|toronto 2|2026-05-25",
        "exact_odds_sources": ["bzzoiro", "odds_api_io_account1"],
        "exact_odds_sources_count": 2,
        "exact_books_count": 2,
        "context_sources": ["bzzoiro", "sstats", "clubelo", "model_xg"],
        "context_sources_count": 4,
        "confirmation_sources_count": 4,
    }
    ev = candidate_evidence(candidate, "latest-api-coverage-consensus-runtime-patch.json")
    assert ev["counts"]["independent_odds_sources_count"] >= 2
    assert ev["counts"]["books_count"] >= 2
    assert ev["counts"]["context_sources_count"] >= 4
    assert "bzzoiro" in ev["odds_sources"]
    assert "sstats" in ev["context_sources"]
