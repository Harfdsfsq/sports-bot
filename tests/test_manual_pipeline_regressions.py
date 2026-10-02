import importlib
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from app.services import daily_coverage_full_inventory_provider_patch as scope
from app.services import daily_coverage_plan as plan
from app.services.focused_alpha import enabled
from app.services.publication_thresholds import publish_min_books, publish_min_context_sources, publish_min_odds_sources
from scripts import run_manual_fallback as fallback
from scripts import send_pipeline_run_report as report

UTC = UTC

@pytest.mark.parametrize('profile,allow,expected', [('strict', 'true', 2), ('rules_ab', 'false', 2), ('rules_ab', 'true', 1)])
def test_profile_source_floor(monkeypatch, profile, allow, expected):
    monkeypatch.setenv('PUBLICATION_PROFILE', profile)
    monkeypatch.setenv('PUBLISH_ALLOW_B_TIER', allow)
    monkeypatch.setenv('PUBLISH_COVERAGE_TIER_MODE', 'a_or_b')
    monkeypatch.setenv('PUBLISH_MIN_ODDS_SOURCES', '1')
    monkeypatch.setenv('PUBLISH_MIN_CONTEXT_SOURCES', '1')
    monkeypatch.setenv('PUBLISH_MIN_BOOKS', '1')
    assert publish_min_odds_sources() == expected
    assert publish_min_context_sources() == expected
    assert publish_min_books() == 2


def test_disabled_focus_ignores_saved_empty_cohort(monkeypatch):
    monkeypatch.setenv('FOCUSED_ALPHA_RUNTIME_POLICY_ENABLED', 'false')
    monkeypatch.setenv('FOCUSED_ALPHA_ENABLED', 'true')
    monkeypatch.setattr(scope, 'load_plan', lambda: {'focused_alpha': {'enabled': True}, 'fixed_300_provider_target': False, 'target_match_keys': []})
    matches = [object(), object()]
    assert enabled() is False
    assert scope._focused_model_scope(SimpleNamespace(), matches) == (matches, False)


def test_old_run_plan_is_ignored(monkeypatch):
    monkeypatch.setenv('GITHUB_RUN_ID', 'current')
    monkeypatch.setattr(plan, 'load', lambda *args: {'run_id': 'old', 'assignments': {'bzzoiro': []}})
    assert plan.load_plan() == {}
    monkeypatch.setattr(plan, 'load', lambda *args: {'run_id': 'current'})
    assert plan.load_plan() == {'run_id': 'current'}


def write_summary(tmp_path, started, **counts):
    directory = tmp_path / '.logs'
    directory.mkdir(exist_ok=True)
    (directory / 'debug-last-run.json').write_text(json.dumps({'summary': {'started_time_utc': started.isoformat(), **counts}}))


def test_dry_report_never_sends_and_does_not_count_service_messages(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    write_summary(tmp_path, now, published_to_telegram=0, telegram_messages_sent=1)
    monkeypatch.setenv('RUN_STARTED_AT_UTC', now.isoformat())
    monkeypatch.setenv('PUBLISH_DRY_RUN', 'true')
    monkeypatch.setattr(report, 'send_telegram', lambda *args: pytest.fail('Dry run attempted Telegram'))
    assert report.main(tmp_path) == 0
    text = (tmp_path / '.data/exports/latest-manual-run-report.txt').read_text()
    assert 'Прогнозы Telegram из pipeline: 0' in text


def test_stale_summary_cannot_start_reserve(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    write_summary(tmp_path, now - timedelta(hours=1))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RUN_STARTED_AT_UTC', now.isoformat())
    monkeypatch.setattr(fallback.subprocess, 'call', lambda *args: pytest.fail('Stale reserve invoked'))
    assert report.fresh_summary(tmp_path, now) == {}
    assert fallback.main() == 1


def test_primary_publication_prevents_extra_reserve(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    write_summary(tmp_path, now, published_to_telegram=2)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('RUN_STARTED_AT_UTC', now.isoformat())
    monkeypatch.setenv('PUBLISH_DRY_RUN', 'false')
    monkeypatch.setattr(fallback.subprocess, 'call', lambda *args: pytest.fail('Extra publication'))
    assert fallback.main() == 0


def test_live_report_failure_is_visible(tmp_path, monkeypatch):
    now = datetime.now(UTC)
    write_summary(tmp_path, now, published_to_telegram=0)
    monkeypatch.setenv('RUN_STARTED_AT_UTC', now.isoformat())
    monkeypatch.setenv('PUBLISH_DRY_RUN', 'false')
    monkeypatch.setattr(report, 'send_telegram', lambda *args: False)
    assert report.main(tmp_path) == 1


def test_legacy_report_entrypoint_imports():
    module = importlib.import_module('scripts.send_harizon_telegram_run_report_v10')
    assert callable(module.main)
