from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from scripts import send_harizon_telegram_run_report_v13 as report


def _write(path: Path, payload: dict) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def test_repairs_bzzoiro_provider_and_overlap_lines(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(report, "EXPORT", tmp_path)
    _write(
        tmp_path / "latest-sstats-bzzoiro-odds-merge.json",
        {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "after_2plus_sources": 4,
            "bzzoiro": {
                "matches_with_offers": 8,
                "offers_added_to_pool": 32,
                "offers_parsed": 32,
                "v2_primary": {
                    "requests": 4,
                    "odds_best_requests": 4,
                    "odds_best_rows": 39,
                    "offers_from_best": 32,
                    "response_errors": 0,
                },
            },
        },
    )
    text = (
        "• Bzzoiro: direct req 4, v2 req 4; v2 ctx 1; v2 odds 0; "
        "secondary offers 0; overlap odds-api.io 0; ошибок 0.\n"
        "• Bzzoiro overlap bridge: offers 0; match-overlap 0; same-bucket overlap 0.\n"
    )

    repaired = report._repair_bzzoiro_runtime_lines(text)

    assert "batch odds rows 39" in repaired
    assert "matches with offers 8" in repaired
    assert "secondary offers 32" in repaired
    assert "2+ source matches 4" in repaired
    assert "Bzzoiro runtime merge" in repaired
    assert "overlap bridge: offers 0" not in repaired


def test_separates_waiting_rows_from_post_snapshot_drops(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(report, "EXPORT", tmp_path)
    dropped = []
    for index in range(8):
        if index < 5:
            reasons = ["needs_next_cron_line_movement_recheck"]
            lifecycle = "awaiting_next_run"
        elif index == 5:
            reasons = [
                "current_ev_below_floor:1.0<2.9",
                "current_edge_below_floor:0.4<1.4",
                "needs_next_cron_line_movement_recheck",
            ]
            lifecycle = "awaiting_next_run"
        else:
            reasons = [
                "current_ev_below_floor:1.0<2.9",
                "current_edge_below_floor:0.4<1.4",
            ]
            lifecycle = "movement_failed"
        dropped.append({"guard": {"reasons": reasons, "line_movement_lifecycle_status": lifecycle}})
    _write(
        tmp_path / "latest-line-movement-guard-report.json",
        {
            "updated_at_utc": datetime.now(UTC).isoformat(),
            "candidates_dropped": 8,
            "files": [{"dropped_sample": dropped}],
        },
    )
    text = (
        "• Главная причина: кандидаты ждут следующий cron для второго снимка линии (8)\n"
        "• Line guard: увидел 24, оставил 0, отложил 6, снял 2\n"
        "• кандидат ждёт следующий cron для второго снимка линии: 8 (100%)\n"
        "• Есть кандидат по bookmaker-contract, но нужен второй снимок линии. "
        "Ждём следующий регулярный run.\n"
    )

    evidence = report._movement_runtime_evidence()
    repaired = report._repair_movement_runtime_lines(text)

    assert evidence == {
        "dropped_total": 8,
        "waiting_total": 6,
        "movement_only": 5,
        "with_other": 1,
        "removed_after_snapshot": 2,
    }
    assert "второй снимок линии отсутствует у 6" in repaired
    assert "только 5 блокируются исключительно ожиданием" in repaired
    assert "у 1 есть дополнительные EV/edge-блокеры" in repaired
    assert "ещё 2 сняты после имеющегося снимка" in repaired
    assert "ожидание второго снимка линии: 6" in repaired
    assert "после снимка снято 2" in repaired
    assert "Line guard: увидел 24, оставил 0, отложил 6, снял 2" in repaired


def test_stale_sstats_deep_report_is_not_reused_when_current_step_skipped(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(report, "EXPORT", tmp_path)
    _write(
        tmp_path / "latest-runbot-discovery-first-prepare.json",
        {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "steps": [
                {
                    "name": "apply_sstats_deep_inventory_enrichment_v4",
                    "status": "skipped",
                    "reason": "discovery_budget_reserve",
                }
            ],
        },
    )
    _write(
        tmp_path / "latest-sstats-deep-inventory-enrichment.json",
        {"created_at_utc": "2026-07-23T19:08:18+00:00", "enriched_matches": 48},
    )
    text = (
        "• SStats: запросы 31; сырых строк 26487; контекстов 30; "
        "deep-enriched 48; team-form 0; direct 0; ошибок 0.\n"
    )
    payload = {"api": {"sstats": {"deep_enriched": 48}}}

    evidence = report._sstats_deep_runtime_evidence()
    report._normalize_sstats_payload(payload)
    repaired = report._repair_sstats_runtime_line(text)

    assert evidence == {"deep_enriched": 0, "status": "current_run_skipped"}
    assert payload["api"]["sstats"]["deep_enriched"] == 0
    assert payload["api"]["sstats"]["deep_status"] == "current_run_skipped"
    assert "deep-enriched 0 (текущий deep-step пропущен)" in repaired
    assert "deep-enriched 48" not in repaired


def test_fresh_successful_sstats_deep_report_is_preserved(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(report, "EXPORT", tmp_path)
    now = datetime.now(UTC).isoformat()
    _write(
        tmp_path / "latest-runbot-discovery-first-prepare.json",
        {
            "created_at_utc": now,
            "steps": [{"name": "apply_sstats_deep_inventory_enrichment_v4", "status": "ok"}],
        },
    )
    _write(
        tmp_path / "latest-sstats-deep-inventory-enrichment.json",
        {"created_at_utc": now, "enriched_matches": 17},
    )

    assert report._sstats_deep_runtime_evidence() == {"deep_enriched": 17, "status": "fresh"}
