from __future__ import annotations

import csv
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
UTC = timezone.utc
from pathlib import Path
from statistics import mean
from typing import Any

from app.schemas import CandidateBet
from app.utils import candidate_selection_key, clamp


class PredictionQualityService:
    def __init__(self, settings: Any) -> None:
        self.settings = settings


    def _setting(self, name: str, default: Any) -> Any:
        value = getattr(self.settings, name, None)
        if value is not None:
            return value
        env_value = os.getenv(str(name or '').upper())
        if env_value is None:
            return default
        if isinstance(default, bool):
            return str(env_value).strip().lower() in {'1', 'true', 'yes', 'on'}
        if isinstance(default, int) and not isinstance(default, bool):
            try:
                return int(float(env_value))
            except Exception:
                return default
        if isinstance(default, float):
            try:
                return float(env_value)
            except Exception:
                return default
        if isinstance(default, (list, tuple, set)):
            return [item.strip() for item in str(env_value).split(',') if item.strip()]
        return env_value

    def build_quality_report(
        self,
        ledger_rows: list[dict[str, Any]],
        clv_rows: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        rows = [dict(item) for item in ledger_rows if isinstance(item, dict)]
        settled = [row for row in rows if self._binary_result(row) is not None]
        segments = self._segment_stats(settled)
        clv = self._clv_segment_stats(clv_rows or [])
        report = {
            'created_at': datetime.now(UTC).isoformat(),
            'enabled': bool(self._setting('quality_layer_enabled', True)),
            'summary': {
                'total_tracked_bets': len(rows),
                'settled_binary_bets': len(settled),
                **self._portfolio_summary(settled),
            },
            'segments': segments,
            'calibration': {
                'probability_buckets': self._calibration_bucket_stats(settled),
                'brier_score': self._brier_score(settled),
            },
            'clv': clv,
            'backtest': self._backtest_policy(settled),
            'error_analysis': self.analyze_rows(rows),
        }
        report['profile'] = self._profile_from_stats(segments, clv)
        report['learning'] = self._learning_summary(report['profile'])
        return report

    def apply_to_candidates(
        self,
        candidates: list[CandidateBet],
        quality_report: dict[str, Any],
        now_utc: datetime,
    ) -> tuple[list[CandidateBet], dict[str, int], dict[str, Any]]:
        if not bool(self._setting('quality_layer_enabled', True)):
            return candidates, {}, {'enabled': False, 'decisions': []}

        profile = dict(quality_report.get('profile') or {})
        summary = dict(quality_report.get('summary') or {})
        settled_count = int(summary.get('settled_binary_bets') or 0)
        min_history = max(0, int(self._setting('quality_min_history_bets', 12) or 12))
        enough_history = settled_count >= min_history
        recent_learning_state = dict(quality_report.get('recent_learning_state') or {})

        passed: list[CandidateBet] = []
        rejections: Counter[str] = Counter()
        decisions: list[dict[str, Any]] = []
        for candidate in candidates:
            original_probability = float(candidate.adjusted_probability)
            segments = self._candidate_segments(candidate)
            calibration = self._candidate_calibration(segments, profile, enough_history)
            if calibration['applied']:
                self._apply_probability_adjustment(candidate, float(calibration['delta_probability']))
            learning_adjustment = self._candidate_learning_adjustment(segments, profile, enough_history)
            if learning_adjustment['applied']:
                candidate.publication_score = round(
                    float(getattr(candidate, 'publication_score', 0.0) or 0.0)
                    + float(learning_adjustment['score_delta']),
                    3,
                )
                candidate.source_summary['learning_adjustment'] = learning_adjustment
                if abs(float(learning_adjustment['score_delta'])) >= 1.0:
                    candidate.reasons.append(
                        f"learning=score_delta({float(learning_adjustment['score_delta']):+.2f})"
                    )
            recent_adjustment = self._candidate_recent_adjustment(candidate, recent_learning_state)
            if recent_adjustment['applied']:
                candidate.publication_score = round(
                    float(getattr(candidate, 'publication_score', 0.0) or 0.0)
                    + float(recent_adjustment['score_delta']),
                    3,
                )
                candidate.source_summary['nightly_review_adjustment'] = recent_adjustment
                candidate.reasons.append(
                    f"nightly_review=score_delta({float(recent_adjustment['score_delta']):+.2f})"
                )

            quality_score = self._candidate_quality_score(candidate, segments, profile, enough_history)
            status = 'passed_quality'
            reasons: list[str] = []

            for guard in (
                self._market_sanity_guard,
                lambda item: self._clv_guard(segments, profile, enough_history),
                lambda item: self._historical_segment_guard(item, segments, profile, enough_history),
                lambda item: self._quarantine_guard(item, segments, profile, enough_history),
                lambda item: self._high_odds_guard(item, quality_score),
                self._post_calibration_threshold_guard,
            ):
                reason = guard(candidate)
                if reason:
                    status = 'quarantined_shadow' if reason.startswith('quarantine_') else 'rejected_by_quality_filters'
                    reasons.append(reason)
                    break

            if status == 'passed_quality' and bool(self._setting('no_bet_quality_score_enabled', True)):
                min_score = float(self._setting('min_quality_score_publish', 60.0) or 60.0)
                if quality_score < min_score:
                    status = 'rejected_by_quality_filters'
                    reasons.append('no_bet_quality_score_guard')

            if status == 'passed_quality':
                passed.append(candidate)
            else:
                rejections[reasons[0] if reasons else status] += 1

            payload = {
                'status': status,
                'quality_score': round(quality_score, 3),
                'reasons': reasons,
                'calibration': calibration,
                'learning_adjustment': learning_adjustment,
                'nightly_review_adjustment': recent_adjustment,
                'segments': segments,
                'original_adjusted_probability': round(original_probability, 5),
                'final_adjusted_probability': round(float(candidate.adjusted_probability), 5),
                'publication_score': round(float(getattr(candidate, 'publication_score', 0.0) or 0.0), 3),
                'evaluated_at': now_utc.isoformat(),
            }
            candidate.diagnostics.setdefault('quality', payload)
            candidate.source_summary['quality_status'] = status
            candidate.source_summary['quality_score'] = round(quality_score, 3)
            if reasons:
                candidate.source_summary['quality_reasons'] = reasons
                candidate.reasons.extend(f'quality={reason}' for reason in reasons)
            decisions.append({
                'match_key': candidate.match_key,
                'family': candidate.family,
                'selection_key': candidate.selection_key,
                'team_side': candidate.team_side,
                'point': candidate.point,
                **payload,
            })

        if not passed and candidates and bool(self._setting('quality_emergency_publish_enabled', False)):
            fallback_candidate = self._select_emergency_publish_candidate(candidates)
            if fallback_candidate is not None:
                passed = [fallback_candidate]
                rejections['quality_emergency_publish_used'] += 1
                for decision in decisions:
                    if decision.get('match_key') == fallback_candidate.match_key and decision.get('selection_key') == fallback_candidate.selection_key:
                        decision['status'] = 'passed_quality_emergency'
                        reasons = list(decision.get('reasons') or [])
                        reasons.append('quality_emergency_publish')
                        decision['reasons'] = reasons
                        decision['quality_score'] = round(float(decision.get('quality_score') or 0.0), 3)
                        break
                fallback_candidate.source_summary['quality_status'] = 'passed_quality_emergency'
                fallback_candidate.source_summary['quality_reasons'] = ['quality_emergency_publish']
                fallback_candidate.reasons.append('quality=quality_emergency_publish')
                fallback_candidate.diagnostics.setdefault('quality', {})
                fallback_candidate.diagnostics['quality']['status'] = 'passed_quality_emergency'
                fallback_candidate.diagnostics['quality']['reasons'] = ['quality_emergency_publish']

        if not passed and candidates:
            historical_relief = self._select_historical_guard_relief_candidate(candidates, decisions)
            if historical_relief is not None:
                passed = [historical_relief]
                rejections['quality_historical_guard_relief_used'] += 1
                for decision in decisions:
                    if decision.get('match_key') == historical_relief.match_key and decision.get('selection_key') == historical_relief.selection_key:
                        decision['status'] = 'passed_quality_historical_relief'
                        reasons = list(decision.get('reasons') or [])
                        reasons.append('quality_historical_guard_relief')
                        decision['reasons'] = reasons
                        break
                historical_relief.source_summary['quality_status'] = 'passed_quality_historical_relief'
                historical_relief.source_summary['quality_reasons'] = ['quality_historical_guard_relief']
                historical_relief.reasons.append('quality=quality_historical_guard_relief')
                historical_relief.diagnostics.setdefault('quality', {})
                historical_relief.diagnostics['quality']['status'] = 'passed_quality_historical_relief'
                historical_relief.diagnostics['quality']['reasons'] = ['quality_historical_guard_relief']

        if not passed and candidates and bool(self._setting('quality_last_resort_publish_enabled', False)):
            last_resort = self._select_last_resort_quality_candidate(candidates, decisions)
            if last_resort is not None:
                passed = [last_resort]
                rejections['quality_last_resort_publish_used'] += 1
                for decision in decisions:
                    if decision.get('match_key') == last_resort.match_key and decision.get('selection_key') == last_resort.selection_key:
                        decision['status'] = 'passed_quality_last_resort'
                        reasons = list(decision.get('reasons') or [])
                        reasons.append('quality_last_resort_publish')
                        decision['reasons'] = reasons
                        break
                last_resort.source_summary['quality_status'] = 'passed_quality_last_resort'
                last_resort.source_summary['quality_reasons'] = ['quality_last_resort_publish']
                last_resort.reasons.append('quality=quality_last_resort_publish')
                last_resort.diagnostics.setdefault('quality', {})
                last_resort.diagnostics['quality']['status'] = 'passed_quality_last_resort'
                last_resort.diagnostics['quality']['reasons'] = ['quality_last_resort_publish']

        passed.sort(key=lambda item: (
            float(getattr(item, 'publication_score', 0.0) or 0.0),
            float(getattr(item, 'ev_pct', 0.0) or 0.0),
            float(getattr(item, 'edge_pct', 0.0) or 0.0),
        ), reverse=True)
        return passed, dict(rejections), {
            'enabled': True,
            'enough_history': enough_history,
            'settled_binary_bets': settled_count,
            'decisions': decisions,
            'passed': len(passed),
            'rejected': len(candidates) - len(passed),
        }

    def _select_historical_guard_relief_candidate(
        self,
        candidates: list[CandidateBet],
        decisions: list[dict[str, Any]],
    ) -> CandidateBet | None:
        if not bool(self._setting('historical_segment_relief_enabled', False)):
            return None
        if not decisions:
            return None
        if any((item.get('status') == 'passed_quality') for item in decisions):
            return None
        bad_keys = {'bad_historical_segment_guard', 'historical_guard'}
        offenders: set[tuple[str, str]] = set()
        for item in decisions:
            reasons = list(item.get('reasons') or [])
            top_reason = reasons[0] if reasons else ''
            if top_reason in bad_keys:
                offenders.add((str(item.get('match_key') or ''), str(item.get('selection_key') or '')))
                continue
            return None
        if not offenders:
            return None
        allowed_families = {'h2h', 'btts', 'dnb', 'doubleChance', 'teamTotals', 'totals', 'spreads'}
        min_conf = float(self._setting('historical_segment_relief_min_confidence', 58.0) or 58.0)
        min_ev = float(self._setting('historical_segment_relief_min_ev_pct', 0.8) or 0.8)
        min_edge = float(self._setting('historical_segment_relief_min_edge_pct', 1.0) or 1.0)
        edge_tolerance = max(0.0, float(self._setting('historical_segment_relief_edge_tolerance_pp', 0.0) or 0.0))
        min_books = max(1, int(self._setting('historical_segment_relief_min_books', 1) or 1))
        min_sources = max(1, int(self._setting('historical_segment_relief_min_sources', 1) or 1))
        min_context_sources = max(0, int(self._setting('historical_segment_relief_min_context_sources', 0) or 0))
        min_publication_score = float(self._setting('historical_segment_relief_min_publication_score', 16.0) or 16.0)
        ranked = sorted(
            candidates,
            key=lambda item: (
                float(getattr(item, 'publication_score', 0.0) or 0.0),
                float(getattr(item, 'confidence', 0.0) or 0.0),
                float(getattr(item, 'ev_pct', 0.0) or 0.0),
                float(getattr(item, 'edge_pct', 0.0) or 0.0),
            ),
            reverse=True,
        )
        for item in ranked:
            selection_key = str(
                item.selection_key
                or candidate_selection_key(
                    str(item.family or ''),
                    str(item.selection or ''),
                    point=item.point,
                    team_side=getattr(item, 'team_side', None),
                    home_team=str(getattr(item, 'home_team', '') or ''),
                    away_team=str(getattr(item, 'away_team', '') or ''),
                )
            )
            if (str(item.match_key or ''), selection_key) not in offenders:
                continue
            if item.family not in allowed_families:
                continue
            if not self._passes_quality_fallback_profile(item):
                continue
            if float(item.confidence) < min_conf:
                continue
            if float(item.ev_pct) < min_ev or float(item.edge_pct) < (min_edge - edge_tolerance):
                continue
            if float(getattr(item, 'publication_score', 0.0) or 0.0) < min_publication_score:
                continue
            if int(getattr(item, 'books_count', 0) or 0) < min_books:
                continue
            if int(getattr(item, 'sources_count', 0) or 0) < min_sources:
                continue
            if self._candidate_context_sources_count(item) < min_context_sources:
                continue
            item.source_summary['historical_segment_relief'] = {
                'min_edge_pct': round(min_edge, 3),
                'edge_tolerance_pp': round(edge_tolerance, 3),
                'edge_pct': round(float(item.edge_pct), 3),
                'min_sources': min_sources,
                'min_context_sources': min_context_sources,
            }
            return item
        return None

    def _select_emergency_publish_candidate(self, candidates: list[CandidateBet]) -> CandidateBet | None:
        families = {'h2h', 'btts', 'dnb', 'doubleChance', 'teamTotals'}
        min_conf = float(self._setting('quality_emergency_min_confidence', 50.0) or 50.0)
        min_ev = float(self._setting('quality_emergency_min_ev_pct', 0.4) or 0.4)
        min_edge = float(self._setting('quality_emergency_min_edge_pct', 0.6) or 0.6)
        min_books = max(1, int(self._setting('quality_emergency_min_books', 1) or 1))
        ranked = sorted(
            candidates,
            key=lambda item: (
                float(getattr(item, 'publication_score', 0.0) or 0.0),
                float(getattr(item, 'confidence', 0.0) or 0.0),
                float(getattr(item, 'ev_pct', 0.0) or 0.0),
                float(getattr(item, 'edge_pct', 0.0) or 0.0),
            ),
            reverse=True,
        )
        for item in ranked:
            if item.family not in families:
                continue
            if not self._passes_quality_fallback_profile(item):
                continue
            if float(item.confidence) < min_conf:
                continue
            if float(item.ev_pct) < min_ev or float(item.edge_pct) < min_edge:
                continue
            if int(getattr(item, 'books_count', 0) or 0) < min_books:
                continue
            return item
        return None

    def _select_last_resort_quality_candidate(
        self,
        candidates: list[CandidateBet],
        decisions: list[dict[str, Any]],
    ) -> CandidateBet | None:
        if not candidates or not decisions:
            return None
        allowed_reasons = {
            'bad_historical_segment_guard',
            'no_bet_quality_score_guard',
            'post_calibration_probability_guard',
            'post_calibration_edge_guard',
            'post_calibration_ev_guard',
        }
        offenders: set[tuple[str, str]] = set()
        for item in decisions:
            reasons = list(item.get('reasons') or [])
            if not reasons:
                return None
            if reasons[0] not in allowed_reasons:
                return None
            offenders.add((str(item.get('match_key') or ''), str(item.get('selection_key') or '')))
        ranked = sorted(
            candidates,
            key=lambda item: (
                float(getattr(item, 'publication_score', 0.0) or 0.0),
                float(getattr(item, 'confidence', 0.0) or 0.0),
                float(getattr(item, 'ev_pct', 0.0) or 0.0),
                float(getattr(item, 'edge_pct', 0.0) or 0.0),
            ),
            reverse=True,
        )
        min_conf = float(self._setting('quality_last_resort_min_confidence', 45.0) or 45.0)
        min_ev = float(self._setting('quality_last_resort_min_ev_pct', 0.0) or 0.0)
        min_edge = float(self._setting('quality_last_resort_min_edge_pct', 0.0) or 0.0)
        min_books = max(1, int(self._setting('quality_last_resort_min_books', 1) or 1))
        for item in ranked:
            selection_key = str(
                item.selection_key
                or candidate_selection_key(
                    str(item.family or ''),
                    str(item.selection or ''),
                    point=item.point,
                    team_side=getattr(item, 'team_side', None),
                    home_team=str(getattr(item, 'home_team', '') or ''),
                    away_team=str(getattr(item, 'away_team', '') or ''),
                )
            )
            if (str(item.match_key or ''), selection_key) not in offenders:
                continue
            if not self._passes_quality_fallback_profile(item):
                continue
            if float(item.confidence) < min_conf:
                continue
            if float(item.ev_pct) < min_ev or float(item.edge_pct) < min_edge:
                continue
            if int(getattr(item, 'books_count', 0) or 0) < min_books:
                continue
            return item
        return None

    def _passes_quality_fallback_profile(self, candidate: CandidateBet) -> bool:
        if not self._is_core_league_candidate(candidate):
            return False
        if int(getattr(candidate, 'books_count', 0) or 0) < max(2, int(self._setting('quality_fallback_min_books_strict', 2) or 2)):
            return False
        if self._candidate_context_source(candidate) == 'sstats_form':
            return False
        if self._candidate_market_contradiction(candidate):
            return False
        if float(candidate.odds) >= float(self._setting('quality_high_odds_min_odds', 3.40) or 3.40) and float(candidate.confidence) < 68.0:
            return False
        if candidate.family == 'totals':
            return self._passes_totals_quality_fallback_profile(candidate)
        return True

    def _passes_totals_quality_fallback_profile(self, candidate: CandidateBet) -> bool:
        if not bool(self._setting('quality_totals_fallback_enabled', True)):
            return False
        if int(getattr(candidate, 'books_count', 0) or 0) < max(2, int(self._setting('quality_totals_fallback_min_books', 2) or 2)):
            return False
        if float(candidate.confidence) < float(self._setting('quality_totals_fallback_min_confidence', 58.0) or 58.0):
            return False
        if float(candidate.ev_pct) < float(self._setting('quality_totals_fallback_min_ev_pct', 1.0) or 1.0):
            return False
        if float(candidate.edge_pct) < float(self._setting('quality_totals_fallback_min_edge_pct', 1.5) or 1.5):
            return False
        headroom = self._totals_xg_headroom(candidate)
        if headroom is None:
            return False
        selection_kind = self._candidate_selection_kind(candidate)
        min_headroom = float(
            self._setting(
                'quality_totals_fallback_under_min_xg_headroom' if selection_kind == 'under' else 'quality_totals_fallback_over_min_xg_headroom',
                0.06 if selection_kind == 'under' else 0.16,
            ) or (0.06 if selection_kind == 'under' else 0.16)
        )
        if headroom < min_headroom:
            return False
        if selection_kind == 'over' and float(candidate.confidence) < max(
            float(self._setting('quality_totals_fallback_min_confidence', 58.0) or 58.0),
            61.0,
        ):
            return False
        return True

    def analyze_daily_report(self, daily_report: dict[str, Any]) -> dict[str, Any]:
        rows = [dict(item) for item in (daily_report.get('rows') or []) if isinstance(item, dict)]
        return self.analyze_rows(rows)

    def build_next_day_adjustments(self, daily_report: dict[str, Any]) -> dict[str, Any]:
        rows = [dict(item) for item in (daily_report.get('rows') or []) if isinstance(item, dict)]
        settled = [row for row in rows if self._binary_result(row) is not None]
        family_adjustments: dict[str, dict[str, Any]] = {}
        league_adjustments: dict[str, dict[str, Any]] = {}
        actions: list[dict[str, Any]] = []

        for family, group in self._group_rows(settled, lambda row: str(row.get('family') or 'unknown')).items():
            summary = self._portfolio_summary(group)
            count = int(summary.get('count') or 0)
            if count < 2:
                continue
            roi = float(summary.get('roi_pct') or 0.0)
            hit = float(summary.get('hit_rate_pct') or 0.0)
            score_delta = 0.0
            reason = ''
            if roi <= -18.0 or (count >= 3 and hit < 35.0):
                score_delta = -1.6
                reason = 'cooldown_after_bad_day'
            elif roi >= 8.0 and hit >= 52.0:
                score_delta = 0.8
                reason = 'reinforce_after_good_day'
            if abs(score_delta) < 0.01:
                continue
            family_adjustments[family] = {
                'score_delta': round(score_delta, 3),
                'count': count,
                'roi_pct': round(roi, 2),
                'hit_rate_pct': round(hit, 2),
                'reason': reason,
            }
            actions.append({
                'scope': 'family',
                'key': family,
                'score_delta': round(score_delta, 3),
                'reason': reason,
            })

        for bucket, group in self._group_rows(
            settled,
            lambda row: self._league_bucket_from_text(str(row.get('league_name') or ''), str(row.get('match_tier') or '')),
        ).items():
            summary = self._portfolio_summary(group)
            count = int(summary.get('count') or 0)
            if count < 2:
                continue
            roi = float(summary.get('roi_pct') or 0.0)
            score_delta = 0.0
            reason = ''
            if bucket in {'other', 'low'} and roi <= -12.0:
                score_delta = -0.9
                reason = 'trim_non_core_after_losses'
            elif bucket in {'preferred', 'secondary'} and roi >= 6.0:
                score_delta = 0.45
                reason = 'trust_core_bucket_after_profit'
            if abs(score_delta) < 0.01:
                continue
            league_adjustments[bucket] = {
                'score_delta': round(score_delta, 3),
                'count': count,
                'roi_pct': round(roi, 2),
                'reason': reason,
            }
            actions.append({
                'scope': 'league_bucket',
                'key': bucket,
                'score_delta': round(score_delta, 3),
                'reason': reason,
            })

        actions.sort(key=lambda item: abs(float(item.get('score_delta') or 0.0)), reverse=True)
        return {
            'enabled': bool(self._setting('nightly_review_store_adjustments_enabled', True)),
            'report_date': str(daily_report.get('report_date') or ''),
            'settled_bets': len(settled),
            'family_adjustments': family_adjustments,
            'league_bucket_adjustments': league_adjustments,
            'actions': actions[:12],
        }

    def analyze_rows(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        settled = [row for row in rows if self._binary_result(row) is not None]
        lost = [row for row in settled if self._binary_result(row) == 0.0]
        tags: Counter[str] = Counter()
        for row in lost:
            for tag in self._failure_tags(row):
                tags[tag] += 1
        return {
            'settled_bets': len(settled),
            'lost_bets': len(lost),
            'top_failure_tags': dict(tags.most_common(12)),
            'by_family': {
                key: self._portfolio_summary(group)
                for key, group in self._group_rows(settled, lambda row: str(row.get('family') or 'unknown')).items()
            },
            'by_league_bucket': {
                key: self._portfolio_summary(group)
                for key, group in self._group_rows(
                    settled,
                    lambda row: self._league_bucket_from_text(str(row.get('league_name') or ''), str(row.get('match_tier') or '')),
                ).items()
            },
        }

    def export_quality_report(self, export_dir: str, report: dict[str, Any]) -> dict[str, str]:
        root = Path(export_dir)
        dated = root / datetime.now(UTC).strftime('%Y-%m-%d')
        rows = self._segment_rows_for_csv(report)
        learning_rows = self.learning_rows_for_csv(report)
        return {
            'quality_report_json': str(self._write_json(dated / 'quality-report.json', report)),
            'quality_segments_csv': str(self._write_csv(dated / 'quality-segments.csv', rows)),
            'quality_learning_csv': str(self._write_csv(dated / 'quality-learning.csv', learning_rows)),
            'latest_quality_report_json': str(self._write_json(root / 'latest-quality-report.json', report)),
            'latest_quality_segments_csv': str(self._write_csv(root / 'latest-quality-segments.csv', rows)),
            'latest_quality_learning_csv': str(self._write_csv(root / 'latest-quality-learning.csv', learning_rows)),
        }

    def _segment_stats(self, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            for segment in self._row_segments(row):
                grouped[segment].append(row)
        return {key: self._segment_summary(value) for key, value in sorted(grouped.items())}

    def _calibration_bucket_stats(self, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        grouped = self._group_rows(rows, lambda row: f"prob:{self._probability_bucket(self._probability(row))}")
        return {key: self._segment_summary(value) for key, value in sorted(grouped.items())}

    def _profile_from_stats(self, segment_stats: dict[str, dict[str, Any]], clv_stats: dict[str, Any]) -> dict[str, Any]:
        min_sample = max(1, int(self._setting('calibration_min_sample', 8) or 8))
        return {
            'min_sample': min_sample,
            'segments': {
                key: value
                for key, value in segment_stats.items()
                if int(value.get('count') or 0) >= min_sample
            },
            'clv_segments': dict(clv_stats.get('segments') or {}),
        }

    def _learning_summary(self, profile: dict[str, Any]) -> dict[str, Any]:
        min_sample = int(profile.get('min_sample') or self._setting('calibration_min_sample', 8) or 8)
        segments = dict(profile.get('segments') or {})
        positive: list[dict[str, Any]] = []
        negative: list[dict[str, Any]] = []
        for segment, stats in segments.items():
            if not isinstance(stats, dict):
                continue
            count = int(stats.get('count') or 0)
            if count < min_sample:
                continue
            delta = self._to_float(stats.get('calibration_delta_probability'))
            roi = self._to_float(stats.get('roi_pct'))
            if delta is None and roi is None:
                continue
            row = {
                'segment': str(segment),
                'count': count,
                'calibration_delta_probability': None if delta is None else round(float(delta), 4),
                'roi_pct': None if roi is None else round(float(roi), 2),
                'hit_rate_pct': self._to_float(stats.get('hit_rate_pct')),
                'avg_predicted_probability': self._to_float(stats.get('avg_predicted_probability')),
            }
            if (delta or 0.0) >= 0:
                positive.append(row)
            else:
                negative.append(row)
        positive.sort(
            key=lambda item: (
                float(item.get('calibration_delta_probability') or 0.0),
                float(item.get('roi_pct') or 0.0),
                int(item.get('count') or 0),
            ),
            reverse=True,
        )
        negative.sort(
            key=lambda item: (
                float(item.get('calibration_delta_probability') or 0.0),
                float(item.get('roi_pct') or 0.0),
                -int(item.get('count') or 0),
            ),
        )
        return {
            'enabled': bool(self._setting('quality_layer_enabled', True) and self._setting('calibration_enabled', True)),
            'min_sample': min_sample,
            'segments_tracked': len(segments),
            'positive_segments': positive[:15],
            'negative_segments': negative[:15],
        }

    def _segment_summary(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        probs = [self._probability(row) for row in rows if self._probability(row) is not None]
        outcomes = [self._binary_result(row) for row in rows if self._binary_result(row) is not None]
        avg_prob = mean(probs) if probs else 0.0
        hit_rate = mean(outcomes) if outcomes else 0.0
        summary = self._portfolio_summary(rows)
        summary.update({
            'avg_predicted_probability': round(avg_prob, 4),
            'hit_rate_probability': round(hit_rate, 4),
            'calibration_delta_probability': round(hit_rate - avg_prob, 4),
            'brier_score': self._brier_score(rows),
        })
        return summary

    def _portfolio_summary(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        stake = sum(self._to_float(row.get('stake_amount')) or 0.0 for row in rows)
        pnl = sum(self._to_float(row.get('pnl')) or 0.0 for row in rows if row.get('pnl') not in (None, ''))
        wins = sum(1 for row in rows if self._binary_result(row) == 1.0)
        losses = sum(1 for row in rows if self._binary_result(row) == 0.0)
        roi = (pnl / stake * 100.0) if stake > 0 else 0.0
        hit = (wins / max(wins + losses, 1) * 100.0) if wins + losses else 0.0
        odds = [self._to_float(row.get('odds')) or 0.0 for row in rows if (self._to_float(row.get('odds')) or 0.0) > 1.0]
        return {
            'count': len(rows),
            'wins': wins,
            'losses': losses,
            'stake': round(stake, 2),
            'pnl': round(pnl, 2),
            'roi_pct': round(roi, 2),
            'hit_rate_pct': round(hit, 2),
            'avg_odds': round(mean(odds), 3) if odds else 0.0,
        }

    def _backtest_policy(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        kept: list[dict[str, Any]] = []
        rejected: Counter[str] = Counter()
        for row in rows:
            reason = self._historical_policy_reject_reason(row)
            if reason:
                rejected[reason] += 1
            else:
                kept.append(row)
        return {
            'mode': 'published_bets_retrospective_filter',
            'baseline': self._portfolio_summary(rows),
            'quality_policy': self._portfolio_summary(kept),
            'rejected_by_reason': dict(rejected.most_common()),
            'kept_count': len(kept),
            'rejected_count': len(rows) - len(kept),
        }

    def _historical_policy_reject_reason(self, row: dict[str, Any]) -> str | None:
        if self._row_totals_contradiction(row):
            return 'market_sanity_contradiction'
        h2h_high_odds_reason = self._historical_high_odds_h2h_single_source_reason(row)
        if h2h_high_odds_reason:
            return h2h_high_odds_reason
        if (self._to_float(row.get('odds')) or 0.0) >= float(self._setting('quality_high_odds_min_odds', 3.40) or 3.40):
            if (self._to_float(row.get('confidence')) or 0.0) < float(self._setting('quality_high_odds_min_confidence', 68.0) or 68.0):
                return 'high_odds_low_confidence'
        if (self._to_float(row.get('edge_pct')) or 0.0) < float(self._setting('quality_backtest_min_edge_pct', 2.0) or 2.0):
            return 'thin_edge'
        if (self._to_float(row.get('ev_pct')) or 0.0) < float(self._setting('quality_backtest_min_ev_pct', 1.0) or 1.0):
            return 'thin_ev'
        return None

    def _candidate_calibration(self, segments: list[str], profile: dict[str, Any], enough_history: bool) -> dict[str, Any]:
        if not enough_history or not bool(self._setting('calibration_enabled', True)):
            return {'applied': False, 'delta_probability': 0.0, 'segments_used': []}
        stats = dict(profile.get('segments') or {})
        min_sample = int(profile.get('min_sample') or self._setting('calibration_min_sample', 8) or 8)
        weighted: list[tuple[float, float, str]] = []
        for segment in segments:
            item = stats.get(segment)
            if not isinstance(item, dict) or int(item.get('count') or 0) < min_sample:
                continue
            delta = self._to_float(item.get('calibration_delta_probability'))
            if delta is None:
                continue
            weight = min(40.0, float(item.get('count') or 0)) ** 0.5
            if segment.startswith('family:'):
                weight *= 1.25
            elif segment.startswith('market:'):
                weight *= 1.15
            weighted.append((float(delta), weight, segment))
        if not weighted:
            return {'applied': False, 'delta_probability': 0.0, 'segments_used': []}
        raw_delta = sum(delta * weight for delta, weight, _ in weighted) / max(sum(weight for _, weight, _ in weighted), 0.01)
        strength = float(self._setting('calibration_strength', 0.55) or 0.55)
        down_cap = float(self._setting('calibration_max_down_pct', 8.0) or 8.0) / 100.0
        up_cap = float(self._setting('calibration_max_up_pct', 1.5) or 1.5) / 100.0
        delta = clamp(raw_delta * strength, -down_cap, up_cap)
        return {
            'applied': abs(delta) >= 0.0025,
            'delta_probability': round(delta, 5),
            'raw_delta_probability': round(raw_delta, 5),
            'segments_used': [segment for _, _, segment in weighted[:8]],
        }

    def _candidate_learning_adjustment(self, segments: list[str], profile: dict[str, Any], enough_history: bool) -> dict[str, Any]:
        if not enough_history or not bool(self._setting('learning_score_adjustment_enabled', True)):
            return {'applied': False, 'score_delta': 0.0, 'segments_used': []}
        stats = dict(profile.get('segments') or {})
        clv_segments = dict(profile.get('clv_segments') or {})
        min_sample = max(1, int(self._setting('learning_score_min_sample', 10) or 10))
        weighted: list[tuple[float, float, str]] = []
        for segment in segments:
            item = stats.get(segment)
            if not isinstance(item, dict) or int(item.get('count') or 0) < min_sample:
                continue
            roi = self._to_float(item.get('roi_pct'))
            delta = self._to_float(item.get('calibration_delta_probability'))
            if roi is None and delta is None:
                continue
            segment_delta = 0.0
            if roi is not None:
                segment_delta += clamp(float(roi) * 0.08, -4.5, 2.5)
            if delta is not None:
                segment_delta += clamp(float(delta) * 26.0, -4.0, 1.8)
            clv_value = self._to_float((clv_segments.get(segment) or {}).get('avg_open_clv_pct'))
            if clv_value is not None:
                segment_delta += clamp(float(clv_value) * 0.45, -1.2, 1.2)
            weight = min(40.0, float(item.get('count') or 0)) ** 0.5
            if segment.startswith('family:'):
                weight *= 1.25
            elif segment.startswith(('market:', 'league:')):
                weight *= 1.1
            weighted.append((segment_delta, weight, segment))
        if not weighted:
            return {'applied': False, 'score_delta': 0.0, 'segments_used': []}
        raw_delta = sum(delta * weight for delta, weight, _ in weighted) / max(sum(weight for _, weight, _ in weighted), 0.01)
        strength = float(self._setting('learning_score_strength', 0.7) or 0.7)
        score_delta = clamp(raw_delta * strength, -8.0, 5.0)
        return {
            'applied': abs(score_delta) >= 0.35,
            'score_delta': round(score_delta, 3),
            'raw_score_delta': round(raw_delta, 3),
            'segments_used': [segment for _, _, segment in weighted[:8]],
        }

    def _candidate_recent_adjustment(self, candidate: CandidateBet, recent_learning_state: dict[str, Any]) -> dict[str, Any]:
        if not bool(self._setting('nightly_review_store_adjustments_enabled', True)):
            return {'applied': False, 'score_delta': 0.0, 'sources': []}
        payload = dict((recent_learning_state or {}).get('next_day_adjustments') or {})
        if not payload or not bool(payload.get('enabled', True)):
            return {'applied': False, 'score_delta': 0.0, 'sources': []}

        family_adjustments = dict(payload.get('family_adjustments') or {})
        league_adjustments = dict(payload.get('league_bucket_adjustments') or {})
        sources: list[str] = []
        score_delta = 0.0

        family_key = str(getattr(candidate, 'family', '') or '')
        family_payload = dict(family_adjustments.get(family_key) or {})
        family_delta = float(family_payload.get('score_delta') or 0.0)
        if abs(family_delta) >= 0.01:
            score_delta += family_delta
            sources.append(f'family:{family_key}')

        league_bucket = self._candidate_league_bucket(candidate)
        league_payload = dict(league_adjustments.get(league_bucket) or {})
        league_delta = float(league_payload.get('score_delta') or 0.0)
        if abs(league_delta) >= 0.01:
            score_delta += league_delta
            sources.append(f'league:{league_bucket}')

        score_delta = clamp(score_delta, -3.0, 2.0)
        return {
            'applied': abs(score_delta) >= 0.25,
            'score_delta': round(score_delta, 3),
            'sources': sources,
            'report_date': str(payload.get('report_date') or recent_learning_state.get('report_date') or ''),
        }

    def _apply_probability_adjustment(self, candidate: CandidateBet, delta: float) -> None:
        if abs(delta) < 0.0001:
            return
        adjusted = clamp(float(candidate.adjusted_probability) + float(delta), 0.02, 0.98)
        candidate.adjusted_probability = adjusted
        candidate.final_probability = adjusted
        candidate.fair_odds = 1.0 / max(adjusted, 0.01)
        candidate.edge_pct = (adjusted - float(candidate.market_probability)) * 100.0
        candidate.ev_pct = (adjusted * float(candidate.odds) - 1.0) * 100.0
        candidate.confidence = clamp(float(candidate.confidence) + min(1.0, max(-5.0, delta * 45.0)), 0.0, 100.0)
        candidate.publication_score = round(float(candidate.publication_score) + delta * 135.0, 3)
        candidate.reasons.append(f'historical_calibration={delta * 100.0:+.2f}pp')

    def _candidate_quality_score(self, candidate: CandidateBet, segments: list[str], profile: dict[str, Any], enough_history: bool) -> float:
        score = 43.0
        score += float(candidate.confidence) * 0.22
        score += max(0.0, float(candidate.edge_pct)) * 1.35
        score += max(0.0, float(candidate.ev_pct)) * 1.25
        score += min(8.0, max(0, int(candidate.books_count) - 1) * 3.0)
        score += min(5.0, max(0, int(candidate.sources_count) - 1) * 1.8)
        score += {'preferred': 4.0, 'secondary': 2.0, 'other': -2.0, 'low': -5.0}.get(self._candidate_league_bucket(candidate), 0.0)
        context_sources_count = self._candidate_context_sources_count(candidate)
        if context_sources_count <= 1:
            score -= float(self._setting('quality_single_source_context_penalty', 7.5) or 7.5)
        if self._candidate_market_contradiction(candidate):
            score -= 11.0
        if float(candidate.odds) >= float(self._setting('quality_high_odds_min_odds', 3.40) or 3.40):
            score -= 4.0
        if int(candidate.books_count) <= 1:
            score -= 3.0
        context_source = self._candidate_context_source(candidate)
        if context_sources_count <= 1:
            score = min(score, float(self._setting('quality_single_source_context_score_cap', 88.0) or 88.0))
        if context_source == 'sstats_form':
            score -= 6.0
        if candidate.family == 'totals':
            selection_kind = self._candidate_selection_kind(candidate)
            headroom = self._totals_xg_headroom(candidate)
            score -= 4.0
            if selection_kind == 'over':
                score -= 6.0
            elif headroom is not None and headroom > 0.0:
                score += clamp(headroom * 8.0, 0.0, 3.5)
            if self._odds_bucket(float(candidate.odds)) == '2.05-2.74':
                score -= 2.5 if selection_kind == 'under' else 4.0
            if int(candidate.books_count) <= 1:
                score -= 3.0
            elif selection_kind == 'under':
                score += 1.5
        if enough_history:
            segment_stats = dict(profile.get('segments') or {})
            roi_values = [
                self._to_float((segment_stats.get(segment) or {}).get('roi_pct'))
                for segment in segments
                if isinstance(segment_stats.get(segment), dict)
            ]
            roi_values = [value for value in roi_values if value is not None]
            if roi_values:
                score += clamp(mean(roi_values) * 0.12, -8.0, 4.0)
            clv_segments = dict(profile.get('clv_segments') or {})
            clv_values = [
                self._to_float((clv_segments.get(segment) or {}).get('avg_open_clv_pct'))
                for segment in segments
                if isinstance(clv_segments.get(segment), dict)
            ]
            clv_values = [value for value in clv_values if value is not None]
            if clv_values:
                score += clamp(mean(clv_values) * 0.9, -6.0, 4.0)
        return clamp(score, 0.0, 100.0)

    def _market_sanity_guard(self, candidate: CandidateBet) -> str | None:
        if not bool(self._setting('market_sanity_guard_enabled', True)):
            return None
        if candidate.family == 'totals' and self._candidate_market_contradiction(candidate):
            if not self._allow_market_sanity_override(candidate):
                return 'market_sanity_totals_xg_contradiction'
        if candidate.family == 'btts':
            expected = self._candidate_xg(candidate)
            if expected is None:
                return None
            home_xg, away_xg = expected
            selection_key = str(candidate.selection_key or '').lower()
            if selection_key == 'yes' and min(home_xg, away_xg) < float(self._setting('market_sanity_btts_yes_min_side_xg', 0.62) or 0.62):
                return 'market_sanity_btts_yes_side_xg_guard'
            if selection_key == 'no' and min(home_xg, away_xg) >= float(self._setting('market_sanity_btts_no_max_side_xg', 0.92) or 0.92):
                return 'market_sanity_btts_no_side_xg_guard'
        if candidate.family in {'dnb', 'spreads'} and self._candidate_side_xg_contradiction(candidate):
            return 'market_sanity_side_xg_contradiction'
        return None

    def _allow_market_sanity_override(self, candidate: CandidateBet) -> bool:
        if not bool(self._setting('market_sanity_override_enabled', False)):
            return False
        return (
            float(candidate.confidence) >= float(self._setting('market_sanity_override_min_confidence', 76.0) or 76.0)
            and float(candidate.edge_pct) >= float(self._setting('market_sanity_override_min_edge_pct', 10.0) or 10.0)
            and float(candidate.ev_pct) >= float(self._setting('market_sanity_override_min_ev_pct', 7.0) or 7.0)
            and int(candidate.books_count) >= int(self._setting('market_sanity_override_min_books', 3) or 3)
        )

    def _candidate_market_contradiction(self, candidate: CandidateBet) -> bool:
        if candidate.family != 'totals':
            return False
        expected = self._candidate_xg(candidate)
        point = self._to_float(candidate.point)
        if expected is None or point is None:
            return False
        total_xg = expected[0] + expected[1]
        kind = self._selection_kind(candidate.family, candidate.selection_key, candidate.selection)
        margin = float(self._setting('market_sanity_totals_xg_margin', 0.58) or 0.58)
        if kind == 'under' and point <= 1.5:
            margin = min(margin, float(self._setting('market_sanity_under15_xg_margin', 0.45) or 0.45))
        if kind == 'under':
            return total_xg >= point + margin
        if kind == 'over':
            return total_xg <= point - margin
        return False

    def _totals_xg_headroom(self, candidate: CandidateBet) -> float | None:
        if candidate.family != 'totals':
            return None
        expected = self._candidate_xg(candidate)
        point = self._to_float(candidate.point)
        if expected is None or point is None:
            return None
        total_xg = float(expected[0]) + float(expected[1])
        kind = self._candidate_selection_kind(candidate)
        if kind == 'under':
            return point - total_xg
        if kind == 'over':
            return total_xg - point
        return None

    def _clv_guard(self, segments: list[str], profile: dict[str, Any], enough_history: bool) -> str | None:
        if not enough_history or not bool(self._setting('clv_quality_guard_enabled', True)):
            return None
        clv_segments = dict(profile.get('clv_segments') or {})
        min_sample = max(1, int(self._setting('clv_quality_min_sample', 8) or 8))
        min_clv = float(self._setting('clv_quality_min_avg_pct', -2.0) or -2.0)
        for segment in segments:
            item = clv_segments.get(segment)
            if isinstance(item, dict) and int(item.get('count') or 0) >= min_sample:
                if (self._to_float(item.get('avg_open_clv_pct')) or 0.0) < min_clv:
                    return 'negative_clv_segment_guard'
        return None

    def _historical_segment_guard(self, candidate: CandidateBet, segments: list[str], profile: dict[str, Any], enough_history: bool) -> str | None:
        if not enough_history or not bool(self._setting('historical_segment_guard_enabled', True)):
            return None
        stats = dict(profile.get('segments') or {})
        if candidate.family == 'totals':
            selection_kind = self._candidate_selection_kind(candidate)
            context_source = self._candidate_context_source(candidate)
            odds_bucket = self._odds_bucket(float(candidate.odds))
            if (
                selection_kind == 'over'
                and self._segment_is_deep_negative(stats.get('market:totals:over'), min_count=5, max_roi=-80.0, max_delta=-0.30)
            ):
                return 'historical_totals_over_guard'
            if (
                context_source == 'sstats_form'
                and self._segment_is_deep_negative(stats.get('context:sstats_form'), min_count=6, max_roi=-45.0, max_delta=-0.20)
            ):
                return 'historical_context_sstats_form_guard'
            if (
                odds_bucket == '2.05-2.74'
                and self._segment_is_deep_negative(stats.get('odds:2.05-2.74'), min_count=6, max_roi=-80.0, max_delta=-0.30)
            ):
                return 'historical_mid_odds_totals_guard'
        min_sample = max(1, int(self._setting('historical_segment_min_sample', 10) or 10))
        min_roi = float(self._setting('historical_segment_min_roi_pct', -18.0) or -18.0)
        min_delta = float(self._setting('historical_segment_min_calibration_delta_pct', -9.0) or -9.0) / 100.0
        hard_roi = float(self._setting('historical_segment_hard_min_roi_pct', -26.0) or -26.0)
        hard_delta = float(self._setting('historical_segment_hard_min_calibration_delta_pct', -12.0) or -12.0) / 100.0
        hard_bad_segments = max(1, int(self._setting('historical_segment_hard_min_bad_segments', 2) or 2))
        bad_segments = 0
        hard_hits: list[tuple[str, float, float]] = []
        for segment in segments:
            if segment.startswith('prob:'):
                continue
            item = stats.get(segment)
            if not isinstance(item, dict) or int(item.get('count') or 0) < min_sample:
                continue
            roi = self._to_float(item.get('roi_pct')) or 0.0
            delta = self._to_float(item.get('calibration_delta_probability')) or 0.0
            if roi < min_roi and delta < min_delta:
                bad_segments += 1
                if roi < hard_roi and delta < hard_delta:
                    hard_hits.append((segment, roi, delta))
        if self._allow_late_window_historical_override(candidate, bad_segments, hard_hits):
            candidate.source_summary['historical_segment_override'] = 'late_window_soft' if not hard_hits else 'late_window_single_hard'
            candidate.source_summary['historical_bad_segments'] = [item[0] for item in hard_hits] if hard_hits else bad_segments
            candidate.diagnostics.setdefault('quality_historical_override', {
                'enabled': True,
                'bad_segments': bad_segments,
                'hard_segments': [item[0] for item in hard_hits],
            })
            return None
        if hard_hits:
            return 'bad_historical_segment_guard'
        if bad_segments >= hard_bad_segments:
            return 'bad_historical_segment_guard'
        return None

    def _segment_is_deep_negative(
        self,
        stats: dict[str, Any] | None,
        *,
        min_count: int,
        max_roi: float,
        max_delta: float,
    ) -> bool:
        if not isinstance(stats, dict):
            return False
        if int(stats.get('count') or 0) < min_count:
            return False
        roi = self._to_float(stats.get('roi_pct'))
        delta = self._to_float(stats.get('calibration_delta_probability'))
        if roi is None or delta is None:
            return False
        return roi <= max_roi and delta <= max_delta

    def _allow_late_window_historical_override(self, candidate: CandidateBet, bad_segments: int, hard_hits: list[tuple[str, float, float]]) -> bool:
        hours_to_start = max(0.0, (candidate.commence_time - datetime.now(UTC)).total_seconds() / 3600.0)
        publish_window_hours = float(self._setting('publish_window_hours', 12) or 12)
        late_window_limit = min(6.0, max(3.0, publish_window_hours))
        if hours_to_start > late_window_limit:
            return False
        league_bucket = self._candidate_league_bucket(candidate)
        if league_bucket not in {'preferred', 'secondary'}:
            return False
        if (self._to_float(getattr(candidate, 'confidence', None)) or 0.0) < 59.0:
            return False
        if (self._to_float(getattr(candidate, 'ev_pct', None)) or 0.0) < 1.5:
            return False
        if (self._to_float(getattr(candidate, 'edge_pct', None)) or 0.0) < 1.5:
            return False
        if (self._to_float(getattr(candidate, 'adjusted_probability', None)) or 0.0) < 0.52:
            return False
        if (self._to_float(getattr(candidate, 'quality_score', None)) or 100.0) < 55.0:
            return False
        books_count = int(self._to_float(getattr(candidate, 'books_count', None)) or 0)
        if books_count <= 0:
            books_count = int(bool((getattr(candidate, 'source_summary', {}) or {}).get('effective_book_support')))
        if books_count <= 0:
            return False
        if not hard_hits and bad_segments <= 1:
            return True
        if len(hard_hits) == 1:
            _, roi, delta = hard_hits[0]
            if roi >= (float(self._setting('historical_segment_hard_min_roi_pct', -26.0) or -26.0) - 4.0) and delta >= ((float(self._setting('historical_segment_hard_min_calibration_delta_pct', -12.0) or -12.0) - 2.0) / 100.0):
                return True
        return False

    def _historical_high_odds_h2h_single_source_reason(self, row: dict[str, Any]) -> str | None:
        if not bool(self._setting('quality_high_odds_h2h_single_source_guard_enabled', True)):
            return None
        if str(row.get('family') or '') != 'h2h':
            return None
        selection_kind = self._selection_kind(
            str(row.get('family') or ''),
            str(row.get('selection_key') or ''),
            str(row.get('selection') or ''),
        )
        if selection_kind == 'draw':
            return None
        if (self._to_float(row.get('odds')) or 0.0) < float(self._setting('quality_high_odds_h2h_single_source_min_odds', 2.80) or 2.80):
            return None
        if int(self._to_float(row.get('sources_count')) or 0) > int(self._setting('quality_high_odds_h2h_single_source_max_sources', 1) or 1):
            return None
        if int(self._to_float(row.get('books_count')) or 0) > int(self._setting('quality_high_odds_h2h_single_source_max_books', 2) or 2):
            return None
        model_probability = self._to_float(row.get('model_probability'))
        adjusted_probability = self._to_float(row.get('adjusted_probability'))
        if adjusted_probability is None:
            adjusted_probability = self._to_float(row.get('final_probability'))
        if model_probability is None or adjusted_probability is None:
            return None
        shrink_pp = max(0.0, (float(model_probability) - float(adjusted_probability)) * 100.0)
        if shrink_pp < float(self._setting('quality_high_odds_h2h_single_source_min_shrink_pp', 9.0) or 9.0):
            return None
        return 'high_odds_h2h_single_source_shrink'

    def _quarantine_guard(self, candidate: CandidateBet, segments: list[str], profile: dict[str, Any], enough_history: bool) -> str | None:
        if not enough_history or not bool(self._setting('quarantine_shadow_mode_enabled', True)):
            return None
        league_bucket = self._candidate_league_bucket(candidate)
        buckets = {str(item).strip().lower() for item in (self._setting('quarantine_league_buckets', []) or [])}
        if league_bucket not in buckets and not str(candidate.model_mode or '').startswith('market_simple'):
            return None
        stats = dict(profile.get('segments') or {})
        min_sample = max(1, int(self._setting('quarantine_min_segment_sample', 16) or 16))
        for segment in segments:
            if segment.startswith(('family:', 'market:', 'league:', 'context:')):
                item = stats.get(segment)
                if isinstance(item, dict) and int(item.get('count') or 0) >= min_sample:
                    return None
        return 'quarantine_shadow_insufficient_history'

    def _high_odds_h2h_single_source_guard(self, candidate: CandidateBet) -> str | None:
        if not bool(self._setting('quality_high_odds_h2h_single_source_guard_enabled', True)):
            return None
        if str(candidate.family or '') != 'h2h':
            return None
        if self._candidate_selection_kind(candidate) == 'draw':
            return None
        if float(candidate.odds) < float(self._setting('quality_high_odds_h2h_single_source_min_odds', 2.80) or 2.80):
            return None
        if int(candidate.sources_count) > int(self._setting('quality_high_odds_h2h_single_source_max_sources', 1) or 1):
            return None
        if int(candidate.books_count) > int(self._setting('quality_high_odds_h2h_single_source_max_books', 2) or 2):
            return None
        shrink_pp = max(0.0, (float(candidate.model_probability) - float(candidate.adjusted_probability)) * 100.0)
        if shrink_pp < float(self._setting('quality_high_odds_h2h_single_source_min_shrink_pp', 9.0) or 9.0):
            return None
        return 'quality_high_odds_h2h_single_source_shrink_guard'

    def _high_odds_guard(self, candidate: CandidateBet, quality_score: float) -> str | None:
        if not bool(self._setting('quality_high_odds_guard_enabled', True)):
            return None
        min_odds = float(self._setting('quality_high_odds_min_odds', 3.40) or 3.40)
        market_max = float(self._setting('quality_high_odds_market_max_prob', 0.31) or 0.31)
        if float(candidate.odds) < min_odds and float(candidate.market_probability) > market_max:
            return None
        h2h_single_source_reason = self._high_odds_h2h_single_source_guard(candidate)
        if h2h_single_source_reason:
            return h2h_single_source_reason
        min_score = float(self._setting('quality_high_odds_min_score', 68.0) or 68.0)
        if quality_score < min_score:
            if self._passes_high_odds_score_relief(candidate, quality_score, min_score):
                candidate.reasons.append(
                    f'quality=high_odds_score_relief(gap={max(0.0, min_score - quality_score):.1f})'
                )
                candidate.source_summary['high_odds_score_relief'] = {
                    'quality_score': round(float(quality_score), 3),
                    'min_score': round(float(min_score), 3),
                    'gap': round(max(0.0, float(min_score) - float(quality_score)), 3),
                }
            else:
                return 'quality_high_odds_score_guard'
        min_confidence = float(self._setting('quality_high_odds_min_confidence', 68.0) or 68.0)
        confidence_relief = 0.0
        if float(candidate.ev_pct) >= float(self._setting('quality_high_odds_relief_min_ev_pct', 12.0) or 12.0):
            confidence_relief += float(self._setting('quality_high_odds_relief_ev_points', 3.0) or 3.0)
        if float(candidate.edge_pct) >= float(self._setting('quality_high_odds_relief_min_edge_pct', 8.0) or 8.0):
            confidence_relief += float(self._setting('quality_high_odds_relief_edge_points', 2.0) or 2.0)
        if int(candidate.books_count) >= int(self._setting('quality_high_odds_relief_min_books', 3) or 3):
            confidence_relief += float(self._setting('quality_high_odds_relief_books_points', 2.0) or 2.0)
        if quality_score >= float(self._setting('quality_high_odds_relief_min_quality_score', 70.0) or 70.0):
            confidence_relief += float(self._setting('quality_high_odds_relief_quality_points', 1.0) or 1.0)

        if float(candidate.confidence) + confidence_relief < min_confidence:
            return 'quality_high_odds_confidence_guard'
        if int(candidate.books_count) < int(self._setting('quality_high_odds_min_books', 2) or 2):
            return 'quality_high_odds_books_guard'
        if float(candidate.edge_pct) < float(self._setting('quality_high_odds_min_edge_pct', 6.0) or 6.0):
            return 'quality_high_odds_edge_guard'
        if float(candidate.ev_pct) < float(self._setting('quality_high_odds_min_ev_pct', 4.0) or 4.0):
            return 'quality_high_odds_ev_guard'
        if candidate.family == 'totals':
            expected = self._candidate_xg(candidate)
            point = self._to_float(candidate.point)
            if expected is None or point is None:
                return 'quality_high_odds_totals_xg_missing_guard'
            total_xg = float(expected[0]) + float(expected[1])
            kind = self._selection_kind(candidate.family, candidate.selection_key, candidate.selection)
            min_headroom = float(self._setting('quality_high_odds_totals_min_xg_headroom', 0.18) or 0.18)
            if kind == 'under' and (point - total_xg) < min_headroom:
                return 'quality_high_odds_totals_xg_headroom_guard'
            if kind == 'over' and (total_xg - point) < min_headroom:
                return 'quality_high_odds_totals_xg_headroom_guard'
        return None

    def _passes_high_odds_score_relief(
        self,
        candidate: CandidateBet,
        quality_score: float,
        min_score: float,
    ) -> bool:
        if not bool(self._setting('quality_high_odds_score_relief_enabled', True)):
            return False
        gap = float(min_score) - float(quality_score)
        if gap <= 0.0:
            return True
        if gap > float(self._setting('quality_high_odds_score_relief_max_gap', 4.5) or 4.5):
            return False
        if not self._is_core_league_candidate(candidate):
            return False
        if self._candidate_context_source(candidate) == 'sstats_form':
            return False
        if self._candidate_market_contradiction(candidate):
            return False
        min_books = max(1, int(self._setting('quality_high_odds_score_relief_min_books', 1) or 1))
        if int(candidate.books_count) < min_books:
            return False
        if float(candidate.confidence) < float(self._setting('quality_high_odds_score_relief_min_confidence', 60.0) or 60.0):
            return False
        if float(candidate.edge_pct) < float(self._setting('quality_high_odds_score_relief_min_edge_pct', 3.0) or 3.0):
            return False
        if float(candidate.ev_pct) < float(self._setting('quality_high_odds_score_relief_min_ev_pct', 1.5) or 1.5):
            return False
        if float(getattr(candidate, 'publication_score', 0.0) or 0.0) < float(
            self._setting('quality_high_odds_score_relief_min_publication_score', 10.0) or 10.0
        ):
            return False
        min_probability = self._post_calibration_probability_threshold(candidate)
        if float(candidate.adjusted_probability) < max(
            min_probability,
            float(self._setting('quality_high_odds_score_relief_min_probability', 0.0) or 0.0),
        ):
            return False
        if candidate.family == 'totals':
            headroom = self._totals_xg_headroom(candidate)
            if headroom is None:
                return False
            selection_kind = self._candidate_selection_kind(candidate)
            min_headroom = float(
                self._setting(
                    'quality_high_odds_score_relief_totals_under_min_xg_headroom'
                    if selection_kind == 'under'
                    else 'quality_high_odds_score_relief_totals_over_min_xg_headroom',
                    0.06 if selection_kind == 'under' else 0.16,
                ) or (0.06 if selection_kind == 'under' else 0.16)
            )
            if headroom < min_headroom:
                return False
        return True

    def _post_calibration_threshold_guard(self, candidate: CandidateBet) -> str | None:
        min_probability = float(self._post_calibration_probability_threshold(candidate))
        adjusted_probability = float(candidate.adjusted_probability)
        if adjusted_probability < min_probability:
            probability_gap = min_probability - adjusted_probability
            max_gap_relief = float(self._setting('post_calibration_probability_relief_max_gap', 0.09) or 0.09)
            min_conf_relief = float(self._setting('post_calibration_probability_relief_min_confidence', 58.0) or 58.0)
            min_edge_relief = float(self._setting('post_calibration_probability_relief_min_edge_pct', 6.5) or 6.5)
            min_ev_relief = float(self._setting('post_calibration_probability_relief_min_ev_pct', 5.0) or 5.0)
            min_books_relief = max(1, int(self._setting('post_calibration_probability_relief_min_books', 1) or 1))
            if (
                probability_gap <= max_gap_relief
                and float(candidate.confidence) >= min_conf_relief
                and float(candidate.edge_pct) >= min_edge_relief
                and float(candidate.ev_pct) >= min_ev_relief
                and int(candidate.books_count) >= min_books_relief
            ):
                candidate.reasons.append(
                    f'quality=post_calibration_probability_relief(gap={probability_gap * 100.0:.1f}pp)'
                )
                candidate.source_summary['post_calibration_probability_relief'] = {
                    'gap_pp': round(probability_gap * 100.0, 3),
                    'min_probability': round(min_probability, 4),
                    'adjusted_probability': round(adjusted_probability, 4),
                    'profile': 'standard',
                }
            elif self._passes_post_calibration_core_relief(candidate, probability_gap):
                candidate.reasons.append(
                    f'quality=post_calibration_core_relief(gap={probability_gap * 100.0:.1f}pp)'
                )
                candidate.source_summary['post_calibration_probability_relief'] = {
                    'gap_pp': round(probability_gap * 100.0, 3),
                    'min_probability': round(min_probability, 4),
                    'adjusted_probability': round(adjusted_probability, 4),
                    'profile': 'core_market',
                }
            else:
                return 'post_calibration_probability_guard'
        if float(candidate.edge_pct) < float(self.settings.min_edge_pct_for_family(candidate.family)):
            return 'post_calibration_edge_guard'
        if float(candidate.ev_pct) < float(self.settings.min_ev_pct_for_family(candidate.family)):
            return 'post_calibration_ev_guard'
        return None

    def _post_calibration_probability_threshold(self, candidate: CandidateBet) -> float:
        family = str(getattr(candidate, 'family', '') or '')
        if family == 'h2h':
            selection_kind = self._candidate_selection_kind(candidate)
            if selection_kind == 'draw':
                return float(self._setting('h2h_draw_min_probability', 0.20) or 0.20)
            return float(self._setting('h2h_side_min_probability', 0.24) or 0.24)
        if family == 'doubleChance':
            return float(self._setting('double_chance_min_probability', 0.46) or 0.46)
        if family == 'dnb':
            return float(self._setting('dnb_min_probability', 0.38) or 0.38)
        if family == 'spreads':
            return float(self._setting('spreads_min_probability', 0.49) or 0.49)
        if family == 'totals':
            return float(self._setting('totals_min_probability', 0.49) or 0.49)
        if family == 'teamTotals':
            return float(self._setting('team_totals_min_probability', 0.50) or 0.50)
        if family == 'btts':
            return float(self._setting('btts_min_probability', 0.50) or 0.50)
        return float(self.settings.min_model_confidence_for_family(family))

    def _passes_post_calibration_core_relief(self, candidate: CandidateBet, probability_gap: float) -> bool:
        if not bool(self._setting('post_calibration_core_relief_enabled', True)):
            return False
        if probability_gap > float(self._setting('post_calibration_core_relief_max_gap', 0.12) or 0.12):
            return False
        if not self._is_core_league_candidate(candidate):
            return False
        if int(candidate.books_count) < max(2, int(self._setting('post_calibration_core_relief_min_books', 2) or 2)):
            return False
        if self._candidate_context_source(candidate) == 'sstats_form':
            return False
        if self._candidate_market_contradiction(candidate):
            return False
        if float(candidate.confidence) < float(self._setting('post_calibration_core_relief_min_confidence', 58.0) or 58.0):
            return False
        if float(candidate.edge_pct) < float(self._setting('post_calibration_core_relief_min_edge_pct', 2.8) or 2.8):
            return False
        if float(candidate.ev_pct) < float(self._setting('post_calibration_core_relief_min_ev_pct', 1.1) or 1.1):
            return False
        if float(candidate.odds) >= float(self._setting('quality_high_odds_min_odds', 3.40) or 3.40) and float(candidate.confidence) < 66.0:
            return False
        if candidate.family == 'totals':
            headroom = self._totals_xg_headroom(candidate)
            if headroom is None:
                return False
            selection_kind = self._candidate_selection_kind(candidate)
            min_headroom = float(
                self._setting(
                    'post_calibration_core_relief_totals_under_min_xg_headroom' if selection_kind == 'under' else 'post_calibration_core_relief_totals_over_min_xg_headroom',
                    0.06 if selection_kind == 'under' else 0.16,
                ) or (0.06 if selection_kind == 'under' else 0.16)
            )
            if headroom < min_headroom:
                return False
            if selection_kind == 'over' and float(candidate.confidence) < 61.0:
                return False
        return True

    def _clv_segment_stats(self, rows: list[dict[str, Any]]) -> dict[str, Any]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        values: list[float] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            open_clv = self._to_float(row.get('open_clv_pct'))
            if open_clv is not None:
                values.append(open_clv)
            family = str(row.get('family') or 'unknown')
            key = self._selection_kind(
                family,
                str(row.get('selection_key') or ''),
                str(row.get('selection') or ''),
            ) or 'unknown'
            context = str(row.get('context_source') or 'unknown')
            for segment in (f'family:{family}', f'market:{family}:{key}', f'context:{context}'):
                grouped[segment].append(row)
        segments: dict[str, Any] = {}
        for segment, group in grouped.items():
            clv_values = [self._to_float(row.get('open_clv_pct')) for row in group]
            clv_values = [value for value in clv_values if value is not None]
            segments[segment] = {
                'count': len(group),
                'avg_open_clv_pct': round(mean(clv_values), 3) if clv_values else None,
            }
        return {
            'count': len(rows),
            'avg_open_clv_pct': round(mean(values), 3) if values else None,
            'segments': segments,
        }

    def _row_segments(self, row: dict[str, Any]) -> list[str]:
        family = str(row.get('family') or 'unknown')
        selection_key = self._selection_kind(
            family,
            str(row.get('selection_key') or ''),
            str(row.get('selection') or ''),
        ) or 'unknown'
        context = str(row.get('context_source') or 'unknown')
        bookmaker = str(row.get('selected_bookmaker') or 'unknown').lower()
        league_bucket = self._league_bucket_from_text(str(row.get('league_name') or ''), str(row.get('match_tier') or ''))
        return [
            f'family:{family}',
            f'market:{family}:{selection_key}',
            f'league:{league_bucket}',
            f'context:{context}',
            f'bookmaker:{bookmaker}',
            f'odds:{self._odds_bucket(self._to_float(row.get("odds")))}',
            f'prob:{self._probability_bucket(self._probability(row))}',
        ]

    def _candidate_segments(self, candidate: CandidateBet) -> list[str]:
        source_summary = dict(getattr(candidate, 'source_summary', {}) or {})
        context = self._candidate_context_source(candidate)
        bookmaker = str(source_summary.get('selected_bookmaker') or 'unknown').lower()
        selection_key = str(
            candidate.selection_key
            or candidate_selection_key(
                str(candidate.family or ''),
                str(candidate.selection or ''),
                point=candidate.point,
                team_side=getattr(candidate, 'team_side', None),
                home_team=str(candidate.home_team or ''),
                away_team=str(candidate.away_team or ''),
            )
        ).lower()
        return [
            f'family:{candidate.family}',
            f'market:{candidate.family}:{selection_key}',
            f'league:{self._candidate_league_bucket(candidate)}',
            f'context:{context}',
            f'bookmaker:{bookmaker}',
            f'odds:{self._odds_bucket(float(candidate.odds))}',
            f'prob:{self._probability_bucket(float(candidate.adjusted_probability))}',
        ]


    def _candidate_context_sources_count(self, candidate: CandidateBet) -> int:
        source_summary = dict(getattr(candidate, 'source_summary', {}) or {})
        explicit = source_summary.get('context_sources_count')
        try:
            if explicit not in (None, ''):
                return max(0, int(explicit))
        except Exception:
            pass
        sources = [str(item).strip() for item in (source_summary.get('context_sources') or []) if str(item).strip()]
        if sources:
            return len(set(sources))
        source = str(source_summary.get('context_source') or '').strip()
        return 1 if source else 0

    def _candidate_side_xg_contradiction(self, candidate: CandidateBet) -> bool:
        if not bool(self._setting('xg_side_sanity_guard_enabled', True)):
            return False
        expected = self._candidate_xg(candidate)
        if expected is None:
            return False
        home_xg, away_xg = expected
        diff = float(home_xg) - float(away_xg)
        min_diff = float(self._setting('xg_side_sanity_min_diff', 0.52) or 0.52)
        if abs(diff) < min_diff:
            return False
        selected_side = str(candidate.team_side or candidate.selection_key or '').strip().lower()
        if selected_side not in {'home', 'away'}:
            return False
        stronger_side = 'home' if diff > 0 else 'away'
        if selected_side == stronger_side:
            return False
        if candidate.family == 'dnb':
            return True
        if candidate.family == 'spreads':
            if bool(self._setting('xg_side_sanity_zero_line_only', True)):
                point = self._to_float(candidate.point)
                return point is None or abs(point) <= 0.01
            return True
        return False

    def _candidate_context_source(self, candidate: CandidateBet) -> str:
        source_summary = dict(getattr(candidate, 'source_summary', {}) or {})
        return str(source_summary.get('context_source') or 'unknown')

    def _candidate_selection_kind(self, candidate: CandidateBet) -> str:
        return self._selection_kind(
            str(candidate.family or ''),
            str(candidate.selection_key or ''),
            str(candidate.selection or ''),
        )

    def _is_core_league_candidate(self, candidate: CandidateBet) -> bool:
        return self._candidate_league_bucket(candidate) in {'preferred', 'secondary'}

    def _candidate_league_bucket(self, candidate: CandidateBet) -> str:
        source_summary = dict(getattr(candidate, 'source_summary', {}) or {})
        return self._league_bucket_from_text(str(candidate.league_name or ''), str(source_summary.get('match_tier') or ''))

    def _league_bucket_from_text(self, league_name: str, tier: str = '') -> str:
        if str(tier).lower() == 'low':
            return 'low'
        league = str(league_name or '').lower()
        if any(str(term).lower() in league for term in (self._setting('preferred_league_terms', []) or [])):
            return 'preferred'
        if any(str(term).lower() in league for term in (self._setting('secondary_league_terms', []) or [])):
            return 'secondary'
        if any(term in league for term in ('u17', 'u19', 'u20', 'u21', 'u23', 'reserve', 'youth', 'women', 'amateur', 'regional')):
            return 'low'
        return 'other'

    def _failure_tags(self, row: dict[str, Any]) -> list[str]:
        tags: list[str] = []
        source_summary = dict(row.get('source_summary') or {})
        quality_status = str(row.get('quality_status') or source_summary.get('quality_status') or '').strip().lower()
        quality_reasons = [str(item).strip().lower() for item in (row.get('quality_reasons') or source_summary.get('quality_reasons') or [])]
        context_source = str(row.get('context_source') or source_summary.get('context_source') or '').strip().lower()
        family = str(row.get('family') or '').strip().lower()
        selection_kind = self._selection_kind(family, str(row.get('selection_key') or ''), str(row.get('selection') or ''))
        if self._row_totals_contradiction(row):
            tags.append('xg_contradiction')
        if (self._to_float(row.get('odds')) or 0.0) >= float(self._setting('quality_high_odds_min_odds', 3.40) or 3.40):
            tags.append('high_odds')
        if int(self._to_float(row.get('books_count')) or 0) <= 1:
            tags.append('single_book')
        if family == 'totals' and selection_kind == 'over':
            tags.append('totals_over')
        if context_source == 'sstats_form':
            tags.append('sstats_form')
        if 'emergency' in quality_status or 'quality_emergency_publish' in quality_reasons:
            tags.append('quality_emergency')
        if 'last_resort' in quality_status or 'quality_last_resort_publish' in quality_reasons:
            tags.append('quality_last_resort')
        if 'historical_relief' in quality_status or 'quality_historical_guard_relief' in quality_reasons:
            tags.append('historical_relief')
        if self._league_bucket_from_text(str(row.get('league_name') or ''), str(row.get('match_tier') or '')) in {'other', 'low'}:
            tags.append('non_core_league')
        if (self._to_float(row.get('confidence')) or 0.0) < 60.0:
            tags.append('low_confidence')
        if (self._to_float(row.get('ev_pct')) or 0.0) < 2.0:
            tags.append('thin_ev')
        if str(row.get('model_mode') or '').startswith('market_simple'):
            tags.append('market_simple_mode')
        return tags or ['uncategorized']

    def _row_totals_contradiction(self, row: dict[str, Any]) -> bool:
        if str(row.get('family') or '') != 'totals':
            return False
        total_xg = self._to_float(row.get('total_xg'))
        if total_xg is None:
            home_xg = self._to_float(row.get('expected_home'))
            away_xg = self._to_float(row.get('expected_away'))
            if home_xg is None or away_xg is None:
                return False
            total_xg = home_xg + away_xg
        point = self._to_float(row.get('point'))
        if point is None:
            return False
        kind = self._selection_kind(str(row.get('family') or ''), str(row.get('selection_key') or ''), str(row.get('selection') or ''))
        margin = float(self._setting('market_sanity_totals_xg_margin', 0.58) or 0.58)
        if kind == 'under' and point <= 1.5:
            margin = min(margin, float(self._setting('market_sanity_under15_xg_margin', 0.45) or 0.45))
        if kind == 'under':
            return total_xg >= point + margin
        if kind == 'over':
            return total_xg <= point - margin
        return False

    def _candidate_xg(self, candidate: CandidateBet) -> tuple[float, float] | None:
        if candidate.expected_home is None or candidate.expected_away is None:
            return None
        return float(candidate.expected_home), float(candidate.expected_away)

    @staticmethod
    def _selection_kind(family: str, selection_key: str, selection: str) -> str:
        key = str(selection_key or '').strip().lower()
        raw = str(selection or '').strip().lower()
        if family == 'totals':
            if key in {'over', 'under'}:
                return key
            if any(token in raw for token in ('over', 'больше', 'тб')):
                return 'over'
            if any(token in raw for token in ('under', 'меньше', 'тм')):
                return 'under'
        if family == 'btts' and key in {'yes', 'no'}:
            return key
        return key or raw

    @staticmethod
    def _group_rows(rows: list[dict[str, Any]], key_fn: Any) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in rows:
            grouped[str(key_fn(row))].append(row)
        return grouped

    @staticmethod
    def _binary_result(row: dict[str, Any]) -> float | None:
        status = str(row.get('status') or row.get('result') or '').lower()
        result = str(row.get('result') or '').lower()
        value = result or status
        if value in {'won', 'half_won'}:
            return 1.0
        if value in {'lost', 'half_lost'}:
            return 0.0
        hit = row.get('is_hit')
        if hit is True or str(hit).lower() == 'true':
            return 1.0
        if hit is False or str(hit).lower() == 'false':
            return 0.0
        return None

    def _probability(self, row: dict[str, Any]) -> float | None:
        for key in ('adjusted_probability_pct', 'model_probability_pct', 'market_probability_pct'):
            value = self._to_float(row.get(key))
            if value is not None:
                return clamp(value / 100.0 if value > 1.0 else value, 0.01, 0.99)
        return None

    def _brier_score(self, rows: list[dict[str, Any]]) -> float | None:
        valid = [
            (self._probability(row), self._binary_result(row))
            for row in rows
        ]
        pairs = [(p, y) for p, y in valid if p is not None and y is not None]
        if not pairs:
            return None
        return round(mean((float(p) - float(y)) ** 2 for p, y in pairs), 5)

    @staticmethod
    def _odds_bucket(value: float | None) -> str:
        if value is None:
            return 'unknown'
        if value < 1.7:
            return '1.00-1.69'
        if value < 2.05:
            return '1.70-2.04'
        if value < 2.75:
            return '2.05-2.74'
        if value < 3.5:
            return '2.75-3.49'
        return '3.50+'

    @staticmethod
    def _probability_bucket(value: float | None) -> str:
        if value is None:
            return 'unknown'
        pct = int(max(0, min(95, math.floor(float(value) * 100.0 / 5.0) * 5)))
        return f'{pct:02d}-{pct + 5:02d}'

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value in (None, ''):
                return None
            number = float(value)
            if math.isnan(number) or math.isinf(number):
                return None
            return number
        except Exception:
            return None

    @staticmethod
    def _write_json(path: Path, payload: Any) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
        return path

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]]) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        headers: list[str] = []
        for row in rows:
            for key in row.keys():
                if key not in headers:
                    headers.append(key)
        with path.open('w', newline='', encoding='utf-8') as handle:
            if not headers:
                handle.write('')
                return path
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: PredictionQualityService._csv_value(row.get(key)) for key in headers})
        return path

    @staticmethod
    def _csv_value(value: Any) -> Any:
        if value is None:
            return ''
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return value

    @staticmethod
    def _segment_rows_for_csv(report: dict[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for name, stats in dict(report.get('segments') or {}).items():
            if isinstance(stats, dict):
                rows.append({'segment': name, **stats})
        return rows

    @staticmethod
    def learning_rows_for_csv(report: dict[str, Any]) -> list[dict[str, Any]]:
        learning = dict(report.get('learning') or {})
        rows: list[dict[str, Any]] = []
        for polarity, key in (('positive', 'positive_segments'), ('negative', 'negative_segments')):
            for item in (learning.get(key) or []):
                if isinstance(item, dict):
                    rows.append({'polarity': polarity, **item})
        return rows
