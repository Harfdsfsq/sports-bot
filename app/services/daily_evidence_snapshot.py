"""Compact observation provenance for ablations; no untrained feature bonuses."""
from __future__ import annotations

import hashlib
import json
from app.services.daily_match_registry import parse_time


def snapshot(context, support, weight):
    payload = context.payload or {}
    histories = {}
    for side in ('home', 'away'):
        rows = payload.get(side+'_recent') or payload.get('recent_'+side) or []
        rows = [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
        observations = []
        for r in rows:
            observed = {k: str(r[k]) for k in ('id', 'start', 'date', 'home', 'opponent', 'goals_for', 'goals_against') if k in r}
            if observed:
                observations.append(observed)
        weights = [1/(i+1)**.6 for i in range(len(observations))]
        dates = [str(r.get('start') or r.get('date')) for r in observations
                 if parse_time(r.get('start') or r.get('date'))]
        histories[side] = {'observations': observations, 'count': len(observations),
                           'latest_match_at': max(dates, key=parse_time) if dates else None,
                           'rank_weight_effective_count': sum(weights)**2/sum(w*w for w in weights) if weights else None,
                           'signature': hashlib.sha256(json.dumps(observations, sort_keys=True).encode()).hexdigest() if observations else None}
    # Do not claim shot-level xG merely because the provider has expected_home.
    return {'source': context.source, 'sample': support, 'blend_weight': weight,
            'expected_home': context.expected_home, 'expected_away': context.expected_away,
            'observed_at': context.details.get('daily_evidence_observed_at'),
            'histories': histories, 'shot_xg_verified': False,
            'projection_basis': 'provider_projection_or_score_history'}
