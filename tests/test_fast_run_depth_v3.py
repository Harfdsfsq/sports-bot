from pathlib import Path


def test_fast_workflow_sources_effective_env_before_app_cli() -> None:
    text = Path('.github/workflows/run-bot-fast.yml').read_text(encoding='utf-8')
    assert 'RUN_MODE: "normal"' in text
    assert 'PUBLISH_WINDOW_HOURS: "24"' in text
    assert 'source .data/exports/latest-fast-run-env.sh' in text
    assert 'ODDS_API_IO_ACC2_KEY' in text
    assert 'ODDS_API_IO_SECONDARY_KEY' in text


def test_fast_budget_writes_sourceable_env_file() -> None:
    text = Path('scripts/apply_fast_run_budget.py').read_text(encoding='utf-8')
    assert 'latest-fast-run-env.sh' in text
    assert "'PUBLISH_WINDOW_HOURS': str(window_hours)" in text
    assert "'ODDS_API_IO_BOOKMAKERS_ACCOUNT2': 'Betfair Exchange,Sbobet'" in text
    assert 'balanced_depth_v3_uses_24h_window_and_dual_account_bookmakers' in text
