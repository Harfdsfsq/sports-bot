"""One-time import from an explicitly pinned, trusted full-run artifact.

Refuse to replace any existing financial position. Preserve new research records.
"""
from __future__ import annotations
import json
import os
import shutil
from pathlib import Path
from app.services.daily_match_registry import read_json, write_json

SOURCE_RUN = 42803945613
MARKER = Path('.data/daily_quality/branch-migration.json')


def merge_history(old, current):
    result = dict(old)
    for key, value in current.items():
        if isinstance(value, dict):
            merged = dict(result.get(key) or {})
            for identity, row in value.items():
                if identity not in merged:
                    merged[identity] = row
            result[key] = merged
    trials = {row['id']: row for row in old.get('priced_totals_trials', [])}
    for row in current.get('priced_totals_trials', []):
        trials.setdefault(row['id'], row)
    result['priced_totals_trials'] = list(trials.values())
    return result


def finish(source, destination=Path('.data')):
    source, destination = Path(source), Path(destination)
    if read_json(source / 'exports/latest-run-summary.json', {}).get('dry_run') is True:
        raise RuntimeError('Source artifact is dry-run state; refusing live migration')
    old = read_json(source / 'state.json', {})
    current = read_json(destination / 'state.json', {})
    if not old.get('bets') or not old.get('bankroll'):
        raise RuntimeError('Source financial history missing; refusing live initialization')
    if current.get('bets'):
        raise RuntimeError('Destination has financial positions; manual reconciliation required')
    if any(read_json(destination / 'daily_quality/registry.json', {}).get('published', [])):
        raise RuntimeError('Destination has acknowledged publications; refusing replacement')
    # Keep newly observed paper bets and run/message diagnostics, never new money.
    for field, identity in [('shadow_bets', 'fingerprint'), ('run_history', 'created_at'),
                            ('message_history', 'created_at')]:
        records = {}
        for row in old.get(field, []) + current.get(field, []):
            key = row.get(identity) or json.dumps(row, sort_keys=True)
            records.setdefault(key, row)
        old[field] = list(records.values())
    history = merge_history(read_json(source / 'daily_quality/goal-history.json', {}),
                            read_json(destination / 'daily_quality/goal-history.json', {}))
    if not history.get('priced_totals_trials'):
        raise RuntimeError('Source research history missing')
    backup = destination / 'migration-backup'
    backup.mkdir(parents=True, exist_ok=True)
    for name in ['state.json', 'daily_quality/goal-history.json']:
        path = destination / name
        if path.exists():
            target = backup / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    # Import acknowledged publication history before permitting another live run.
    registry = read_json(source / 'daily_quality/registry.json', {})
    if not registry.get('matches'):
        raise RuntimeError('Source registry missing')
    # Combine independent fork usage conservatively; never reset shared quotas.
    budget = read_json(source / 'daily_quality/api-budget.json', {'accounts': {}, 'ips': {}})
    local_budget = read_json(destination / 'daily_quality/api-budget.json', {})
    for key, row in local_budget.get('accounts', {}).items():
        if key not in budget.setdefault('accounts', {}):
            budget['accounts'][key] = dict(row)
            continue
        target = budget['accounts'][key]
        for scope in ('day', 'month'):
            if target.get(scope) == row.get(scope):
                target[scope + '_used'] = target.get(scope + '_used', 0) + row.get(scope + '_used', 0)
            elif str(row.get(scope, '')) > str(target.get(scope, '')):
                target[scope], target[scope + '_used'] = row.get(scope), row.get(scope + '_used', 0)
        target['hour_requests'] = sorted(set(target.get('hour_requests', []) + row.get('hour_requests', [])))
        target['blocked_until'] = max(target.get('blocked_until', 0), row.get('blocked_until', 0))
    for key, stamp in local_budget.get('ips', {}).items():
        budget.setdefault('ips', {})[key] = max(budget['ips'].get(key, 0), stamp)
    write_json(destination / 'daily_quality/api-budget.json', budget)
    write_json(destination / 'state.json', old)
    write_json(destination / 'daily_quality/goal-history.json', history)
    write_json(destination / 'daily_quality/registry.json', registry)
    for name in ['published-candidate-index.json', 'fallback-sent-index.json',
                 'candidate-lifecycle-state.json']:
        path = source / name
        if path.exists():
            target = destination / name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
    prices = read_json(source / 'daily_quality/priced-totals-clv.json', {})
    for key, row in read_json(destination / 'daily_quality/priced-totals-clv.json', {}).items():
        prices.setdefault(key, row)
    write_json(destination / 'daily_quality/priced-totals-clv.json', prices)
    write_json(destination / 'daily_quality/branch-migration.json', {
        'source_run': int(os.getenv('MIGRATION_SOURCE_RUN', str(SOURCE_RUN))), 'bets': len(old['bets']),
        'balance': old['bankroll'].get('current_balance'),
        'research_records': len(history['priced_totals_trials'])})


if __name__ == '__main__':
    import sys
    if sys.argv[1] == 'prepare':
        needed = not MARKER.exists()
        if not needed and not read_json('.data/state.json', {}).get('bets'):
            raise RuntimeError('Migration marker without financial ledger; refusing empty bank')
        outputs = {'needed': str(needed).lower()}
        if needed:
            import requests
            repo = os.environ['GITHUB_REPOSITORY']
            session = requests.Session()
            session.headers['Authorization'] = 'Bearer ' + os.environ['GH_TOKEN']
            base = 'https://api.github.com/repos/' + repo
            response = session.get(base + '/actions/workflows/run-bot.yml/runs',
                params={'branch': 'feature/daily-quality-pipeline', 'status': 'success', 'per_page': 100}, timeout=30)
            response.raise_for_status()
            for run in response.json()['workflow_runs']:
                if run['head_branch'] != 'feature/daily-quality-pipeline':
                    continue
                # This repair was reviewed against this exact source implementation.
                if run['head_sha'] != '36ce3fb5572d03434bfed352414d38f46ad4ae59':
                    raise RuntimeError('Source implementation changed; migration requires review')
                response = session.get(base + f"/actions/runs/{run['id']}/artifacts", timeout=30)
                response.raise_for_status()
                name = f"run-bot-rules-{run['id']}"
                if any(a['name'] == name and not a['expired'] for a in response.json()['artifacts']):
                    outputs.update(source_run=str(run['id']), source_artifact=name)
                    break
            if 'source_run' not in outputs:
                raise RuntimeError('No verified full source artifact; refusing empty bank')
        with Path(os.environ['GITHUB_OUTPUT']).open('a') as out:
            for key, value in outputs.items():
                out.write(f'{key}={value}\n')
    else:
        finish(Path(sys.argv[2]))
