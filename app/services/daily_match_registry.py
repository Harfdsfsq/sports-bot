"""Persistent exact match identities, provider evidence and gap-first work queue."""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.schemas import Match, MatchContext, Offer
from app.utils import canonicalize_team_name

TZ = ZoneInfo('Europe/Moscow')


def parse_time(value):
    try:
        dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return dt.astimezone(UTC) if dt.tzinfo else None
    except (TypeError, ValueError):
        return None


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + '\n')
    temp.replace(path)


def match_identity(match: Match) -> str:
    pair = sorted([canonicalize_team_name(match.home_team), canonicalize_team_name(match.away_team)])
    identity = '|'.join([match.sport_key, *pair, match.commence_time.astimezone(UTC).isoformat()])
    return hashlib.sha256(identity.encode()).hexdigest()[:24]


class DailyMatchRegistry:
    def __init__(self, path: Path, now: datetime):
        self.path, self.now = Path(path), now.astimezone(UTC)
        self.date = now.astimezone(TZ).date().isoformat()
        payload = read_json(path, {})
        # Retain acknowledged-match markers even when the sending day rolls over.
        for publication in payload.get('published', []):
            entry = payload.get('matches', {}).get(publication.get('match_id'))
            if entry is not None:
                entry['publication'] = publication
        if payload.get('date_local') == self.date:
            self.data = payload
        else:
            retained = {key: row for key, row in payload.get('matches', {}).items() if parse_time(row['match']['commence_time']) and parse_time(row['match']['commence_time']).astimezone(TZ).date().isoformat() == self.date}
            self.data = {'date_local': self.date, 'matches': retained, 'published': [], 'issues': []}

    def sync(self, matches: list[Match]):
        entries = self.data['matches']
        for match in sorted(matches, key=lambda m: m.commence_time):
            day = match.commence_time.astimezone(TZ).date().isoformat()
            lookahead = day != self.date
            if lookahead and not (self.now < match.commence_time <= self.now + timedelta(hours=4)):
                continue
            key = match_identity(match)
            day_count = sum(parse_time(row['match']['commence_time']).astimezone(TZ).date().isoformat() == day for row in entries.values())
            if key not in entries and day_count >= (60 if lookahead else 300):
                continue
            ids = match.metadata.get('provider_source_ids') or match.metadata.get('day_inventory_source_ids') or {}
            entry = entries.setdefault(key, {'match': asdict(match), 'provider_ids': {}, 'evidence': {}, 'attempts': {}})
            # Provider IDs belong to this exact team pair and kickoff, not a loose daily key.
            for provider, event_id in ids.items():
                existing = entry['provider_ids'].get(provider)
                if existing and str(existing) != str(event_id):
                    self.data['issues'].append({'kind': 'provider_id_conflict', 'match_id': key, 'provider': provider})
                    continue
                entry['provider_ids'][provider] = event_id
            entry['match']['metadata'].update(match.metadata)
        self.save()

    def matches(self):
        result = []
        for entry in self.data['matches'].values():
            row = dict(entry['match'])
            row['commence_time'] = parse_time(row['commence_time'])
            row['metadata'] = dict(row.get('metadata') or {})
            ids = entry['provider_ids']
            row['metadata'].update({'provider_source_ids': ids, 'day_inventory_source_ids': ids, 'source_ids': ids})
            for provider, event_id in ids.items():
                row['metadata'][provider + '_id'] = event_id
                row['metadata'][provider + '_event_id'] = event_id
            if ids.get('sstats'):
                row['metadata']['sstats_game_id'] = ids['sstats']
            result.append(Match(**row))
        return result

    def cached(self, provider: str, role: str, match: Match):
        evidence = self.data['matches'].get(match_identity(match), {}).get('evidence', {}).get(provider + ':' + role)
        if not evidence:
            return None
        observed = parse_time(evidence['observed_at'])
        ttl = 15 if role == 'offers' else 120 if provider == 'weather' else 360
        if observed is None or not -timedelta(seconds=5) <= self.now - observed <= timedelta(minutes=ttl):
            return None
        try:
            if role == 'offers':
                return [Offer(**row) for row in evidence['payload']]
            context = MatchContext(**evidence['payload'])
            if provider == 'sstats' and context.source == 'sstats' and os.getenv('PUBLICATION_PROFILE', '').lower() == 'daily_quality':
                from app.providers.sstats import market_only_context
                # Also invalidate persisted entries produced before the provenance marker.
                if market_only_context(context):
                    return None
            if provider == 'weather' and os.getenv('PUBLICATION_PROFILE', '').lower() == 'daily_quality' and not context.details.get('weather_location_verified'):
                return None
            if provider != 'weather' and (context.source in {'bzzoiro_event_odds', 'market_implied_xg', 'market_signal'} or context.details.get('bzzoiro_event_only_context')):
                return None
            return context
        except (TypeError, ValueError):
            return None

    def has_role(self, match: Match, role: str):
        providers = {name.split(':')[0] for name in self.data['matches'][match_identity(match)]['evidence'] if name.endswith(':' + role) and (role != 'context' or not name.startswith('weather:'))}
        return any(self.cached(p, role, match) for p in providers)

    def targets(self, provider: str, role: str, *, limit: int = 60):
        ranked = []
        for match in self.matches():
            minutes = (match.commence_time - self.now).total_seconds() / 60
            if minutes < 30:
                continue
            if self.cached(provider, role, match):
                continue
            entry = self.data['matches'][match_identity(match)]
            attempt = entry['attempts'].get(provider + ':' + role, {})
            retry = parse_time(attempt.get('retry_after'))
            if retry and self.now < retry:
                attempted = parse_time(attempt.get('at'))
                urgent_odds_retry = role == 'offers' and minutes <= 120 and attempt.get('result') == 'empty' and attempted is not None and self.now - attempted >= timedelta(minutes=30) and retry - attempted <= timedelta(minutes=120)
                if not urgent_odds_retry:
                    continue
            lane = 0 if minutes <= 240 else 1 if minutes <= 480 else 2
            ranked.append(((lane, self.has_role(match, role), minutes), match))
        ranked.sort(key=lambda item: item[0])
        # Close near window first, then prefetch next and rotate through the daily backlog.
        near = [m for key, m in ranked if key[0] == 0]
        nxt = [m for key, m in ranked if key[0] == 1][:20]
        later = [m for key, m in ranked if key[0] == 2][:20]
        return (near + nxt + later)[:max(0, limit)]

    def record(self, provider, role, targets, data, stats, *, observed_at=None):
        fetched = observed_at or datetime.now(UTC)
        self.now = fetched
        for match in targets:
            entry = self.data['matches'][match_identity(match)]
            value = data.get(match.match_key)
            if value:
                payload = [asdict(offer) for offer in value] if role == 'offers' else asdict(value)
                if role == 'offers':
                    for row in payload:
                        row['metadata'].update({'fetched_at_utc': fetched.isoformat(), 'registry_match_id': match_identity(match)})
                observed = fetched.isoformat()
                if role == 'context':
                    observed = payload.get('details', {}).get('daily_source_observed_at') or observed
                entry['evidence'][provider + ':' + role] = {'observed_at': observed, 'payload': payload}
            # Empty or unavailable endpoints are retried in a later run, not in a tight loop.
            near = 30 <= (match.commence_time - fetched).total_seconds() / 60 <= 120
            delay = 0 if value else 30 if role == 'offers' and near else 120
            unavailable = bool(stats.get('auth_error') or stats.get('plan_restriction'))
            if unavailable:
                delay = 1440
            entry['attempts'][provider + ':' + role] = {'at': fetched.isoformat(), 'retry_after': (fetched + timedelta(minutes=delay)).isoformat(), 'result': 'data' if value else 'unavailable' if unavailable else 'empty'}
        self.save()

    def observation(self, match, role):
        times = []
        entry = self.data['matches'][match_identity(match)]
        for name, row in entry['evidence'].items():
            provider, kind = name.split(':', 1)
            if kind == role and (role != 'context' or provider != 'weather') and self.cached(provider, role, match):
                times.append(row['observed_at'])
        return max(times, default=None)

    def coverage(self):
        from app.providers.sstats import market_only_context
        result = {'inventory': sum(m.commence_time.astimezone(TZ).date().isoformat() == self.date for m in self.matches()), 'line': 0, 'context': 0, 'ready': 0, 'near': 0, 'near_ready': 0, 'form': 0, 'standings': 0, 'weather': 0, 'collected_line': 0, 'collected_context': 0, 'lookahead': 0, 'near_line': 0, 'near_context': 0, 'near_missing_line': 0, 'near_missing_context': 0}
        for match in self.matches():
            line, context = self.has_role(match, 'offers'), self.has_role(match, 'context')
            near = 30 <= (match.commence_time - self.now).total_seconds() / 60 <= 240
            result['near'] += near
            result['near_ready'] += bool(near and line and context)
            result['near_line'] += bool(near and line)
            result['near_context'] += bool(near and context)
            result['near_missing_line'] += bool(near and not line)
            result['near_missing_context'] += bool(near and not context)
            evidence = self.data['matches'][match_identity(match)]['evidence']
            details = [row['payload'].get('details', {}) for name, row in evidence.items() if name.endswith(':context') and self.cached(name.split(':')[0], 'context', match)]
            if near:
                form = any((d.get('home_recent_count') or 0) > 0 and (d.get('away_recent_count') or 0) > 0 for d in details)
                result['form'] += form or any('form' in str(field).lower() and value not in (None, '', [], {}) for d in details for field, value in d.items())
                result['standings'] += any(any(token in str(field).lower() for token in ('standing', 'table')) and value not in (None, '', [], {}) for d in details for field, value in d.items())
                result['weather'] += bool(self.cached('weather', 'context', match))
            if match.commence_time.astimezone(TZ).date().isoformat() != self.date:
                result['lookahead'] += 1
                continue
            entry = self.data['matches'][match_identity(match)]
            result['collected_line'] += any(name.endswith(':offers') and row['payload'] for name, row in entry['evidence'].items())
            result['collected_context'] += any(name.endswith(':context') and not name.startswith('weather:') and row['payload'] and not (name == 'sstats:context' and market_only_context(MatchContext(**row['payload']))) for name, row in entry['evidence'].items())
            line, context = self.has_role(match, 'offers'), self.has_role(match, 'context')
            result['line'] += bool(line)
            result['context'] += bool(context)
            result['ready'] += bool(line and context)
        return result

    def save(self):
        self.data['issues'] = self.data['issues'][-100:]
        write_json(self.path, self.data)
