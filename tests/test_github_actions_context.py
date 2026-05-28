from __future__ import annotations

from app.services.github_actions_context import append_github_run_reference, github_run_context, github_run_reference_text


def test_github_run_context_builds_url(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "123456789")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Harfdsfsq/sports-bot")
    monkeypatch.setenv("GITHUB_SERVER_URL", "https://github.com")
    monkeypatch.setenv("GITHUB_WORKFLOW", "run-bot")
    monkeypatch.setenv("GITHUB_RUN_ATTEMPT", "2")

    ctx = github_run_context()

    assert ctx["enabled"] is True
    assert ctx["run_id"] == "123456789"
    assert ctx["run_url"] == "https://github.com/Harfdsfsq/sports-bot/actions/runs/123456789"
    assert ctx["artifact_name"] == "run-bot-123456789"
    assert "Run ID: 123456789" in github_run_reference_text()


def test_append_github_run_reference_is_idempotent(monkeypatch):
    monkeypatch.setenv("GITHUB_RUN_ID", "555")
    monkeypatch.setenv("GITHUB_REPOSITORY", "Harfdsfsq/sports-bot")

    once = append_github_run_reference("hello")
    twice = append_github_run_reference(once)

    assert once == twice
    assert "Run URL:" in once
