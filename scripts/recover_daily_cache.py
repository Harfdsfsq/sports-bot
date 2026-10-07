"""Merge the last intact ledger with current data after failed-run cache loss."""
from __future__ import annotations

import shutil
from pathlib import Path

from app.services.daily_match_registry import read_json, write_json

MARKER = Path('.data/daily_quality/recovered-37669273643.json')
BACKUP = Path('.daily-cache-recovery-backup')


def merge_state(old, current):
    old = dict(old)
    bets = {r['fingerprint']: r for r in old.get('bets', [])}
    bank = dict(old['bankroll'])
    for row in current.get('bets', []):
        fp = row['fingerprint']
        previous = bets.get(fp)
        if previous:
            # Preserve confirmed settlements; this incident's duplicate is pending.
            if previous.get('status') != 'pending' or row.get('status') == 'pending':
                continue
            raise ValueError('Conflicting settlement needs manual reconciliation')
        bets[fp] = row
        if row.get('telegram_sent'):
            stake = float(row.get('stake_amount') or 0)
            bank['total_staked'] = round(bank.get('total_staked', 0) + stake, 2)
            bank['bets_published'] = bank.get('bets_published', 0) + 1
            if row.get('status') != 'pending':
                raise ValueError('New settled row needs manual reconciliation')
            bank['open_exposure'] = round(bank.get('open_exposure', 0) + stake, 2)
    old['bets'], old['bankroll'] = list(bets.values()), bank
    old['published_candidates'] = [r for r in bets.values() if r.get('telegram_sent')]
    return old


def finish():
    current_state = read_json(BACKUP / 'state.json', {})
    old_state = read_json('.data/state.json', {})
    if not old_state.get('bets') or not old_state.get('bankroll'):
        raise RuntimeError('Intact recovery cache unavailable; publication stopped to protect ledger')
    state = merge_state(old_state, current_state)
    old_registry = read_json('.data/daily_quality/registry.json', {})
    current_registry = read_json(BACKUP / 'daily_quality/registry.json', {})
    for key, row in old_registry.get('matches', {}).items():
        target = current_registry.setdefault('matches', {}).setdefault(key, row)
        if row.get('publication'):
            target['publication'] = row['publication']
    if old_registry.get('date_local') == current_registry.get('date_local'):
        published = {r['match_id']: r for r in old_registry.get('published', [])}
        for row in current_registry.get('published', []):
            published.setdefault(row['match_id'], row)
        current_registry['published'] = list(published.values())
    # Keep current inventory, API budget and provider data over the old cache.
    shutil.copytree(BACKUP, '.data', dirs_exist_ok=True)
    write_json('.data/state.json', state)
    write_json('.data/daily_quality/registry.json', current_registry)
    write_json(MARKER, {'restored_run': 37669273643, 'bets': len(state['bets'])})
    shutil.rmtree(BACKUP)


if __name__ == '__main__':
    import sys
    if sys.argv[1] == 'prepare':
        if not MARKER.exists():
            shutil.copytree('.data', BACKUP, dirs_exist_ok=True)
            with Path(__import__('os').environ['GITHUB_ENV']).open('a') as target:
                target.write('RECOVER_DAILY_CACHE=true\n')
    else:
        finish()
