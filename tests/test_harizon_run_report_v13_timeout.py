from __future__ import annotations

from pathlib import Path

from scripts import send_harizon_telegram_run_report_v13 as mod
from scripts import send_harizon_telegram_run_report_v14 as v14


def test_timeout_report_does_not_present_zero_matrix_as_measurement(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mod, "EXPORT_DIR", tmp_path)
    (tmp_path / "latest-run-bot-step-status.json").write_text(
        "run bot failed or timed out with status 124\n",
        encoding="utf-8",
    )
    (tmp_path / "latest-autonomous-accumulation-report.json").write_text(
        '{"install": {"status": "installed"}}\n',
        encoding="utf-8",
    )

    status = mod._main_run_timeout_status()
    text = mod._insert_autonomous_section("header\n🔗 GitHub Actions", status)

    assert "не обновлены в этом run" in text
    assert "не являются измерением покрытия" in text
    assert "status 124" in text


def test_timeout_truth_marks_stale_candidate_diagnostics(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(mod, "EXPORT_DIR", tmp_path)
    status = "run bot failed or timed out with status 124"
    source = (
        "🟡 Прогнозов нет: текущие кандидаты не прошли финальные guards.\n"
        "• Главная причина: line movement guard снял кандидата\n"
        "\n📦 Инвентарь и покрытие\n"
    )

    text = mod._mark_timeout_truth(source, status)

    assert "Основной prediction run не завершён" in text
    assert "могли быть прочитаны из сохранённых диагностик" in text
    assert "Главная причина отсутствия свежего результата" in text


def test_status_one_is_failure_not_timeout(monkeypatch, tmp_path: Path) -> None:
    implementation = v14._IMPL
    monkeypatch.setattr(implementation, "EXPORT", tmp_path)
    monkeypatch.setattr(implementation, "STEP_STATUS", tmp_path / "latest-run-bot-step-status.json")
    monkeypatch.setattr(implementation, "RUN_LOG", tmp_path / "latest-run-bot.log")
    monkeypatch.setattr(implementation, "DEBUG", tmp_path / "debug-last-run.json")
    monkeypatch.setattr(implementation, "LIFECYCLE", tmp_path / "latest-main-run-lifecycle.json")
    now = v14.datetime.now(v14.UTC)
    (tmp_path / "latest-main-run-lifecycle.json").write_text(
        '{"status":"failed","started_at_utc":"' + now.isoformat() + '"}',
        encoding="utf-8",
    )
    (tmp_path / "latest-run-bot-step-status.json").write_text(
        "run bot failed or timed out with status 1\n",
        encoding="utf-8",
    )
    (tmp_path / "latest-run-bot.log").write_text(
        "Traceback (most recent call last):\n"
        "SyntaxError: source code string cannot contain null bytes\n",
        encoding="utf-8",
    )

    truth = implementation._run_lifecycle_truth(now)

    assert truth["failed"] is True
    assert truth["timed_out"] is False
    assert truth["failure_reason"] == "runner_failed_status_1"
    assert truth["failure_detail"] == "SyntaxError: source code string cannot contain null bytes"
