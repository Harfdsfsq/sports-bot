"""Native daily pipeline: no legacy startup monkey-patches or reserve promotions."""
from __future__ import annotations

import asyncio
from copy import deepcopy
import os
from collections import Counter
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.context_admission import admit_context
from app.services.daily_match_registry import DailyMatchRegistry, match_identity, parse_time, read_json, write_json
from app.services.line_movement_state import evaluate_and_record_line_movement
from app.services.runner import PredictionRunner
from app.services.strict_price_integrity import rejection_reasons
from app.services.telegram import TelegramPublisher
from app.services.daily_context_reliability import GOAL_MODEL_VERSION

ODDS = {'odds_api_io'}
CONTEXT = {'sstats', 'bzzoiro', 'football_data', 'thesportsdb', 'espn', 'openligadb', 'api_football'}


def same_reserve_fixture(first, second):
    """One observation per actual fixture/family/version, despite feed aliases."""
    from app.services.fixture_identity import same_team_identity
    def value(row, key):
        return row.get(key) if isinstance(row, dict) else getattr(row, key, None)
    def version(row):
        return value(row, 'goal_model_version') or (value(row, 'source_summary') or {}).get('goal_model_version')
    a = parse_time(value(first, 'commence_time_utc') or value(first, 'commence_time'))
    b = parse_time(value(second, 'commence_time_utc') or value(second, 'commence_time'))
    return bool(a and b and abs(a - b) <= timedelta(minutes=15)
                and value(first, 'family') == value(second, 'family')
                and version(first) == version(second)
                and same_team_identity(value(first, 'home_team'), [value(second, 'home_team')])
                and same_team_identity(value(first, 'away_team'), [value(second, 'away_team')]))


def unique_reserve_rows(rows):
    # Keep the first recorded observation, never choose by eventual outcome.
    unique = []
    for row in rows:
        if not any(same_reserve_fixture(row, previous) for previous in unique):
            unique.append(row)
    return unique


class DailyTelegramPublisher(TelegramPublisher):
    async def publish(self, bets, bankroll_summary=None):
        # Paper candidates must never enter a Telegram forecast, even if a
        # future caller accidentally passes a mixed list.
        bets[:] = [candidate for candidate in bets if not (getattr(candidate, 'source_summary', None) or {}).get('reserve_only')]
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
        self.clv_recovered = (self.market_monitor.restore_published_tracking(
            self.state.pending_bets(include_shadow=False), datetime.now(UTC)) if self.market_monitor else 0)

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

    def _provider_target_limit(self, provider, name, role):
        limit = max(0, int(os.getenv('DAILY_PROVIDER_MATCH_LIMIT', '80')))
        if role != 'context':
            return limit
        caps = {
            'espn': getattr(self.settings, 'espn_max_matches', 120),
            'football_data': getattr(self.settings, 'football_data_match_limit', 36),
            'bzzoiro': getattr(self.settings, 'bzzoiro_context_match_limit', 300),
            'openligadb': getattr(provider, 'match_limit', 80),
            'api_football': getattr(provider, 'context_limit', 0),
        }
        cap = int(caps.get(name) or 0)
        return min(limit, cap) if cap > 0 else limit

    @staticmethod
    def _provider_missing_key(provider, name):
        if hasattr(provider, 'api_key'):
            return not provider.api_key
        field = {'sstats': 'sstats_api_key', 'football_data': 'football_data_api_key'}.get(name)
        return bool(field and hasattr(provider, 'settings') and not getattr(provider.settings, field, None))

    @staticmethod
    def _provider_supports_match(provider, name, role, match):
        if role == 'offers':
            return True
        if name == 'openligadb' and hasattr(provider, '_competition_key'):
            return match.sport_key == 'soccer' and bool(provider._competition_key(match.league_name))
        supports = getattr(provider, 'supports_match', None)
        return bool(supports(match)) if callable(supports) else match.sport_key == 'soccer'

    async def _fetch_provider(self, provider, method_name, *args, empty_data):
        if not provider or not args or not isinstance(args[0], list):
            return await super()._fetch_provider(provider, method_name, *args, empty_data=empty_data)
        name = self._provider_name(provider)
        role = 'offers' if 'offer' in method_name else 'context'
        if name not in (ODDS if role == 'offers' else CONTEXT):
            return empty_data, {'enabled': False, 'reason': 'outside_daily_pipeline'}, {}
        self.registry.now = datetime.now(UTC)
        limit = self._provider_target_limit(provider, name, role)
        missing_key = role == 'context' and self._provider_missing_key(provider, name)
        targets = [] if missing_key else self.registry.targets(name, role, limit=limit, eligible=lambda m: self._provider_supports_match(provider, name, role, m), bulk=name == 'sstats' and role == 'context')
        self.work_plan[name + ':' + role] = [match_identity(m) for m in targets]
        cached = {m.match_key: self.registry.cached(name, role, m) for m in self.inventory_matches}
        cached = {key: value for key, value in cached.items() if value}
        data, stats, preview = {}, {'enabled': True, **({'api_key_present': False, 'reason': 'missing_api_key'} if missing_key else {})}, {}
        if targets:
            try:
                data, stats, preview = await asyncio.wait_for(
                    super()._fetch_provider(provider, method_name, targets, *args[1:], empty_data=empty_data),
                    timeout=float(os.getenv('DAILY_PROVIDER_TIMEOUT_SECONDS_' + name.upper(), os.getenv('DAILY_PROVIDER_TIMEOUT_SECONDS', '300'))),
                )
            except TimeoutError:
                stats = {'enabled': True, 'runtime_error': 'provider_deadline'}
            data = data if isinstance(data, dict) else {}
            # Drop data not associated with a requested canonical runtime match.
            requested_keys = {m.match_key for m in targets}
            data = {key: value for key, value in data.items() if key in requested_keys}
            attempted_keys = stats.get('attempted_match_keys')
            recorded_targets = targets
            if isinstance(attempted_keys, list):
                considered = set(attempted_keys) | set(data)
                recorded_targets = [m for m in targets if m.match_key in considered]
            stats['unattempted_matches'] = len(targets) - len(recorded_targets)
            stats['registry_record'] = self.registry.record(name, role, recorded_targets, data, stats)
            self.registry.now = datetime.now(UTC)
        # Rebuild at completion: early cache entries may expire during API calls.
        cached = {}
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
        if role == 'context':
            self._retain_fixture_venues(name, cached)
        if name == 'odds_api_io' and hasattr(provider, '_odds_accounts'):
            stats['configured_accounts'] = [account['name'] for account in provider._odds_accounts()]
        return cached, stats, preview

    def _merge_context_maps(self, *maps):
        now = datetime.now(UTC)
        by_key = {m.match_key: m for m in self.inventory_matches}
        for mapping in maps:
            for key, context in list((mapping or {}).items()):
                match = by_key.get(key)
                provider = context.details.get('daily_evidence_provider') or ('sstats' if context.source == 'sstats_form' else context.source)
                observed = context.details.get('daily_evidence_observed_at') or context.details.get('daily_source_observed_at')
                reasons = admit_context(provider, match, context, observed, now) if match else ['unknown_match']
                if reasons:
                    mapping.pop(key)
                    self.daily_rejections.update(reasons)
        from app.services.daily_context_reliability import blend_contexts
        groups = {}
        for mapping in maps:
            for key, context in (mapping or {}).items():
                groups.setdefault(key, []).append(context)
        return {key: blend_contexts(values, self._context_blend_weight) for key, values in groups.items()}

    def _prepare_model_context_maps(self, context_maps, contexts):
        # Revalidate after the slowest provider and weather requests, before bundles.
        sporting = [mapping for provider, mapping in context_maps.items() if provider != 'weather']
        fresh = self._merge_context_maps(*sporting)
        weather = context_maps.get('weather', {})
        for key, cached_weather in list(weather.items()):
            if key not in fresh:
                weather.pop(key)
                continue
            weather[key] = self._reapply_cached_weather(fresh[key], cached_weather)
        fresh.update(weather)
        self.admitted_sources = {}
        for provider, mapping in context_maps.items():
            if provider == 'weather':
                continue
            for key, context in mapping.items():
                self.admitted_sources.setdefault(key, {})[provider] = context.details.get('daily_evidence_observed_at') or context.details.get('daily_source_observed_at')
        return fresh

    def _retain_fixture_venues(self, provider, contexts):
        # Use only the venue of the matched event, never a team's usual stadium.
        if provider not in {'bzzoiro', 'espn'}:
            return
        changed = False
        for match in self.inventory_matches:
            context = contexts.get(match.match_key)
            event = context.payload.get('event') if context else None
            venue = event.get('venue') if isinstance(event, dict) else None
            if provider == 'espn' and isinstance(event, dict):
                raw_venue = (event.get('competitions') or [{}])[0].get('venue') or {}
                address = raw_venue.get('address') or {}
                venue = {'name': raw_venue.get('fullName'), 'city': address.get('city'), 'country': address.get('country')}
            if not isinstance(venue, dict) or not venue.get('city') or not venue.get('country'):
                continue
            match.metadata.update(venue=dict(venue), country=venue['country'], venue_source=provider)
            entry = self.registry.data['matches'].get(match_identity(match))
            if entry is not None:
                entry['match']['metadata'].update(venue=dict(venue), country=venue['country'], venue_source=provider)
                changed = True
        if changed:
            self.registry.save()

    async def _fetch_weather_contexts(self, matches, base_contexts):
        self.registry.now = datetime.now(UTC)
        for match in matches:
            metadata = self.registry.data['matches'].get(match_identity(match), {}).get('match', {}).get('metadata', {})
            if metadata.get('venue_source') in {'bzzoiro', 'espn'}:
                match.metadata.update({key: metadata[key] for key in ('venue', 'country', 'venue_source') if key in metadata})
        # Give verified locations priority within the weather request budget.
        matches = sorted(matches, key=lambda m: not (isinstance(m.metadata.get('venue'), dict) and m.metadata['venue'].get('city') and m.metadata.get('country')))
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
            admission = getattr(self, 'admitted_sources', {}).get(candidate.match_key, {})
            candidate.source_summary['context_admission'] = dict(admission)
            reasons = rejection_reasons(candidate)
            if hasattr(self, 'admitted_sources') and not admission:
                reasons.append('context_identity_missing')
            for provider, observed in admission.items():
                current = self.registry.cached(provider, 'context', match)
                if current is None or current.details.get('daily_evidence_observed_at') != observed:
                    reasons.append('context_source_expired_before_publication')
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
        candidates = [candidate for candidate in candidates if not candidate.source_summary.get('reserve_only')]
        published = self.registry.data['published']
        sent_match_ids = {row.get('match_id') for row in published if row.get('match_id')}
        sent_match_ids.update(key for key, entry in self.registry.data['matches'].items() if entry.get('publication'))
        remaining = max(0, 5 - len(published))
        if remaining == 0:
            self.daily_rejections['daily_publication_limit'] += len(candidates)
            self.selected_daily = []
            return []
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

    def _select_shadow_candidates(self, **kwargs):
        existing_shadow = super()._select_shadow_candidates(**kwargs)
        totals_trial = os.getenv('DAILY_TOTALS_TRIAL_ENABLED', 'false').lower() == 'true'
        if totals_trial:
            # Totals enter only the predeclared one-per-fixture quality trial.
            existing_shadow = [c for c in existing_shadow if c.family != 'totals']
        self.reserve_evaluation = {'evaluated': getattr(self.factory, 'daily_reserve_evaluated_count', 0),
                                   'selected_new': 0, 'rejections': {}}
        if os.getenv('DAILY_RESERVE_TRACKING_ENABLED', 'false').lower() != 'true':
            return existing_shadow
        from app.services.daily_quality_policy import quality_decision
        pool = list(getattr(self.factory, 'daily_reserve_candidates', []))
        reserve_passed_model = len(pool)
        if totals_trial:
            totals = deepcopy([c for c in kwargs.get('candidates_before_quality', []) if c.family == 'totals'])
            for candidate in totals:
                for key in ('quality_reasons', 'quality_status', 'research_blockers'):
                    candidate.source_summary.pop(key, None)
                candidate.diagnostics.pop('quality', None)
                candidate.source_summary['frozen_quality_adjustments'] = True
            pool.extend(totals)
        self.reserve_evaluation.update(passed_model=reserve_passed_model, model_rejections=getattr(self.factory, 'daily_reserve_rejections', {}))
        clv = self.market_monitor.resolved_clv_rows() if self.market_monitor else []
        report = self.quality.build_quality_report(self.state.prediction_ledger(self.settings, include_shadow=False), clv)
        pool, rejected, quality_debug = self.quality.apply_to_candidates(pool, report, datetime.now(UTC), research_only=True)
        self.reserve_evaluation['historically_blocked_under_observation'] = sum(bool(c.source_summary.get('research_blockers')) for c in pool)
        if totals_trial:
            rejected = Counter(reason for decision in quality_debug.get('decisions', [])
                               if decision.get('family') != 'totals' for reason in decision.get('reasons', []))
        self.reserve_evaluation['rejections'].update(rejected)
        by_key = {match.match_key: match for match in self.inventory_matches}
        ledger = self.state.prediction_ledger(self.settings, include_shadow=True)
        fingerprints = {row.get('fingerprint') for row in ledger}
        reserved = {(row.get('match_key'), row.get('family'), row.get('commence_time_utc'), row.get('goal_model_version'))
                    for row in ledger if row.get('reserve_only')}
        reserved_rows = [row for row in ledger if row.get('reserve_only')]
        selected = []
        limit = max(0, int(os.getenv('DAILY_RESERVE_MAX_NEW_PER_RUN', '25')))
        for candidate in sorted(pool, key=lambda c: (c.source_summary.get('quality_score', 0), c.ev_pct), reverse=True):
            if len(selected) >= limit:
                break
            signature = (candidate.match_key, candidate.family, candidate.commence_time.isoformat(), candidate.source_summary.get('goal_model_version'))
            if signature in reserved or self._candidate_fingerprint(candidate) in fingerprints or any(same_reserve_fixture(candidate, previous) for previous in reserved_rows):
                continue
            match = by_key.get(candidate.match_key)
            if match is None:
                continue
            now = datetime.now(UTC)
            self.registry.now = now
            candidate.source_summary.update(
                daily_price_observed_at=self.registry.observation(match, 'offers'),
                daily_context_observed_at=self.registry.observation(match, 'context'),
                registry_match_id=match_identity(match), reserve_only=True, can_publish=False,
                shadow_tracking_reason='reserve_market_test')
            if totals_trial and candidate.family == 'totals':
                from app.services.totals_trial import PROTOCOL
                candidate.source_summary['validation_protocol'] = PROTOCOL
            admission = getattr(self, 'admitted_sources', {}).get(candidate.match_key, {})
            reasons = rejection_reasons(candidate)
            if not admission:
                reasons.append('context_identity_missing')
            for provider, observed in admission.items():
                current = self.registry.cached(provider, 'context', match)
                if current is None or current.details.get('daily_evidence_observed_at') != observed:
                    reasons.append('context_source_expired_before_publication')
            if not self.registry.has_role(match, 'offers'):
                reasons.append('missing_real_line_or_context')
            coverage = {'odds_sources_count': candidate.sources_count,
                        'context_sources_count': len(admission), 'books_count': candidate.books_count}
            _, policy_reasons, _ = quality_decision(candidate, coverage,
                                                    now=now, allow_shadow_markets=True)
            reasons.extend(policy_reasons)
            if reasons:
                for reason in set(reasons) if candidate.family != 'totals' else []:
                    self.reserve_evaluation['rejections'][reason] = self.reserve_evaluation['rejections'].get(reason, 0) + 1
                continue
            candidate.stake_amount, candidate.stake_pct = 0., 0.
            selected.append(candidate)
            reserved.add(signature)
            reserved_rows.append(candidate)
        self.reserve_evaluation['selected_new'] = sum(c.family != 'totals' for c in selected)
        self.reserve_evaluation['historically_blocked_under_observation'] = sum(bool(c.source_summary.get('research_blockers')) for c in selected if c.family != 'totals')
        return existing_shadow + selected

    async def run_once(self):
        try:
            summary = await super().run_once()
            summary['candidates_model_evaluated'] = getattr(self.factory, 'prefilter_candidates_count', 0)
            summary['telegram_delivery_errors'] = getattr(self.telegram, 'delivery_errors', [])
            summary['goal_model_version'] = GOAL_MODEL_VERSION
            from app.services.goal_research import GoalHistory
            try:
                summary['goal_research'] = GoalHistory().run_research(self.inventory_matches, datetime.now(UTC))
            except (OSError, ValueError, TypeError, KeyError, AttributeError, OverflowError) as exc:
                summary['goal_research'] = {'status': 'error', 'error_type': type(exc).__name__}
            write_json('.data/exports/latest-goal-research.json', summary['goal_research'])
            summary['clv_recovered_from_ledger'] = self.clv_recovered
            quality_report = read_json('.data/exports/latest-quality-report.json', {})
            summary['goal_model_history'] = {
                version: {'settled': cohort['settled_total'], **cohort['summary']}
                for version, cohort in (quality_report.get('model_versions') or {}).items()}
            reserve_rows = [row for row in self.state.prediction_ledger(self.settings, include_shadow=True)
                            if row.get('reserve_only') and row.get('family') != 'totals' and row.get('goal_model_version') == GOAL_MODEL_VERSION]
            reserve_duplicates = len(reserve_rows)
            reserve_rows = unique_reserve_rows(reserve_rows)
            reserve_duplicates -= len(reserve_rows)
            outcomes = Counter(row.get('status') for row in reserve_rows)
            summary['reserve_markets'] = {
                **getattr(self, 'reserve_evaluation', {}), 'tracked': len(reserve_rows),
                'duplicates_ignored': reserve_duplicates,
                'pending': outcomes.get('shadow_pending', 0),
                'settled': sum(outcomes.get(status, 0) for status in ('won', 'lost', 'push', 'void')),
                'wins': outcomes.get('won', 0), 'losses': outcomes.get('lost', 0), 'pushes': outcomes.get('push', 0),
                'paper_pnl': round(sum(float(row.get('pnl') or 0) for row in reserve_rows), 2),
                'families': dict(Counter(row.get('family') for row in reserve_rows))}
            from app.services.totals_trial import summarize as summarize_totals_trial
            summary['totals_trial'] = summarize_totals_trial(
                self.state.prediction_ledger(self.settings, include_shadow=True), GOAL_MODEL_VERSION)
            write_json('.data/exports/latest-totals-forward-trial.json', summary['totals_trial'])
            from app.services.api_budget import ApiBudget
            summary['api_budget'] = ApiBudget().snapshot()
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
            reviews = self.registry.identity_review()
            summary['daily_quality']['identity_review_pairs'] = len(reviews)
            write_json('.data/exports/latest-coverage-identity-review.json', {
                'created_at_utc': self.registry.now.isoformat(), 'automatic_merge': False, 'pairs': reviews})
            write_json('.data/exports/latest-run-summary.json', summary)
            return summary
        finally:
            self.registry.save()
            write_json('.data/exports/latest-daily-provider-plan.json', {'date_local': self.registry.date, 'run_id': os.getenv('GITHUB_RUN_ID'), 'assignments': self.work_plan, 'issues': self.registry.data['issues']})
