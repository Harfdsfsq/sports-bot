"""Canonical regulation-time results and a prospective, non-financial challenger.

No provider forecasts, odds or post-kickoff observations enter model fitting.
The candidate model never changes live probabilities, stakes or admission.
"""
from __future__ import annotations

import hashlib
import math
import logging
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.daily_match_registry import parse_time, read_json, write_json
from app.providers.sstats import form_team_key
from app.services.fixture_identity import same_team_identity

VERSION = 'opponent-poisson-research-v1'


def count(value):
    if isinstance(value, bool):
        return None
    try:
        n = float(value)
        return int(n) if math.isfinite(n) and 0 <= n <= 30 and n.is_integer() else None
    except (TypeError, ValueError):
        return None


def result_row(league, home, away, kickoff, hg, ag, source, event_id=None):
    dt = parse_time(kickoff)
    h, a = form_team_key(str(home or '')), form_team_key(str(away or ''))
    hg, ag = count(hg), count(ag)
    if not league or not h or not a or h == a or dt is None or hg is None or ag is None:
        return None
    # Identity preserves orientation and time; contradictory providers cannot vote
    # the truth into existence. League labels are metadata, not separate matches.
    identity = '|'.join((h, a, dt.isoformat()))
    return {'id': hashlib.sha256(identity.encode()).hexdigest()[:24],
            'league': str(league), 'home': h, 'away': a, 'kickoff': dt.isoformat(),
            'hg': hg, 'ag': ag, 'sources': [source],
            'provider_ids': {source: str(event_id)} if event_id is not None else {}}


def normalize_result(provider, row, league=None):
    """Provider-specific explicit final status and 90-minute score only."""
    if provider == 'football_data':
        if row.get('status') != 'FINISHED':
            return None
        score = row.get('score') or {}
        duration = score.get('duration')
        ft = score.get('regularTime') if duration in {'EXTRA_TIME', 'PENALTY_SHOOTOUT'} else score.get('fullTime')
        if duration not in {'REGULAR', 'EXTRA_TIME', 'PENALTY_SHOOTOUT'} or not isinstance(ft, dict):
            return None
        comp = row.get('competition') or {}
        league = league or comp.get('code') or comp.get('name')
        return result_row(league, (row.get('homeTeam') or {}).get('name'),
                          (row.get('awayTeam') or {}).get('name'), row.get('utcDate'),
                          ft.get('home'), ft.get('away'), provider, row.get('id'))
    if provider == 'openligadb':
        if row.get('matchIsFinished') is not True:
            return None
        ft = next((x for x in row.get('matchResults') or []
                   if x.get('resultTypeKind') == 'After90Minutes'
                   or x.get('resultTypeID') == 2 and not x.get('resultTypeKind')), None)
        if not ft:
            return None
        return result_row(league or row.get('leagueName'), (row.get('team1') or {}).get('teamName'),
                          (row.get('team2') or {}).get('teamName'), row.get('matchDateTimeUTC'),
                          ft.get('pointsTeam1'), ft.get('pointsTeam2'), provider, row.get('matchID'))
    if provider == 'sstats':
        status = str(row.get('statusName') or '').lower().replace(' ', '')
        if status not in {'finished', 'afterextratime', 'afterpenalties', 'ended', 'ft'}:
            return None
        # /Games/list homeResult includes ET; never silently substitute it.
        season = row.get('season') or {}
        comp = season.get('league') or {}
        return result_row(league or ('sstats:' + str(comp['id']) if comp.get('id') is not None else None), (row.get('homeTeam') or {}).get('name'),
                          (row.get('awayTeam') or {}).get('name'), row.get('date'),
                          row.get('homeFTResult'), row.get('awayFTResult'), provider, row.get('id'))
    return None


class GoalHistory:
    def __init__(self, path=Path('.data/daily_quality/goal-history.json')):
        self.path = Path(path)
        self.data = read_json(self.path, {'version': 1, 'results': {}, 'forecasts': {}})
        self.data.setdefault('results', {})
        self.data.setdefault('forecasts', {})
        self.data.setdefault('fixtures', {})
        self.data.setdefault('fixture_ids', {})

    def ingest(self, provider, rows, now, league=None):
        added = 0
        by_pair = {}
        for old in self.data['results'].values():
            by_pair.setdefault((old['home'], old['away']), []).append(old)
        for raw in rows:
            if not isinstance(raw, dict):
                continue
            if provider == 'sstats':
                comp = (raw.get('season') or {}).get('league') or {}
                dt = parse_time(raw.get('date'))
                h, a = form_team_key(str((raw.get('homeTeam') or {}).get('name') or '')), form_team_key(str((raw.get('awayTeam') or {}).get('name') or ''))
                if dt and h and a and comp.get('id') is not None:
                    key = '|'.join((h, a, dt.isoformat()))
                    self.data['fixtures'][key] = 'sstats:' + str(comp['id'])
                    if raw.get('id') is not None:
                        self.data['fixture_ids']['sstats:' + str(raw['id'])] = {
                            'league': 'sstats:' + str(comp['id']), 'home': h, 'away': a, 'kickoff': dt.isoformat()}
            item = normalize_result(provider, raw, league)
            if not item or parse_time(item['kickoff']) + timedelta(hours=2) > now:
                continue
            near = [r for r in by_pair.get((item['home'], item['away']), [])
                    if abs((parse_time(r['kickoff']) - parse_time(item['kickoff'])).total_seconds()) <= 900]
            old = self.data['results'].get(item['id']) or (near[0] if len(near) == 1 else None)
            if old:
                if (old['hg'], old['ag']) != (item['hg'], item['ag']):
                    old['conflict'] = True
                    old.setdefault('conflicting_sources', [])
                    if provider not in old['conflicting_sources']:
                        old['conflicting_sources'].append(provider)
                old['sources'] = sorted(set(old['sources'] + item['sources']))
                old['leagues'] = sorted(set(old.get('leagues', [old['league']]) + [item['league']]))
                old['provider_ids'].update(item['provider_ids'])
            else:
                item['observed_at'] = now.isoformat()
                self.data['results'][item['id']] = item
                by_pair.setdefault((item['home'], item['away']), []).append(item)
                added += 1
        self.save()
        return added

    def save(self):
        write_json(self.path, self.data)

    def eligible(self, league, cutoff, *, prospective=False):
        return [r for r in self.data['results'].values()
                if not r.get('conflict') and league in r.get('leagues', [r['league']])
                and cutoff - timedelta(days=550) <= parse_time(r['kickoff']) < cutoff - timedelta(hours=3)
                and (not prospective or parse_time(r['observed_at']) <= cutoff)]

    def run_research(self, matches, now):
        forecasts = self.data['forecasts']
        new, skipped = 0, Counter()
        models = {}
        for match in matches:
            if not timedelta(minutes=30) <= match.commence_time - now <= timedelta(hours=4):
                continue
            fixture = '|'.join((form_team_key(match.home_team), form_team_key(match.away_team), match.commence_time.isoformat()))
            league = self.data['fixtures'].get(fixture, match.league_name)
            metadata = getattr(match, 'metadata', {}) or {}
            provider_ids = metadata.get('provider_source_ids') or {}
            sid = provider_ids.get('sstats') or metadata.get('sstats_game_id') or metadata.get('sstats_id')
            binding = self.data['fixture_ids'].get('sstats:' + str(sid))
            if (binding and binding['kickoff'] == match.commence_time.isoformat()
                    and same_team_identity(match.home_team, [binding['home']])
                    and same_team_identity(match.away_team, [binding['away']])):
                league = binding['league']
            if league not in models:
                models[league] = fit_model(self.eligible(league, now, prospective=True), now)
            model = models[league]
            forecast = predict(model, match.home_team, match.away_team)
            if not forecast:
                skipped['insufficient_league_or_team_history'] += 1
                continue
            key = '|'.join((VERSION, form_team_key(match.home_team), form_team_key(match.away_team), match.commence_time.isoformat()))
            if key in forecasts:
                continue
            home, away = forecast
            forecasts[key] = {'version': VERSION, 'home': resolve_team(model, match.home_team),
                              'away': resolve_team(model, match.away_team), 'kickoff': match.commence_time.isoformat(),
                              'created_at': now.isoformat(), 'expected_home': home, 'expected_away': away,
                              'p_over25': over25(home + away), 'baseline_over25': over25(model['league_total']),
                              'training_count': model['count'], 'status': 'pending'}
            new += 1
        # Scores are looked up by names and kickoff; never infer results from odds.
        outcomes = {(r['home'], r['away'], r['kickoff']): r for r in self.data['results'].values() if not r.get('conflict')}
        for f in forecasts.values():
            r = outcomes.get((f['home'], f['away'], f['kickoff']))
            if r and parse_time(r['observed_at']) > parse_time(f['created_at']):
                f.update(status='resolved', total=r['hg'] + r['ag'])
            elif f.get('status') == 'resolved':
                f['status'] = 'pending'  # A subsequently disputed result is not evidence.
                f.pop('total', None)
        resolved = [f for f in forecasts.values() if f['version'] == VERSION and f['status'] == 'resolved']
        self.save()
        metrics = score_forecasts(resolved)
        return {'version': VERSION, 'mode': 'research_only', 'history_matches': len(self.data['results']),
                'history_conflicts': sum(bool(r.get('conflict')) for r in self.data['results'].values()),
                'new_forecasts': new, 'pending': sum(f['status'] == 'pending' for f in forecasts.values()),
                'resolved': len(resolved), 'skips': dict(skipped), **metrics}


def over25(rate):
    return 1 - math.exp(-rate) * (1 + rate + rate * rate / 2)


def fit_model(rows, cutoff, min_matches=30):
    """Regularized, time-decayed Poisson attack/defence fit; no extra dependency."""
    rows = sorted((r for r in rows if parse_time(r['kickoff']) < cutoff - timedelta(hours=3)), key=lambda r: r['kickoff'])
    if len(rows) < min_matches:
        return None
    counts = Counter(t for r in rows for t in (r['home'], r['away']))
    graph = {t: set() for t in counts}
    for r in rows:
        graph[r['home']].add(r['away'])
        graph[r['away']].add(r['home'])
    components = {}
    for t in sorted(graph):
        if t in components:
            continue
        stack = [t]
        while stack:
            node = stack.pop()
            if node not in components:
                components[node] = t
                stack.extend(graph[node] - components.keys())
    teams = sorted(counts)
    index = {t: i for i, t in enumerate(teams)}
    size = len(teams)
    # Intercept, home advantage, team attack and opponent defensive vulnerability.
    samples = []
    for r in rows:
        w = math.exp(-math.log(2) * max(0., (cutoff - parse_time(r['kickoff'])).days) / 180.)
        h, a = index[r['home']], index[r['away']]
        samples.extend([([0, 1, 2 + h, 2 + size + a], r['hg'], w),
                        ([0, 2 + a, 2 + size + h], r['ag'], w)])
    mean = sum(y * w for _, y, w in samples) / sum(w for _, _, w in samples)
    beta = [0.] * (2 + 2 * size)
    beta[0], beta[1] = math.log(max(.2, mean)), math.log(1.12)
    priors = beta.copy()
    columns = [[i for i, (cols, _, _) in enumerate(samples) if j in cols] for j in range(len(beta))]
    eta = [sum(beta[j] for j in cols) for cols, _, _ in samples]
    for _ in range(60):
        largest = 0.
        for j, sample_ids in enumerate(columns):
            penalty = .01 if j == 0 else 8.
            grad, curvature = -penalty * (beta[j] - priors[j]), penalty
            for i in sample_ids:
                _, y, w = samples[i]
                mu = math.exp(max(-8., min(5., eta[i])))
                grad += w * (y - mu)
                curvature += w * mu
            step = max(-.5, min(.5, grad / curvature))
            beta[j] += step
            for i in sample_ids:
                eta[i] += step
            largest = max(largest, abs(step))
        if largest < .0001:
            break
    return {'beta': beta, 'index': index, 'counts': counts, 'components': components, 'size': size, 'count': len(rows),
            'league_total': sum((r['hg'] + r['ag']) for r in rows) / len(rows)}


def predict(model, home, away):
    if model is None:
        return None
    h, a = resolve_team(model, home), resolve_team(model, away)
    if min(model['counts'].get(h, 0), model['counts'].get(a, 0)) < 6:
        return None
    if model['components'][h] != model['components'][a]:
        return None
    h, a = model['index'][h], model['index'][a]
    b, n = model['beta'], model['size']
    values = (math.exp(b[0] + b[1] + b[2 + h] + b[2 + n + a]),
              math.exp(b[0] + b[2 + a] + b[2 + n + h]))
    return values if all(math.isfinite(x) and .05 <= x <= 8 for x in values) else None


def resolve_team(model, name):
    key = form_team_key(name)
    if key in model['index']:
        return key
    matches = [t for t in model['index'] if same_team_identity(name, [t])]
    return matches[0] if len(matches) == 1 else None


def score_forecasts(rows):
    if not rows:
        return {'brier': None, 'baseline_brier': None, 'log_loss': None}
    def brier(key):
        return sum((r[key] - int(r['total'] > 2.5)) ** 2 for r in rows) / len(rows)
    loss = 0.
    for r in rows:
        p = min(1 - 1e-9, max(1e-9, r['p_over25']))
        loss -= math.log(p if r['total'] > 2.5 else 1 - p)
    return {'brier': brier('p_over25'), 'baseline_brier': brier('baseline_over25'), 'log_loss': loss / len(rows)}


def archive_results(provider, rows, league=None):
    """Research storage failures must not disable a sporting data provider."""
    try:
        added = GoalHistory().ingest(provider, rows, datetime.now(UTC), league)
        return {'status': 'ok', 'added': added}
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        logging.getLogger(__name__).warning('Historical archive unavailable: %s', type(exc).__name__)
        return {'status': 'error', 'error_type': type(exc).__name__}
