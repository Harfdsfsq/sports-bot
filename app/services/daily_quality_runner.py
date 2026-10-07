"""Native daily pipeline: no legacy startup monkey-patches or reserve promotions."""
from __future__ import annotations

import asyncio
import os
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.daily_match_registry import DailyMatchRegistry, match_identity, parse_time, read_json, write_json
from app.services.line_movement_state import evaluate_and_record_line_movement
from app.services.runner import PredictionRunner
from app.services.strict_price_integrity import rejection_reasons
from app.services.telegram import TelegramPublisher

ODDS = {'odds_api_io'}
CONTEXT = {'sstats', 'bzzoiro', 'football_data', 'thesportsdb', 'espn', 'openligadb'}


class DailyTelegramPublisher(TelegramPublisher):
    async def publish(self, bets, bankroll_summary=None):
        if self.settings.publish_dry_run:
            return await super().publish(bets, bankroll_summary=bankroll_summary)
        confirmed, payloads, count = [], [], 0
        for candidate in list(bets):
            lead = (candidate.commence_time.astimezone(UTC) - datetime.now(UTC)).total_seconds() / 60
            if lead < 30:
                continue
            try:
                sent, texts = await super().publish([candidate], bankroll_summary=bankroll_summary)
            except Exception as exc:
                self.delivery_errors = getattr(self, 'delivery_errors', []) + [type(exc).__name__]
                continue
            payloads.extend(texts)
            if sent > 0:
                confirmed.append(candidate)
                count += sent
                callback = getattr(self, 'on_confirm', None)
                if callback:
                    callback(candidate)
        # Downstream state must contain only forecasts with acknowledged delivery.
        bets[:] = confirmed
        return count, payloads


class DailyQualityRunner(PredictionRunner):
    def __init__(self, settings):
        super().__init__(settings)
        # The legacy v2 adapter depends on monkey patches and constructs invalid contexts.
        self.bzzoiro = self._safe_provider("app.providers.bzzoiro", "BzzoiroContextProvider")
        self.telegram = DailyTelegramPublisher(settings)
        self.telegram.on_confirm = self._record_confirmed
        self.registry = DailyMatchRegistry(Path('.data/daily_quality/registry.json'), datetime.now(UTC))
        self.work_plan = {}
        self.daily_rejections = Counter()
        self.selected_daily = []

    def _record_confirmed(self, candidate):
        identity = candidate.source_summary['registry_match_id']
        entry = self.registry.data['matches'].get(identity)
        if entry is not None:
            entry['publication'] = {'match_id': identity, 'tier': candidate.source_summary['publication_tier'], 'at': datetime.now(UTC).isoformat()}
        if not any(row['match_id'] == identity for row in self.registry.data['published']):
            self.registry.data['published'].append({'match_id': identity, 'tier': candidate.source_summary['publication_tier'], 'at': datetime.now(UTC).isoformat()})
            self.registry.save()

    async def _fetch_matches(self):
        matches, stats = self._load_day_inventory_matches(datetime.now(UTC))
        self.registry.sync(matches)
        # At the end of the day, cover fixtures beyond Moscow midnight as well.
        next_matches, _ = self._load_day_inventory_matches(datetime.now(UTC) + timedelta(days=1))
        self.next_day_inventory_count = len(next_matches)
        self.registry.sync([m for m in next_matches if m.commence_time <= datetime.now(UTC) + timedelta(hours=4)])
        self.inventory_matches = self.registry.matches()
        if not self.inventory_matches:
            raise RuntimeError('Current Moscow day inventory is empty; rebuild inventory')
        return self.inventory_matches, {'provider': 'daily_match_registry', 'stats': stats, 'attempts': {}}

    def _merge_day_inventory_matches(self, matches, metadata, now):
        return matches, metadata

    def _select_provider_context_matches(self, matches, provider_name, **kwargs):
        return matches if provider_name in CONTEXT else []

    async def _fetch_provider(self, provider, method_name, *args, empty_data):
        if not provider or not args or not isinstance(args[0], list):
            return await super()._fetch_provider(provider, method_name, *args, empty_data=empty_data)
        name = self._provider_name(provider)
        role = 'offers' if 'offer' in method_name else 'context'
        if name not in (ODDS if role == 'offers' else CONTEXT):
            return empty_data, {'enabled': False, 'reason': 'outside_daily_pipeline'}, {}
        self.registry.now = datetime.now(UTC)
        limit = int(os.getenv('DAILY_PROVIDER_MATCH_LIMIT', '80'))
        targets = self.registry.targets(name, role, limit=limit)
        self.work_plan[name + ':' + role] = [match_identity(m) for m in targets]
        cached = {m.match_key: self.registry.cached(name, role, m) for m in self.inventory_matches}
        cached = {key: value for key, value in cached.items() if value}
        data, stats, preview = {}, {'enabled': True}, {}
        if targets:
            try:
                data, stats, preview = await asyncio.wait_for(
                    super()._fetch_provider(provider, method_name, targets, *args[1:], empty_data=empty_data),
                    timeout=float(os.getenv('DAILY_PROVIDER_TIMEOUT_SECONDS', '300')),
                )
            except TimeoutError:
                stats = {'enabled': True, 'runtime_error': 'provider_deadline'}
            data = data if isinstance(data, dict) else {}
            # Drop data not associated with a requested canonical runtime match.
            requested_keys = {m.match_key for m in targets}
            data = {key: value for key, value in data.items() if key in requested_keys}
            self.registry.record(name, role, targets, data, stats)
            self.registry.now = datetime.now(UTC)
        for m in self.inventory_matches:
            value = self.registry.cached(name, role, m)
            if value:
                cached[m.match_key] = value
        # Older cache entries were constructed with percent values as fractions.
        if name == 'bzzoiro' and role == 'context':
            for key, context in list(cached.items()):
                if context.details.get('probability_units') != 'fraction' and isinstance(context.payload.get('prediction'), dict):
                    cached[key] = provider._prediction_to_context(context.payload['prediction'], context.payload.get('event'), context.details.get('bzzoiro_match_quality'))
        stats.update({'assigned_matches': len(targets), 'cached_matches': len(cached), 'daily_pipeline': True})
        return cached, stats, preview

    async def _fetch_weather_contexts(self, matches, base_contexts):
        self.registry.now = datetime.now(UTC)
        available = {m.match_key: m for m in matches if m.match_key in base_contexts}
        cached = {key: self.registry.cached('weather', 'context', m) for key, m in available.items()}
        targets = [m for key, m in available.items() if not cached[key]]
        data, stats, preview = await super()._fetch_weather_contexts(targets, base_contexts)
        self.registry.record('weather', 'context', targets, data, stats)
        # Weather cache carries old sporting fields; reuse only the weather effect.
        data.update({key: self._reapply_cached_weather(base_contexts[key], value) for key, value in cached.items() if value})
        return data, stats, preview

    @staticmethod
    def _reapply_cached_weather(base, cached):
        from app.utils import clamp
        weather = {key: value for key, value in cached.details.items() if key.startswith('weather_')}
        factor = clamp(float(weather.get('weather_total_factor', 1.0)), 0.78, 1.03)
        def adjusted(value):
            return clamp(float(value) * factor, 0.15, 4.80) if value is not None else None
        confidence = float(base.confidence)
        if weather.get('weather_adjustment_reasons'):
            confidence = clamp(confidence + 0.8, 50.0, 78.0)
        return replace(base, expected_home=adjusted(base.expected_home), expected_away=adjusted(base.expected_away), confidence=confidence, details={**base.details, **weather})

    def _filter_publishable_candidates(self, candidates):
        now = datetime.now(UTC)
        self.registry.now = now
        by_key = {m.match_key: m for m in self.inventory_matches}
        eligible = []
        for candidate in candidates:
            match = by_key.get(candidate.match_key)
            if not match:
                self.daily_rejections['unknown_match'] += 1
                continue
            candidate.source_summary.update({
                'daily_price_observed_at': self.registry.observation(match, 'offers'),
                'daily_context_observed_at': self.registry.observation(match, 'context'),
                'registry_match_id': match_identity(match),
            })
            reasons = rejection_reasons(candidate)
            movement = evaluate_and_record_line_movement(candidate, self.settings, now=now)
            # A first current quote is valid; an observed adverse move remains a blocker.
            if movement.get('status') in {'movement_failed', 'value_failed'}:
                reasons.append('line_or_value_changed')
            if reasons:
                self.daily_rejections.update(reasons)
                continue
            eligible.append(candidate)
        publishable = super()._filter_publishable_candidates(eligible)
        for candidate in eligible:
            if candidate not in publishable:
                self.daily_rejections.update(candidate.source_summary.get('publish_coverage_reasons') or ['quality_contract_blocked'])
        return publishable

    def _select_publishable_candidates(self, candidates):
        published = self.registry.data['published']
        sent_match_ids = {row.get('match_id') for row in published if row.get('match_id')}
        sent_match_ids.update(key for key, entry in self.registry.data['matches'].items() if entry.get('publication'))
        remaining = max(0, 5 - len(published))
        a_today = sum(row['tier'] == 'A' for row in published)
        b_today = sum(row['tier'] == 'B' for row in published)
        def priority(c):
            tier = c.source_summary.get('publication_tier')
            unmet = (tier == 'A' and a_today < 1) or (tier == 'B' and b_today < 2)
            return (unmet, tier == 'A', float(c.source_summary.get('quality_score') or 0), c.ev_pct)
        selected, matches = [], set()
        cap = min(remaining, int(self.settings.max_picks_per_run))
        for candidate in sorted(candidates, key=priority, reverse=True):
            if len(selected) >= cap:
                break
            if candidate.source_summary.get('registry_match_id') in sent_match_ids:
                self.daily_rejections['already_published_match'] += 1
                continue
            if candidate.match_key in matches:
                continue
            # Recheck at the final selection time; API work may have consumed lead time.
            lead = (candidate.commence_time.astimezone(UTC) - datetime.now(UTC)).total_seconds() / 60
            if not 30 <= lead <= 240:
                self.daily_rejections['final_kickoff_window'] += 1
                continue
            tier = candidate.source_summary['publication_tier']
            pct = 0.5 if tier == 'A' else 0.25
            amount = min(float(candidate.stake_amount), float(candidate.bankroll_snapshot) * pct / 100)
            if amount <= 0:
                continue
            candidate.stake_amount = round(amount, 2)
            candidate.stake_pct = pct
            candidate.risk_label = 'A_quality' if tier == 'A' else 'B_reduced'
            matches.add(candidate.match_key)
            selected.append(candidate)
        self.selected_daily = selected
        return selected

    async def run_once(self):
        try:
            summary = await super().run_once()
            summary['telegram_delivery_errors'] = getattr(self.telegram, 'delivery_errors', [])
            pending = self.state.pending_bets(include_shadow=False)
            summary['overdue_published_bets'] = [
                {'match': f"{row.get('home_team')} — {row.get('away_team')}", 'stake_amount': float(row.get('stake_amount') or 0)}
                for row in pending if parse_time(row.get('commence_time')) and datetime.now(UTC) - parse_time(row['commence_time']) >= timedelta(hours=6)
            ]
            self.registry.now = datetime.now(UTC)
            debug = read_json(summary.get('debug_path') or '.logs/debug-last-run.json', {})
            rows = debug.get('candidates_before_quality', []) if debug.get('summary', {}).get('started_time_utc') == summary.get('started_time_utc') else []
            quality_review = [{'match': f"{row.get('home_team')} — {row.get('away_team')}", 'selection': row.get('selection'), 'point': row.get('point'), 'odds': row.get('odds'), 'quality': (row.get('source_summary') or {}).get('quality_score'), 'confidence': row.get('confidence'), 'reasons': (row.get('source_summary') or {}).get('quality_reasons') or []} for row in rows[:5]]
            summary['daily_quality'] = {'quality_review': quality_review, 'coverage': self.registry.coverage(), 'next_day_inventory': getattr(self, 'next_day_inventory_count', 0), 'published_today': self.registry.data['published'], 'rejections': dict(self.daily_rejections), 'selected': [{'match': f'{c.home_team} — {c.away_team}', 'tier': c.source_summary['publication_tier'], 'quality': c.source_summary.get('quality_score'), 'odds': c.odds, 'selection': self.telegram._compact_selection_display(c.family, c.selection, c.point, c.team_side, c.home_team, c.away_team, c.selection_key), 'kickoff_utc': c.commence_time.isoformat(), 'probability_pct': round(c.adjusted_probability * 100, 1), 'ev_pct': round(c.ev_pct, 1), 'stake_amount': c.stake_amount} for c in self.selected_daily]}
            write_json('.data/exports/latest-run-summary.json', summary)
            return summary
        finally:
            self.registry.save()
            write_json('.data/exports/latest-daily-provider-plan.json', {'date_local': self.registry.date, 'run_id': os.getenv('GITHUB_RUN_ID'), 'assignments': self.work_plan, 'issues': self.registry.data['issues']})
