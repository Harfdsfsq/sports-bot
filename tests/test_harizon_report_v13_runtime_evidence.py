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


def test_repairs_movement_waiting_only_claim(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(report, "EXPORT", tmp_path)
    dropped = []
    for index in range(18):
        reasons = ["needs_next_cron_line_movement_recheck"]
        if index >= 4:
            reasons = [
                "current_ev_below_floor:1.0<2.9",
                "current_edge_below_floor:0.4<1.4",
                "needs_next_cron_line_movement_recheck",
            ]
        dropped.append({"guard": {"reasons": reasons}})
    _write(
        tmp_path / "latest-line-movement-guard-report.json",
        {
            "updated_at_utc": datetime.now(UTC).isoformat(),
            "candidates_dropped": 18,
            "files": [{"dropped_sample": dropped}],
        },
    )
    text = (
        "• Главная причина: кандидаты ждут следующий cron для второго снимка линии (18)\n"
        "• Line guard: увидел 31, оставил 0, отложил 18 до следующего cron\n"
        "• кандидат ждёт следующий cron для второго снимка линии: 18 (100%)\n"
        "• Есть кандидат по bookmaker-contract, но нужен второй снимок линии. "
        "Ждём следующий регулярный run.\n"
    )

    repaired = report._repair_movement_runtime_lines(text)

    assert "только 4 блокируются исключительно ожиданием" in repaired
    assert "у 14 есть дополнительные EV/edge-блокеры" in repaired
    assert "movement-only 4, с другими блокерами 14" in repaired
    assert "также ниже EV/edge 14" in repaired
    assert "У 4 кандидатов единственный текущий стопор" in repaired
