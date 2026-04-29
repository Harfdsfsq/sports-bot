from __future__ import annotations

import importlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_POLICY_VERSION = 'v2-unified-daily-best5-no-hard-cap'


def run_python(path: str) -> tuple[int, str, str]:
    env = os.environ.copy()
    env.setdefault('APP_TIMEZONE', 'Europe/Moscow')
    env.setdefault('TZ', 'Europe/Moscow')
    env.setdefault('DAILY_BEST5_TARGET_PICKS', '5')
    env.setdefault('DAILY_TOP5_NO_HARD_CAP', 'true')
    env.setdefault('DAILY_TOP5_HARD_CAP_PICKS', '999')
    env.setdefault('GITHUB_EVENT_NAME', 'workflow_dispatch')
    proc = subprocess.run(
        [sys.executable, path],
        cwd=str(ROOT),
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
    )
    return proc.returncode, proc.stdout, proc.stderr


def assert_ok(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    sys.path.insert(0, str(ROOT))
    module = importlib.import_module('scripts.apply_daily_best5_governor')
    assert_ok(hasattr(module, 'main'), 'apply_daily_best5_governor.main is missing')

    checks = [
        'scripts/apply_daily_best5_governor.py',
        'scripts/apply_volume_policy.py',
        'scripts/apply_daily_top5_publish_policy.py',
    ]
    results: dict[str, dict[str, object]] = {}
    for script in checks:
        code, stdout, stderr = run_python(script)
        results[script] = {
            'returncode': code,
            'stdout_tail': stdout[-1000:],
            'stderr_tail': stderr[-1000:],
        }
        assert_ok(code == 0, f'{script} failed with exit code {code}: {stderr[-500:]}')

    export_path = ROOT / '.data' / 'exports' / 'latest-daily-best5-governor.json'
    assert_ok(export_path.exists(), 'latest-daily-best5-governor.json was not created')
    payload = json.loads(export_path.read_text(encoding='utf-8'))
    assert_ok(payload.get('status') == 'ok', 'governor export status is not ok')
    assert_ok(payload.get('policy_version') == EXPECTED_POLICY_VERSION, 'unexpected governor version')
    assert_ok(payload.get('no_daily_hard_cap') is True, 'no_daily_hard_cap must be true in smoke mode')
    env = payload.get('applied_env') or {}
    assert_ok(env.get('DAILY_BEST5_GOVERNOR_ACTIVE') == 'true', 'DAILY_BEST5_GOVERNOR_ACTIVE is not true')
    assert_ok(env.get('DAILY_TOP5_NO_HARD_CAP') == 'true', 'DAILY_TOP5_NO_HARD_CAP is not true')
    assert_ok(env.get('VOLUME_DAILY_HARD_CAP_PICKS') == '999', 'VOLUME_DAILY_HARD_CAP_PICKS must be 999')
    assert_ok(env.get('CONTROLLED_FALLBACK_ENABLED') == 'true', 'CONTROLLED_FALLBACK_ENABLED is not true')
    assert_ok(int(env.get('MAX_PICKS_PER_RUN') or 0) >= 1, 'MAX_PICKS_PER_RUN must be at least 1')

    report = {
        'status': 'ok',
        'expected_policy_version': EXPECTED_POLICY_VERSION,
        'checked_scripts': checks,
        'stage': payload.get('stage'),
        'no_daily_hard_cap': payload.get('no_daily_hard_cap'),
        'target_picks': payload.get('target_picks'),
        'existing_today_picks': payload.get('existing_today_picks'),
        'allowed_this_run': payload.get('allowed_this_run'),
        'results': results,
    }
    out = ROOT / '.data' / 'exports' / 'latest-daily-best5-governor-smoke.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
