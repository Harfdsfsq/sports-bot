from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient


@pytest.mark.parametrize("problem", ["missing", "wrong", "long"])
def test_run_auth_rejects_missing_and_wrong_tokens_before_runner(monkeypatch, problem):
    from app.main import app

    monkeypatch.setenv("ADMIN_RUN_TOKEN", "test-admin-secret")
    headers = {} if problem == "missing" else {"X-Admin-Token": "wrong" if problem == "wrong" else "x" * 1000}
    assert TestClient(app).post("/run", headers=headers).status_code == 401


def test_authorized_http_run_uses_native_admission_for_daily_profile(monkeypatch):
    from app.main import app

    monkeypatch.setenv("ADMIN_RUN_TOKEN", "test-admin-secret")
    monkeypatch.setenv("PUBLICATION_PROFILE", "daily_quality")

    class Native:
        def __init__(self, settings):
            pass

        async def run_once(self):
            return {"tested_native_admission": True}

    monkeypatch.setattr("app.services.daily_quality_runner.DailyQualityRunner", Native)
    result = TestClient(app).post("/run", headers={"X-Admin-Token": "test-admin-secret"})
    assert result.status_code == 200
    assert result.json()["summary"]["tested_native_admission"] is True


@pytest.mark.parametrize("old", [False, True])
def test_self_history_parses_iso_times_and_excludes_old_archives(tmp_path, monkeypatch, old):
    import json

    from app.services import runner as module

    now = datetime.now(UTC)
    path = tmp_path / "run.json"
    path.write_text(
        json.dumps(
            {
                "created_at": (now - timedelta(days=90 if old else 1)).isoformat(),
                "provider_diagnostics": {
                    "matches": [
                        {
                            "commence_time": (now - timedelta(days=1)).isoformat(),
                            "home_team": "Arsenal",
                            "away_team": "Chelsea",
                            "league_name": "Premier League",
                            "contexts": [{"expected_home": 1.4, "expected_away": 1.1}],
                        }
                    ]
                },
            }
        )
    )
    monkeypatch.setattr(module, "resolve_run_history_roots", lambda _: [])
    monkeypatch.setattr(module, "collect_run_archive_paths", lambda *a, **k: [path])
    r = module.PredictionRunner.__new__(module.PredictionRunner)
    r.settings = SimpleNamespace(self_history_context_enabled=True, self_history_context_include_state=False)
    r._best_self_history_archive_context = lambda rows: rows[0]
    _, stats, _ = r._build_self_history_contexts([], now)
    assert stats["history_matches_used"] == (0 if old else 1)


def test_v14_entry_point_uses_supported_base_report(monkeypatch):
    from scripts import send_harizon_telegram_run_report_v14 as report

    called = []
    base = SimpleNamespace(build_payload=lambda: {}, main=lambda: called.append("main") or 0)
    monkeypatch.setattr(report.v13, "_refresh_truth", lambda: None)
    monkeypatch.setattr(report.v13, "_load_v5", lambda: base)
    monkeypatch.setattr("scripts.repair_synthetic_publication_timestamps.repair_exports", lambda: None)
    assert report.main() == 0
    assert called == ["main"]


def test_market_implied_xg_is_never_independent_confirmation(monkeypatch):
    from scripts.publish_controlled_fallback import candidate_metrics, hard_reject_reasons, xg_sanity_metrics

    monkeypatch.setenv("CONTROLLED_FALLBACK_XG_SANITY_ENABLED", "true")
    candidate = {
        "family": "totals",
        "selection": "Over",
        "point": 2.5,
        "expected_home": 1.5,
        "expected_away": 1.5,
        "source_summary": {"xg": {"source": "market_implied_total_xg"}},
    }
    metrics = xg_sanity_metrics(candidate, 0.6)
    assert metrics["xg_hard_confirmation"] is False
    assert metrics["xg_direction_evaluated"] is False
    assert "market_derived_xg_not_independent" in hard_reject_reasons(candidate, candidate_metrics(candidate), {})
