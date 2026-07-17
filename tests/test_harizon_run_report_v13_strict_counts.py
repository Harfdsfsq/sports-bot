from __future__ import annotations

import json


def test_strict_report_replaces_bookmaker_only_b_cover_count(tmp_path, monkeypatch):
    from scripts import send_harizon_telegram_run_report_v13 as report

    monkeypatch.setattr(report, "EXPORT_DIR", tmp_path)
    (tmp_path / "latest-day-inventory-coverage-truth.json").write_text(
        json.dumps(
            {
                "counts": {
                    "matches_a_tier_coverage_ready": 13,
                    "matches_b_tier_watch_ready": 13,
                    "matches_with_2plus_odds_sources": 17,
                    "matches_with_2plus_price_confirmations": 156,
                    "matches_with_2plus_context_sources": 71,
                }
            }
        ),
        encoding="utf-8",
    )
    text = "\n".join(
        [
            "• A-tier strict-ready: 17 | main опубликовано: 0",
            "• B-tier 2+ line/2+ bookmaker/2+ context coverage: 156 | fallback опубликовано: 0",
            "• A-cover 2+ odds-source ∩ 2+ букмекера ∩ 2+ контекста: до 17 матчей; B-cover: до 156 матчей.",
            "• 2+ independent odds-source — A-tier strict metric; для B-tier не обязательный блок.",
        ]
    )

    rendered = report._replace_two_plus_contract_text(text)

    assert "A-tier strict-ready: 13" in rendered
    assert "coverage: 13 | fallback" in rendered
    assert "B-cover strict intersection: 13 матчей" in rendered
    assert "для B-tier не обязательный" not in rendered
    assert "обязателен и для A, и для B" in rendered


def test_autonomous_section_uses_persisted_flat_ledgers(tmp_path, monkeypatch):
    from scripts import send_harizon_telegram_run_report_v13 as report

    monkeypatch.setattr(report, "EXPORT_DIR", tmp_path)
    (tmp_path / "latest-autonomous-coverage-matrix.json").write_text(
        json.dumps(
            {
                "summary": {
                    "matches_total": 300,
                    "matches_full_2plus_coverage": 13,
                    "matches_with_2plus_exact_odds_sources": 17,
                    "matches_with_2plus_core_contexts": 71,
                }
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / "latest-autonomous-accumulation-report.json").write_text(
        json.dumps({"public_safe": 0, "shadow_blocked": 4}),
        encoding="utf-8",
    )
    (tmp_path / "latest-autonomous-prediction-ledger.json").write_text(
        json.dumps([{"candidate_id": "a"}, {"candidate_id": "b"}]),
        encoding="utf-8",
    )

    rendered = report._insert_autonomous_section("Отчёт\n\n🔗 GitHub Actions\n• Run ID: 1")

    assert "🧠 Автономное накопление" in rendered
    assert "13/300" in rendered
    assert "Prediction ledger: 2 строк" in rendered
    assert rendered.index("🧠 Автономное накопление") < rendered.index("🔗 GitHub Actions")
