from __future__ import annotations

"""Refresh publication guard state from origin/main before Telegram publishing.

This is intentionally narrow: only the files used by duplicate/open-risk guards
are refreshed. Runtime artifacts from the current run are not touched.
After refresh, fallback Telegram publications are materialized into state.json so
bankroll/open-risk code can see pending controlled fallback bets.
"""

import json
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('.').resolve()
OUT = ROOT / '.data' / 'exports' / 'latest-publication-guard-state-refresh.json'
STATE_FILES = [
    '.data/fallback-sent-index.json',
    '.data/state.json',
]


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(cmd, cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)


def is_json_file(path: Path) -> bool:
    try:
        if not path.exists() or not path.is_file():
            return False
        json.loads(path.read_text(encoding='utf-8'))
        return True
    except Exception:
        return False


def sync_fallback_sent_index_to_state(report: dict[str, object]) -> None:
    script = ROOT / 'scripts' / 'sync_fallback_sent_index_to_state.py'
    if not script.exists():
        report['fallback_state_sync'] = {'status': 'missing'}
        return
    proc = subprocess.run([sys.executable, str(script)], cwd=str(ROOT), text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False, timeout=30)
    report['fallback_state_sync'] = {
        'status': 'ok' if proc.returncode == 0 else 'failed',
        'returncode': proc.returncode,
        'stdout_tail': proc.stdout[-1200:],
        'stderr_tail': proc.stderr[-1200:],
    }


def main() -> int:
    OUT.parent.mkdir(parents=True, exist_ok=True)
    report: dict[str, object] = {
        'status': 'ok',
        'updated_at_utc': datetime.now(timezone.utc).isoformat(),
        'refreshed': [],
        'missing_or_invalid': [],
        'commands': [],
    }

    fetch = run(['git', 'fetch', 'origin', 'main'])
    report['commands'].append({'cmd': 'git fetch origin main', 'returncode': fetch.returncode, 'stderr_tail': fetch.stderr[-500:]})
    if fetch.returncode != 0:
        report['status'] = 'fetch_failed_best_effort'
        sync_fallback_sent_index_to_state(report)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    for rel in STATE_FILES:
        path = ROOT / rel
        backup = ROOT / f'{rel}.before_guard_refresh'
        try:
            if path.exists():
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)
            checkout = run(['git', 'checkout', 'origin/main', '--', rel])
            report['commands'].append({'cmd': f'git checkout origin/main -- {rel}', 'returncode': checkout.returncode, 'stderr_tail': checkout.stderr[-500:]})
            if checkout.returncode == 0 and is_json_file(path):
                report['refreshed'].append(rel)
            else:
                if backup.exists():
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(backup, path)
                report['missing_or_invalid'].append(rel)
        except Exception as exc:
            report.setdefault('errors', []).append({'file': rel, 'error': f'{exc.__class__.__name__}: {exc}'})
            if backup.exists():
                shutil.copy2(backup, path)

    sync_fallback_sent_index_to_state(report)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
