from __future__ import annotations

import math
from collections import Counter, defaultdict
from statistics import mean
from typing import Any

from app.config import Settings
from app.schemas import CandidateBet, Match, MatchContext, Offer
from app.utils import (
    clamp,
    implied_probability,
    poisson_over_probability,
    russian_selection,
    shrink_probability,
    strip_vig_three_way,
    strip_vig_two_way,
)

BOOKMAKER_WEIGHTS = {
    'Pinnacle': 1.20,
    'Betfair': 1.15,
    'Bet365': 1.08,
    'Unibet': 1.03,
}


class CandidateFactory:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.target_books = {self._norm_book(item) for item in settings.target_bookmakers}
        self.consensus_books = {self._norm_book(item) for item in settings.consensus_bookmakers}
        self._market_signals_by_match: dict[str, dict[str, Any]] = {}

    def build_candidates(
        self,
        matches: list[Match],
        offers_by_match: dict[str, list[Offer]],
        contexts_by_match: dict[str, Any],
        market_signals_by_match: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[list[CandidateBet], dict[str, int], dict[str, Any]]:
        candidates: list[CandidateBet] = []
        rejections: dict[str, int] = defaultdict(int)
        debug_rows: list[dict[str, Any]] = []
        self._market_signals_by_match = market_signals_by_match or {}
        matches_by_key = {m.match_key: m for m in matches}

        for match_key, offers in offers_by_match.items():
            match = matches_by_key.get(match_key)
            if not match:
                rejections['match_not_found'] += 1
                continue
            context = self._coerce_context(contexts_by_match.get(match_key))
            families: dict[str, list[Offer]] = defaultdict(list)
            for offer in offers:
                if not self._is_target_or_consensus_book(offer.bookmaker):
                    rejections['non_target_bookmaker'] += 1
                    continue
                families[offer.family].append(offer)

            match_candidates: list[CandidateBet] = []
            if families.get('totals'):
                match_candidates.extend(self._build_totals_candidates(match, families['totals'], context, rejections))
            if families.get('h2h'):
                match_candidates.extend(self._build_h2h_candidates(match, families['h2h'], context, rejections))
            if families.get('spreads'):
                match_candidates.extend(self._build_spread_candidates(match, families['spreads'], context, rejections))
            if families.get('doubleChance'):
                match_candidates.extend(self._build_double_chance_candidates(match, families['doubleChance'], context, rejections))
            if families.get('dnb'):
                match_candidates.extend(self._build_dnb_candidates(match, families['dnb'], context, rejections))
            if families.get('btts'):
                match_candidates.extend(self._build_btts_candidates(match, families['btts'], context, rejections))
            if families.get('teamTotals'):
                match_candidates.extend(self._build_team_totals_candidates(match, families['teamTotals'], context, rejections))

            match_candidates.sort(key=lambda item: self._candidate_rank_key(item), reverse=True)
            if match_candidates:
                picked = match_candidates[0]
                candidates.append(picked)
                debug_rows.append(
                    {
                        'match_key': match_key,
                        'selection': picked.selection,
                        'family': picked.family,
                        'count': len(match_candidates),
                        'context_source': picked.source_summary.get('context_source'),
                        'context_mode': picked.source_summary.get('context_mode'),
                        'selected_bookmaker': picked.source_summary.get('selected_bookmaker'),
                        'selected_source': picked.source_summary.get('selected_source'),
                        'market_movement': picked.source_summary.get('market_movement'),
                        'market_probability': round(float(picked.market_probability), 4),
                        'model_probability': round(float(picked.model_probability), 4),
                        'adjusted_probability': round(float(picked.adjusted_probability), 4),
                        'confidence': round(float(picked.confidence), 2),
                        'expected_home': picked.expected_home,
                        'expected_away': picked.expected_away,
                    }
                )
            else:
                rejections['no_candidate_for_match'] += 1

        candidates = self._filter_and_rank(candidates, rejections)
        return candidates, dict(rejections), {'matches': debug_rows[:200]}

    def _build_totals_candidates(
        self,
        match: Match,
        offers: list[Offer],
        context: MatchContext | None,
        rejections: dict[str, int],
    ) -> list[CandidateBet]:
        xg = self._enriched_expected_goals(match, context)
        if xg is None:
            rejections['missing_context_totals'] += 1
            return []
        expected_home, expected_away = xg

        buckets: dict[tuple[str, float | None], list[Offer]] = defaultdict(list)
        for offer in offers:
            buckets[(offer.selection, offer.point)].append(offer)

        supported_total_points = self.settings.supported_lines_for_family('totals')
        expected_total = expected_home + expected_away
        signal_label = self._signal_stack_label(context)
        result: list[CandidateBet] = []
        for (selection, point), bucket in buckets.items():
            low = str(selection or '').lower()
            if point is None or not (low.startswith('over') or low.startswith('under')):
                continue
            normalized_point = self._normalize_supported_line(point, 'totals')
            if normalized_point is None:
                rejections['unsupported_total_line'] += 1
                continue
            point = normalized_point
            required_books = self._required_books_for_bucket('totals', point, bucket, context)
            if len({self._norm_book(item.bookmaker) for item in bucket}) < required_books:
                rejections['insufficient_books'] += 1
                continue
            market_prob = self._fair_market_probability_totals(bucket, offers, selection, point)
            explicit_total_prob = self._context_total_probability(context, point)
            if explicit_total_prob is not None:
                over_prob = explicit_total_prob
                model_reason = 'context_total_probability'
            else:
                over_prob = self._poisson_line_probability(expected_total, point)
                over_prob = self._adjust_total_probability(over_prob, point, expected_home, expected_away, context)
                model_reason = 'xg_total_ensemble'
            if not self._is_valid_probability(over_prob):
                rejections['missing_model_probability_totals'] += 1
                continue
            model_prob = float(over_prob) if low.startswith('over') else (1.0 - float(over_prob))
            candidate = self._candidate_from_bucket(
                match=match,
                family='totals',
                selection=russian_selection('totals', selection, point),
                point=point,
                offers=bucket,
                market_prob=market_prob,
                model_prob=model_prob,
                reasons=[
                    'mode=xg_total',
                    f'model={model_reason}',
                    f'signals={signal_label}',
                    f'consensus_fair_odds={1 / max(float(market_prob), 0.01):.2f}',
                    f'line={point:g}',
                    f'context={self._context_label(context)}',
                ],
                expected_home=expected_home,
                expected_away=expected_away,
                model_mode='xg_total',
                context=context,
            )
            if candidate:
                result.append(candidate)
        return result

    def _build_h2h_candidates(
        self,
        match: Match,
        offers: list[Offer],
        context: MatchContext | None,
        rejections: dict[str, int],
    ) -> list[CandidateBet]:
        probs = self._derive_h2h_probabilities(match, context)
        if probs is None:
            rejections['missing_context_h2h'] += 1
            return []
        xg = self._enriched_expected_goals(match, context)

        buckets: dict[str, list[Offer]] = defaultdict(list)
        for offer in offers:
            buckets[offer.selection].append(offer)

        result: list[CandidateBet] = []
        signal_label = self._signal_stack_label(context)
        for selection, bucket in buckets.items():
            required_books = self._required_books_for_bucket('h2h', None, bucket, context)
            if len({self._norm_book(item.bookmaker) for item in bucket}) < required_books:
                rejections['insufficient_books'] += 1
                continue
            raw_model_prob = probs.get(selection)
            if raw_model_prob is None:
                continue
            market_prob = self._fair_market_probability_h2h(match, offers, selection)
            selection_key = self._h2h_selection_key(match, selection)
            model_prob = self._calibrate_h2h_selection_probability(
                match=match,
                context=context,
                selection_key=selection_key,
                base_prob=float(raw_model_prob),
                market_prob=market_prob,
                xg=xg,
            )
            candidate = self._candidate_from_bucket(
                match=match,
                family='h2h',
                selection=russian_selection('h2h', selection),
                point=None,
                offers=bucket,
                market_prob=market_prob,
                model_prob=model_prob,
                reasons=[
                    'mode=soccer_context',
                    'model=1x2_market_calibrated',
                    f'signals={signal_label}',
                    f'context={self._context_label(context)}',
                ],
                expected_home=xg[0] if xg else None,
                expected_away=xg[1] if xg else None,
                model_mode='soccer_context',
                context=context,
            )
            if candidate:
                result.append(candidate)
        return result

    def _build_spread_candidates(
        self,
        match: Match,
        offers: list[Offer],
        context: MatchContext | None,
        rejections: dict[str, int],
    ) -> list[CandidateBet]:
        xg = self._enriched_expected_goals(match, context)
        if xg is None:
            rejections['missing_context_spreads'] += 1
            return []
        expected_home, expected_away = xg
        diff = expected_home - expected_away
        result: list[CandidateBet] = []
        seen_keys: set[tuple[str, float]] = set()
        for offer in offers:
            if offer.point is None:
                continue
            key = (str(offer.selection), float(offer.point))
            if key in seen_keys:
                continue
            seen_keys.add(key)
            books = [item for item in offers if item.selection == offer.selection and item.point == offer.point]
            required_books = self._required_books_for_bucket('spreads', offer.point, books, context)
            if len({self._norm_book(item.bookmaker) for item in books}) < required_books:
                rejections['insufficient_books'] += 1
                continue
            team_side = (offer.team_side or '').lower()
            if team_side not in {'home', 'away'}:
                continue
            model_prob = clamp(0.50 + diff * 0.10, 0.05, 0.95) if team_side == 'home' else clamp(0.50 - diff * 0.10, 0.05, 0.95)
            market_prob = self._fair_market_probability_spreads(books, offers, offer.selection, offer.point, team_side)
            candidate = self._candidate_from_bucket(
                match=match,
                family='spreads',
                selection=offer.selection,
                point=offer.point,
                offers=books,
                market_prob=market_prob,
                model_prob=model_prob,
                reasons=['mode=xg_spread', 'model=xg_spread_stack', f'signals={self._signal_stack_label(context)}', f'context={self._context_label(context)}'],
                expected_home=expected_home,
                expected_away=expected_away,
                model_mode='xg_spread',
                context=context,
            )
            if candidate:
                result.append(candidate)
        return result

    def _build_btts_candidates(
        self,
        match: Match,
        offers: list[Offer],
        context: MatchContext | None,
        rejections: dict[str, int],
    ) -> list[CandidateBet]:
        xg = self._enriched_expected_goals(match, context)
        if xg is None:
            rejections['missing_context_btts'] += 1
            return []
        expected_home, expected_away = xg
        yes_prob = self._btts_yes_probability(expected_home, expected_away)
        yes_prob = self._adjust_btts_probability(yes_prob, expected_home, expected_away, context)
        if not self._is_valid_probability(yes_prob):
            rejections['missing_model_probability_btts'] += 1
            return []
        buckets: dict[str, list[Offer]] = defaultdict(list)
        for offer in offers:
            key = self._yes_no_key(offer.selection)
            if key:
                buckets[key].append(offer)
        result: list[CandidateBet] = []
        signal_label = self._signal_stack_label(context)
        for key, bucket in buckets.items():
            required_books = self._required_books_for_bucket('btts', None, bucket, context)
            if len({self._norm_book(item.bookmaker) for item in bucket}) < required_books:
                rejections['insufficient_books'] += 1
                continue
            market_prob = self._fair_market_probability_yes_no(bucket, offers, key, selector=self._yes_no_key)
            model_prob = self._calibrate_btts_selection_probability(
                selection_key=key,
                yes_prob=float(yes_prob),
                market_prob=market_prob,
                expected_home=expected_home,
                expected_away=expected_away,
                context=context,
            )
            candidate = self._candidate_from_bucket(
                match=match,
                family='btts',
                selection='Обе забьют: Да' if key == 'yes' else 'Обе забьют: Нет',
                point=None,
                offers=bucket,
                market_prob=market_prob,
                model_prob=model_prob,
                reasons=['mode=btts_poisson', 'model=btts_market_calibrated', f'signals={signal_label}', f'context={self._context_label(context)}'],
                expected_home=expected_home,
                expected_away=expected_away,
                model_mode='btts_poisson',
                context=context,
            )
            if candidate:
                result.append(candidate)
        return result

    def _build_team_totals_candidates(
        self,
        match: Match,
        offers: list[Offer],
        context: MatchContext | None,
        rejections: dict[str, int],
    ) -> list[CandidateBet]:
        xg = self._enriched_expected_goals(match, context)
        if xg is None:
            rejections['missing_context_team_totals'] += 1
            return []
        expected_home, expected_away = xg
        buckets: dict[tuple[str, float | None, str], list[Offer]] = defaultdict(list)
        for offer in offers:
            buckets[(offer.selection, offer.point, str(offer.team_side or '').lower())].append(offer)
        result: list[CandidateBet] = []
        signal_label = self._signal_stack_label(context)
        for (selection, point, team_side), bucket in buckets.items():
            low = str(selection or '').lower()
            if point is None or team_side not in {'home', 'away'}:
                continue
            if not (low.startswith('over') or low.startswith('under')):
                continue
            normalized_point = self._normalize_supported_line(point, 'teamTotals')
            if normalized_point is None:
                rejections['unsupported_team_total_line'] += 1
                continue
            point = normalized_point
            required_books = self._required_books_for_bucket('teamTotals', point, bucket, context)
            if len({self._norm_book(item.bookmaker) for item in bucket}) < required_books:
                rejections['insufficient_books'] += 1
                continue
            lam = expected_home if team_side == 'home' else expected_away
            over_prob = self._poisson_line_probability(lam, float(point))
            over_prob = self._adjust_team_total_probability(over_prob, lam, float(point), team_side, context)
            if not self._is_valid_probability(over_prob):
                rejections['missing_model_probability_team_totals'] += 1
                continue
            market_prob = self._fair_market_probability_team_totals(bucket, offers, selection, point, team_side)
            model_prob = float(over_prob) if low.startswith('over') else (1.0 - float(over_prob))
            team_name = match.home_team if team_side == 'home' else match.away_team
            side_text = 'ТБ' if low.startswith('over') else 'ТМ'
            candidate = self._candidate_from_bucket(
                match=match,
                family='teamTotals',
                selection=f'{team_name} {side_text} {float(point):g}',
                point=float(point),
                offers=bucket,
                market_prob=market_prob,
                model_prob=model_prob,
                reasons=['mode=team_total_poisson', f'model={team_side}_team_total_stack', f'signals={signal_label}', f'context={self._context_label(context)}'],
                expected_home=expected_home,
                expected_away=expected_away,
                model_mode='team_total_poisson',
                context=context,
            )
            if candidate:
                result.append(candidate)
        return result

    def _build_double_chance_candidates(
        self,
        match: Match,
        offers: list[Offer],
        context: MatchContext | None,
        rejections: dict[str, int],
    ) -> list[CandidateBet]:
        probs = self._derive_h2h_probabilities(match, context)
        if probs is None:
            rejections['missing_context_double_chance'] += 1
            return []
        grouped: dict[str, list[Offer]] = defaultdict(list)
        for offer in offers:
            key = self._double_chance_key(match, offer.selection)
            if key:
                grouped[key].append(offer)
        result: list[CandidateBet] = []
        for key, bucket in grouped.items():
            required_books = self._required_books_for_bucket('doubleChance', None, bucket, context)
            if len({self._norm_book(item.bookmaker) for item in bucket}) < required_books:
                rejections['insufficient_books'] += 1
                continue
            if key == 'home_draw':
                model_prob = probs.get(match.home_team, 0.0) + probs.get('draw', 0.0)
                selection = f'{match.home_team} или ничья'
            elif key == 'away_draw':
                model_prob = probs.get(match.away_team, 0.0) + probs.get('draw', 0.0)
                selection = f'{match.away_team} или ничья'
            else:
                model_prob = probs.get(match.home_team, 0.0) + probs.get(match.away_team, 0.0)
                selection = 'Без ничьи'
            market_prob = self._fair_market_probability_double_chance(match, offers, key)
            candidate = self._candidate_from_bucket(
                match=match,
                family='doubleChance',
                selection=selection,
                point=None,
                offers=bucket,
                market_prob=market_prob,
                model_prob=model_prob,
                reasons=['mode=h2h_ensemble', 'model=double_chance_from_1x2', f'context={self._context_label(context)}'],
                expected_home=self._validated_expected_goals(context)[0] if self._validated_expected_goals(context) else None,
                expected_away=self._validated_expected_goals(context)[1] if self._validated_expected_goals(context) else None,
                model_mode='h2h_ensemble',
                context=context,
            )
            if candidate:
                result.append(candidate)
        return result

    def _build_dnb_candidates(
        self,
        match: Match,
        offers: list[Offer],
        context: MatchContext | None,
        rejections: dict[str, int],
    ) -> list[CandidateBet]:
        probs = self._derive_h2h_probabilities(match, context)
        if probs is None:
            rejections['missing_context_dnb'] += 1
            return []
        draw_prob = clamp(float(probs.get('draw', 0.0) or 0.0), 0.02, 0.45)
        remaining = max(1.0 - draw_prob, 0.05)
        grouped: dict[str, list[Offer]] = defaultdict(list)
        for offer in offers:
            key = self._dnb_key(match, offer.selection)
            if key:
                grouped[key].append(offer)
        result: list[CandidateBet] = []
        for key, bucket in grouped.items():
            required_books = self._required_books_for_bucket('dnb', None, bucket, context)
            if len({self._norm_book(item.bookmaker) for item in bucket}) < required_books:
                rejections['insufficient_books'] += 1
                continue
            if key == 'home':
                model_prob = probs.get(match.home_team, 0.0) / remaining
                selection = f'{match.home_team} (0)'
            else:
                model_prob = probs.get(match.away_team, 0.0) / remaining
                selection = f'{match.away_team} (0)'
            market_prob = self._fair_market_probability_dnb(match, offers, key)
            candidate = self._candidate_from_bucket(
                match=match,
                family='dnb',
                selection=selection,
                point=None,
                offers=bucket,
                market_prob=market_prob,
                model_prob=clamp(model_prob, 0.02, 0.98),
                reasons=['mode=h2h_ensemble', 'model=dnb_from_1x2', f'context={self._context_label(context)}'],
                expected_home=self._validated_expected_goals(context)[0] if self._validated_expected_goals(context) else None,
                expected_away=self._validated_expected_goals(context)[1] if self._validated_expected_goals(context) else None,
                model_mode='h2h_ensemble',
                context=context,
            )
            if candidate:
                result.append(candidate)
        return result

    def _candidate_from_bucket(
        self,
        *,
        match: Match,
        family: str,
        selection: str,
        point: float | None,
        offers: list[Offer],
        market_prob: float,
        model_prob: float,
        reasons: list[str],
        expected_home: float | None,
        expected_away: float | None,
        model_mode: str,
        context: MatchContext | None,
        market_signal: dict[str, Any] | None = None,
    ) -> CandidateBet | None:
        if not self._is_valid_probability(model_prob) or not self._is_valid_probability(market_prob):
            return None

        books = {offer.bookmaker for offer in offers}
        sources = {offer.source for offer in offers}
        required_books = self._required_books_for_bucket(family, point, offers, context)
        if len(books) < required_books:
            return None
        if len(sources) < self.settings.min_sources_publish:
            return None
        best_offer = self._select_best_offer(offers)
        best_price = best_offer.price
        if not (self.settings.odds_min <= best_price <= self.settings.odds_max):
            return None

        market_prob = clamp(float(market_prob), 0.02, 0.98)
        model_prob = clamp(float(model_prob), 0.02, 0.98)

        market_signal = market_signal or self._market_signal_for_bucket(match.match_key, family, offers, point)
        market_signal_key = None
        steam_delta = None
        movement_label = None
        dispersion_pct = None
        best_vs_consensus_edge_pct = None
        if isinstance(market_signal, dict):
            market_signal_key = self._market_signal_key(family, str(getattr(offers[0], 'selection', '') or ''), point, str(getattr(offers[0], 'team_side', '') or '')) if offers else None
            steam_delta = self._to_float_safe(market_signal.get('delta_prob_pp'))
            movement_label = str(market_signal.get('movement_label') or '') or None
            dispersion_pct = self._to_float_safe(market_signal.get('consensus_dispersion_pct'))
            best_vs_consensus_edge_pct = self._to_float_safe(market_signal.get('best_vs_consensus_edge_pct'))

        base_confidence = clamp(48 + len(books) * 4 + len(sources) * 5, 0, 100)
        context_confidence = float(getattr(context, 'confidence', 58.0) or 58.0) if context is not None else base_confidence
        confidence = (base_confidence * 0.56) + (context_confidence * 0.44)
        shrink_min = 0.18
        shrink_max = 0.55
        context_source = str(getattr(context, 'source', '') or '') if context is not None else ''
        if len(books) == 1:
            confidence -= 6.0
            shrink_min = min(shrink_min, 0.10)
            shrink_max = min(shrink_max, 0.24)
        if context_source == 'sstats_form':
            confidence = min(confidence, 62.0)
            shrink_min = 0.10
            shrink_max = 0.28
        elif context_source == 'sstats':
            confidence = min(confidence, 68.0)
            shrink_min = 0.15
            shrink_max = 0.42
        elif context_source == 'bzzoiro_predictions':
            confidence = min(confidence, 72.0)
            shrink_min = 0.16
            shrink_max = 0.36
        elif context_source in {'ensemble', 'api_football', 'espn', 'thesportsdb'}:
            confidence = min(confidence + 2.0, 76.0)
        history_ready = bool((market_signal or {}).get('history_ready')) if isinstance(market_signal, dict) else False
        observation_count = int((market_signal or {}).get('observation_count') or (2 if steam_delta is not None else 1)) if isinstance(market_signal, dict) else (2 if steam_delta is not None else 1)
        books_signal_count = int((market_signal or {}).get('books_count') or len(books)) if isinstance(market_signal, dict) else len(books)
        sources_signal_count = int((market_signal or {}).get('sources_count') or len(sources)) if isinstance(market_signal, dict) else len(sources)
        if getattr(self.settings, 'line_movement_signal_enabled', True) and steam_delta is not None:
            threshold = float(getattr(self.settings, 'line_movement_min_delta_pct', 1.75) or 1.75)
            movement_ready = True
            if getattr(self.settings, 'line_movement_requires_history', True):
                movement_ready = history_ready and observation_count >= 2
            movement_ready = movement_ready and books_signal_count >= max(1, int(getattr(self.settings, 'line_movement_min_books', 2) or 2)) and sources_signal_count >= max(1, int(getattr(self.settings, 'line_movement_min_sources', 1) or 1))
            if movement_ready and steam_delta >= threshold:
                confidence += float(getattr(self.settings, 'line_movement_confidence_bonus', 4.0) or 4.0)
            elif movement_ready and steam_delta <= -threshold:
                penalty = float(getattr(self.settings, 'line_movement_confidence_penalty', 3.0) or 3.0)
                penalty *= float(getattr(self.settings, 'line_movement_negative_penalty_factor', 0.5) or 0.5)
                confidence -= penalty
        if dispersion_pct is not None and dispersion_pct <= float(getattr(self.settings, 'max_consensus_dispersion_pct', 6.5) or 6.5):
            confidence += float(getattr(self.settings, 'consensus_tight_confidence_bonus', 2.0) or 2.0)
        confidence = clamp(confidence, 0, 100)

        adjusted = shrink_probability(model_prob, market_prob, confidence, shrink_min, shrink_max)
        fair_odds = 1.0 / max(adjusted, 0.01)
        ev_pct = (adjusted * best_price - 1.0) * 100.0
        edge_pct = (adjusted - market_prob) * 100.0

        context_details = dict(getattr(context, 'details', {}) or {}) if context is not None else {}
        reasons = list(reasons)
        reasons.append(f'selected_book={best_offer.bookmaker}')
        reasons.append(f'selected_source={best_offer.source}')
        reasons.append(f'family_weight={self.settings.score_weight_for_family(family):.2f}')
        if context_source:
            reasons.append(f'context_confidence={context_confidence:.1f}')
        if len(books) == 1:
            reasons.append('single_book_guard=enabled')
        if movement_label:
            reasons.append(f'market_move={movement_label}')
        if not history_ready:
            reasons.append('market_history=limited')
        if steam_delta is not None:
            reasons.append(f'line_move_pp={steam_delta:+.2f}')
        if best_vs_consensus_edge_pct is not None:
            reasons.append(f'best_vs_consensus={best_vs_consensus_edge_pct:+.2f}%')
        if dispersion_pct is not None:
            reasons.append(f'consensus_dispersion={dispersion_pct:.2f}%')

        return CandidateBet(
            match_key=match.match_key,
            sport_key=match.sport_key,
            league_name=match.league_name,
            home_team=match.home_team,
            away_team=match.away_team,
            commence_time=match.commence_time,
            family=family,
            selection=selection,
            point=point,
            odds=best_price,
            fair_odds=fair_odds,
            implied_probability=market_prob,
            market_probability=market_prob,
            consensus_probability=market_prob,
            model_probability=model_prob,
            final_probability=adjusted,
            adjusted_probability=adjusted,
            edge_pct=edge_pct,
            ev_pct=ev_pct,
            confidence=confidence,
            books_count=len(books),
            sources_count=len(sources),
            model_mode=model_mode,
            expected_home=expected_home,
            expected_away=expected_away,
            reasons=reasons,
            source_summary={
                'books': sorted(books),
                'sources': sorted(sources),
                'offers_seen': len(offers),
                'required_books': required_books,
                'selected_bookmaker': best_offer.bookmaker,
                'selected_source': best_offer.source,
                'selected_price': best_offer.price,
                'context_source': context_source or None,
                'context_confidence': round(context_confidence, 2) if context is not None else None,
                'context_mode': context_details.get('sstats_mode') or context_details.get('context_mode'),
                'home_recent_count': context_details.get('home_recent_count'),
                'away_recent_count': context_details.get('away_recent_count'),
                'raw_model_probability': round(float(model_prob), 4),
                'adjusted_probability': round(float(adjusted), 4),
                'market_probability': round(float(market_prob), 4),
                'market_signal_key': market_signal_key,
                'market_movement': movement_label,
                'line_move_pp': round(float(steam_delta), 3) if steam_delta is not None else None,
                'consensus_dispersion_pct': round(float(dispersion_pct), 3) if dispersion_pct is not None else None,
                'best_vs_consensus_edge_pct': round(float(best_vs_consensus_edge_pct), 3) if best_vs_consensus_edge_pct is not None else None,
            },
        )


    def _market_signal_key(self, family: str, selection: str, point: float | None, team_side: str | None) -> str:
        family_key = str(family or '').strip()
        selection_key = str(selection or '').strip().lower()
        point_key = '' if point is None else f'{float(point):.2f}'
        team_key = str(team_side or '').strip().lower()
        return '|'.join([family_key, selection_key, point_key, team_key])

    def _market_signal_for_bucket(self, match_key: str, family: str, offers: list[Offer], point: float | None) -> dict[str, Any] | None:
        mapping = self._market_signals_by_match.get(str(match_key)) or {}
        if not mapping or not offers:
            return None
        first = offers[0]
        key = self._market_signal_key(family, str(getattr(first, 'selection', '') or ''), point, str(getattr(first, 'team_side', '') or ''))
        signal = mapping.get(key)
        if isinstance(signal, dict):
            return signal
        return None


    @staticmethod
    def _to_float_safe(value: Any) -> float | None:
        try:
            if value in (None, ''):
                return None
            number = float(value)
            if math.isnan(number) or math.isinf(number):
                return None
            return number
        except Exception:
            return None

    def _fair_market_probability_h2h(self, match: Match, offers: list[Offer], selection: str) -> float:
        selection_key = self._h2h_selection_key(match, selection)
        weighted_probs: list[tuple[float, float]] = []
        by_book: dict[str, dict[str, float]] = defaultdict(dict)
        for offer in offers:
            key = self._h2h_selection_key(match, offer.selection)
            if not key:
                continue
            book = self._norm_book(offer.bookmaker)
            if not book or offer.price <= 1.0:
                continue
            best = by_book[book].get(key)
            if best is None or offer.price > best:
                by_book[book][key] = float(offer.price)
        for book, price_map in by_book.items():
            weight = self._bookmaker_weight(book)
            if {'home', 'draw', 'away'} <= set(price_map):
                probs = strip_vig_three_way(price_map['home'], price_map['draw'], price_map['away'])
                if probs is None:
                    continue
                mapping = {'home': probs[0], 'draw': probs[1], 'away': probs[2]}
                if selection_key in mapping:
                    weighted_probs.append((mapping[selection_key], weight))
        if weighted_probs:
            return clamp(sum(value * weight for value, weight in weighted_probs) / sum(weight for _, weight in weighted_probs), 0.02, 0.98)
        aggregate: dict[str, float] = {}
        for key in ('home', 'draw', 'away'):
            prices = [offer.price for offer in offers if self._h2h_selection_key(match, offer.selection) == key and offer.price > 1.0]
            if prices:
                aggregate[key] = mean(prices)
        if {'home', 'draw', 'away'} <= set(aggregate):
            probs = strip_vig_three_way(aggregate['home'], aggregate['draw'], aggregate['away'])
            if probs is not None:
                mapping = {'home': probs[0], 'draw': probs[1], 'away': probs[2]}
                if selection_key in mapping:
                    return clamp(mapping[selection_key], 0.02, 0.98)
        fallback = [implied_probability(item.price) for item in offers if self._h2h_selection_key(match, item.selection) == selection_key]
        return clamp(mean(fallback), 0.02, 0.98) if fallback else 0.50

    def _fair_market_probability_totals(self, bucket: list[Offer], offers: list[Offer], selection: str, point: float) -> float:
        return self._fair_market_probability_over_under(
            bucket=bucket,
            offers=offers,
            selection=selection,
            point=point,
            key_getter=lambda offer: ('point', round(float(offer.point), 2)) if offer.point is not None else None,
            required_value=round(float(point), 2),
            side_getter=lambda offer: 'over' if str(offer.selection or '').lower().startswith('over') else 'under' if str(offer.selection or '').lower().startswith('under') else None,
        )

    def _fair_market_probability_spreads(self, bucket: list[Offer], offers: list[Offer], selection: str, point: float | None, team_side: str) -> float:
        if point is None:
            fallback = [implied_probability(item.price) for item in bucket if item.price > 1.0]
            return clamp(mean(fallback), 0.02, 0.98) if fallback else 0.50
        current_side = str(team_side or '').lower()
        other_side = 'away' if current_side == 'home' else 'home'
        return self._fair_market_probability_two_way(
            offers=offers,
            bucket=bucket,
            current_key=current_side,
            other_key=other_side,
            key_resolver=lambda offer: str(offer.team_side or '').lower() if offer.point is not None and round(float(offer.point), 2) == round(float(point), 2) else None,
        )

    def _fair_market_probability_team_totals(self, bucket: list[Offer], offers: list[Offer], selection: str, point: float, team_side: str) -> float:
        current_side = 'over' if str(selection).lower().startswith('over') else 'under'
        other_side = 'under' if current_side == 'over' else 'over'
        target_point = round(float(point), 2)
        side = str(team_side or '').lower()
        return self._fair_market_probability_two_way(
            offers=offers,
            bucket=bucket,
            current_key=current_side,
            other_key=other_side,
            key_resolver=lambda offer: (
                'over' if str(offer.selection or '').lower().startswith('over') else 'under' if str(offer.selection or '').lower().startswith('under') else None
            ) if offer.point is not None and round(float(offer.point), 2) == target_point and str(offer.team_side or '').lower() == side else None,
        )

    def _fair_market_probability_yes_no(self, bucket: list[Offer], offers: list[Offer], current_key: str, selector) -> float:
        other_key = 'no' if current_key == 'yes' else 'yes'
        return self._fair_market_probability_two_way(
            offers=offers,
            bucket=bucket,
            current_key=current_key,
            other_key=other_key,
            key_resolver=lambda offer: selector(offer.selection),
        )

    def _fair_market_probability_dnb(self, match: Match, offers: list[Offer], current_key: str) -> float:
        other_key = 'away' if current_key == 'home' else 'home'
        current_bucket = [offer for offer in offers if self._dnb_key(match, offer.selection) == current_key]
        return self._fair_market_probability_two_way(
            offers=offers,
            bucket=current_bucket,
            current_key=current_key,
            other_key=other_key,
            key_resolver=lambda offer: self._dnb_key(match, offer.selection),
        )

    def _fair_market_probability_double_chance(self, match: Match, offers: list[Offer], selection_key: str) -> float:
        by_book: dict[str, dict[str, float]] = defaultdict(dict)
        for offer in offers:
            key = self._double_chance_key(match, offer.selection)
            if not key or offer.price <= 1.0:
                continue
            book = self._norm_book(offer.bookmaker)
            best = by_book[book].get(key)
            if best is None or offer.price > best:
                by_book[book][key] = float(offer.price)
        weighted_probs: list[tuple[float, float]] = []
        for book, price_map in by_book.items():
            if {'home_draw', 'away_draw', 'home_away'} <= set(price_map):
                probs = strip_vig_three_way(price_map['home_draw'], price_map['away_draw'], price_map['home_away'])
                if probs is None:
                    continue
                mapping = {'home_draw': probs[0], 'away_draw': probs[1], 'home_away': probs[2]}
                weighted_probs.append((mapping[selection_key], self._bookmaker_weight(book)))
        if weighted_probs:
            return clamp(sum(v * w for v, w in weighted_probs) / sum(w for _, w in weighted_probs), 0.02, 0.98)
        current_bucket = [offer for offer in offers if self._double_chance_key(match, offer.selection) == selection_key]
        fallback = [implied_probability(item.price) for item in current_bucket if item.price > 1.0]
        return clamp(mean(fallback), 0.02, 0.98) if fallback else 0.50

    def _fair_market_probability_two_way(self, offers: list[Offer], bucket: list[Offer], current_key: str, other_key: str, key_resolver) -> float:
        weighted_probs: list[tuple[float, float]] = []
        by_book: dict[str, dict[str, float]] = defaultdict(dict)
        for offer in offers:
            key = key_resolver(offer)
            if key not in {current_key, other_key} or offer.price <= 1.0:
                continue
            book = self._norm_book(offer.bookmaker)
            best = by_book[book].get(key)
            if best is None or offer.price > best:
                by_book[book][key] = float(offer.price)
        for book, price_map in by_book.items():
            if current_key not in price_map or other_key not in price_map:
                continue
            probs = strip_vig_two_way(price_map[current_key], price_map[other_key])
            if probs is None:
                continue
            weighted_probs.append((probs[0], self._bookmaker_weight(book)))
        if weighted_probs:
            return clamp(sum(v * w for v, w in weighted_probs) / sum(w for _, w in weighted_probs), 0.02, 0.98)
        current_prices = [offer.price for offer in offers if key_resolver(offer) == current_key and offer.price > 1.0]
        other_prices = [offer.price for offer in offers if key_resolver(offer) == other_key and offer.price > 1.0]
        if current_prices and other_prices:
            probs = strip_vig_two_way(mean(current_prices), mean(other_prices))
            if probs is not None:
                return clamp(probs[0], 0.02, 0.98)
        fallback = [implied_probability(item.price) for item in bucket if item.price > 1.0]
        return clamp(mean(fallback), 0.02, 0.98) if fallback else 0.50

    def _fair_market_probability_over_under(self, bucket: list[Offer], offers: list[Offer], selection: str, point: float, key_getter, required_value: float, side_getter) -> float:
        current_side = 'over' if str(selection).lower().startswith('over') else 'under'
        other_side = 'under' if current_side == 'over' else 'over'
        return self._fair_market_probability_two_way(
            offers=offers,
            bucket=bucket,
            current_key=current_side,
            other_key=other_side,
            key_resolver=lambda offer: side_getter(offer) if offer.point is not None and round(float(offer.point), 2) == required_value else None,
        )

    @staticmethod
    def _h2h_selection_key(match: Match, selection: str) -> str | None:
        text = str(selection or '').strip().lower()
        if text in {'draw', 'x'}:
            return 'draw'
        if CandidateFactory._canonical_team(selection) == CandidateFactory._canonical_team(match.home_team):
            return 'home'
        if CandidateFactory._canonical_team(selection) == CandidateFactory._canonical_team(match.away_team):
            return 'away'
        return None

    @staticmethod
    def _double_chance_key(match: Match, selection: str) -> str | None:
        text = str(selection or '').strip().lower()
        has_draw = any(token in text for token in ['draw', 'нич', 'x'])
        has_home = CandidateFactory._canonical_team(match.home_team) in CandidateFactory._canonical_team(text) or CandidateFactory._canonical_team(match.home_team) in text
        has_away = CandidateFactory._canonical_team(match.away_team) in CandidateFactory._canonical_team(text) or CandidateFactory._canonical_team(match.away_team) in text
        if has_draw and has_home:
            return 'home_draw'
        if has_draw and has_away:
            return 'away_draw'
        if (has_home and has_away) or '12' == text.replace(' ', '') or 'no draw' in text or 'без нич' in text:
            return 'home_away'
        return None

    @staticmethod
    def _dnb_key(match: Match, selection: str) -> str | None:
        if CandidateFactory._h2h_selection_key(match, selection) == 'home':
            return 'home'
        if CandidateFactory._h2h_selection_key(match, selection) == 'away':
            return 'away'
        return None

    @staticmethod
    def _yes_no_key(selection: str) -> str | None:
        text = str(selection or '').strip().lower()
        if text in {'yes', 'да', 'btts yes', 'both teams to score yes'} or text.endswith(' yes'):
            return 'yes'
        if text in {'no', 'нет', 'btts no', 'both teams to score no'} or text.endswith(' no'):
            return 'no'
        return None


    def _bookmaker_weight(self, bookmaker: str) -> float:
        normalized = self._norm_book(bookmaker)
        sharp = {self._norm_book(item) for item in getattr(self.settings, 'sharp_bookmakers', []) or []}
        for raw_name, weight in BOOKMAKER_WEIGHTS.items():
            if self._norm_book(raw_name) == normalized:
                base = float(weight)
                if normalized in sharp:
                    base = max(base, 1.1)
                return base
        return 1.1 if normalized in sharp else 1.0

    @staticmethod
    def _canonical_team(value: str) -> str:
        from app.utils import canonicalize_team_name
        return canonicalize_team_name(value)

    def _validated_expected_goals(self, context: MatchContext | None) -> tuple[float, float] | None:
        if context is None:
            return None
        try:
            home = float(context.expected_home)
            away = float(context.expected_away)
        except Exception:
            return None
        if not math.isfinite(home) or not math.isfinite(away):
            return None
        if getattr(self.settings, 'reject_negative_expected_goals', True) and (home < 0 or away < 0):
            return None
        min_goal = float(getattr(self.settings, 'min_expected_goals_value', 0.15) or 0.15)
        max_goal = float(getattr(self.settings, 'max_expected_goals_value', 4.8) or 4.8)
        if home < min_goal or away < min_goal or home > max_goal or away > max_goal:
            return None
        return clamp(home, min_goal, max_goal), clamp(away, min_goal, max_goal)

    def _enriched_expected_goals(self, match: Match, context: MatchContext | None) -> tuple[float, float] | None:
        base = self._validated_expected_goals(context)
        if base is None:
            return None
        home, away = base
        details = dict(getattr(context, 'details', {}) or {}) if context is not None else {}
        max_goal = float(getattr(self.settings, 'max_expected_goals_value', 4.8) or 4.8)
        min_goal = float(getattr(self.settings, 'min_expected_goals_value', 0.15) or 0.15)

        form_home = self._metric01(self._first_float(details, 'espn_home_form', 'home_form', 'api_football_home_form', 'thesportsdb_home_form_score'))
        form_away = self._metric01(self._first_float(details, 'espn_away_form', 'away_form', 'api_football_away_form', 'thesportsdb_away_form_score'))
        if form_home is not None and form_away is not None:
            delta = clamp((form_home - form_away) * 0.22, -0.18, 0.18)
            home += max(delta, 0.0) * 0.55
            away += max(-delta, 0.0) * 0.55

        ppg_home = self._first_float(details, 'thesportsdb_home_ppg', 'home_ppg', 'sstats_home_ppg')
        ppg_away = self._first_float(details, 'thesportsdb_away_ppg', 'away_ppg', 'sstats_away_ppg')
        if ppg_home is not None and ppg_away is not None:
            delta = clamp((ppg_home - ppg_away) * 0.10, -0.16, 0.16)
            home += max(delta, 0.0) * 0.60
            away += max(-delta, 0.0) * 0.60

        home_att = self._metric01(self._first_float(details, 'home_attack', 'api_football_home_attack', 'api_football_home_att', 'espn_home_attack'))
        away_att = self._metric01(self._first_float(details, 'away_attack', 'api_football_away_attack', 'api_football_away_att', 'espn_away_attack'))
        home_def = self._metric01(self._first_float(details, 'home_defense', 'api_football_home_defense', 'api_football_home_def', 'espn_home_defense'))
        away_def = self._metric01(self._first_float(details, 'away_defense', 'api_football_away_defense', 'api_football_away_def', 'espn_away_defense'))
        if home_att is not None:
            home += (home_att - 0.5) * 0.35
        if away_att is not None:
            away += (away_att - 0.5) * 0.35
        if away_def is not None:
            home += (0.5 - away_def) * 0.30
        if home_def is not None:
            away += (0.5 - home_def) * 0.30

        gf_home = self._first_float(details, 'thesportsdb_home_gf_pg', 'home_gf_pg', 'sstats_home_goals_for_pg')
        ga_home = self._first_float(details, 'thesportsdb_home_ga_pg', 'home_ga_pg', 'sstats_home_goals_against_pg')
        gf_away = self._first_float(details, 'thesportsdb_away_gf_pg', 'away_gf_pg', 'sstats_away_goals_for_pg')
        ga_away = self._first_float(details, 'thesportsdb_away_ga_pg', 'away_ga_pg', 'sstats_away_goals_against_pg')
        if gf_home is not None and ga_away is not None:
            home = (home * 0.78) + (((gf_home + ga_away) / 2.0) * 0.22)
        if gf_away is not None and ga_home is not None:
            away = (away * 0.78) + (((gf_away + ga_home) / 2.0) * 0.22)

        home_rest = self._first_float(details, 'home_rest_days', 'espn_home_rest_days', 'sstats_home_rest_days')
        away_rest = self._first_float(details, 'away_rest_days', 'espn_away_rest_days', 'sstats_away_rest_days')
        if home_rest is not None and away_rest is not None:
            rest_delta = clamp((home_rest - away_rest) * 0.03, -0.12, 0.12)
            home += max(rest_delta, 0.0)
            away += max(-rest_delta, 0.0)

        home_inj = self._first_float(details, 'home_injuries', 'espn_home_injuries', 'home_absences')
        away_inj = self._first_float(details, 'away_injuries', 'espn_away_injuries', 'away_absences')
        if home_inj is not None:
            home -= min(home_inj, 4.0) * 0.07
        if away_inj is not None:
            away -= min(away_inj, 4.0) * 0.07

        home = clamp(home, min_goal, max_goal)
        away = clamp(away, min_goal, max_goal)
        return home, away

    def _derive_h2h_probabilities(self, match: Match, context: MatchContext | None) -> dict[str, float] | None:
        if context is None:
            return None
        weighted_parts: list[tuple[dict[str, float], float]] = []
        xg = self._enriched_expected_goals(match, context)
        if xg is not None:
            expected_home, expected_away = xg
            denom = max(expected_home + expected_away, 0.1)
            xg_home = clamp(expected_home / denom, 0.08, 0.80)
            xg_away = clamp(expected_away / denom, 0.08, 0.80)
            gap = abs(expected_home - expected_away)
            xg_draw = clamp(0.28 - gap * 0.06, 0.10, 0.30)
            weighted_parts.append((self._normalize_probabilities({
                match.home_team: xg_home,
                match.away_team: xg_away,
                'draw': xg_draw,
                'Draw': xg_draw,
            }), float(getattr(self.settings, 'signal_weight_xg', 0.34) or 0.34)))
        explicit = self._explicit_h2h_probabilities(match, context)
        if explicit is not None:
            source = str(context.source or '')
            explicit_weight = float(getattr(self.settings, 'signal_weight_explicit', 0.40) or 0.40)
            if source in {'api_football', 'espn', 'ensemble', 'thesportsdb'}:
                explicit_weight += 0.05
            weighted_parts.append((explicit, explicit_weight))
        strength = self._strength_probabilities(match, context)
        if strength is not None:
            weighted_parts.append((strength, float(getattr(self.settings, 'signal_weight_strength', 0.16) or 0.16)))
        momentum = self._momentum_probabilities(match, context)
        if momentum is not None:
            weighted_parts.append((momentum, float(getattr(self.settings, 'signal_weight_momentum', 0.10) or 0.10)))
        injury = self._injury_probabilities(match, context)
        if injury is not None:
            weighted_parts.append((injury, float(getattr(self.settings, 'signal_weight_injuries', 0.07) or 0.07)))
        if not weighted_parts:
            return None
        aggregate: dict[str, float] = defaultdict(float)
        total_weight = 0.0
        for probs, weight in weighted_parts:
            total_weight += weight
            for key, value in probs.items():
                aggregate[key] += float(value) * weight
        if total_weight <= 0:
            return None
        for key in list(aggregate):
            aggregate[key] /= total_weight
        return self._normalize_probabilities(dict(aggregate))

    def _explicit_h2h_probabilities(self, match: Match, context: MatchContext) -> dict[str, float] | None:
        home = self._safe_probability(context.home_win_probability)
        away = self._safe_probability(context.away_win_probability)
        draw = self._context_draw_probability(context)
        if home is None or away is None:
            return None
        if draw is None:
            draw = clamp(1.0 - home - away, 0.08, 0.32)
        return self._normalize_probabilities({
            match.home_team: home,
            match.away_team: away,
            'draw': draw,
            'Draw': draw,
        })

    def _strength_probabilities(self, match: Match, context: MatchContext) -> dict[str, float] | None:
        details = dict(getattr(context, 'details', {}) or {})
        delta = 0.0
        parts = 0
        ppg_home = self._first_float(details, 'thesportsdb_home_ppg', 'home_ppg', 'sstats_home_ppg')
        ppg_away = self._first_float(details, 'thesportsdb_away_ppg', 'away_ppg', 'sstats_away_ppg')
        if ppg_home is not None and ppg_away is not None:
            delta += clamp((ppg_home - ppg_away) * 0.12, -0.18, 0.18)
            parts += 1
        rank_home = self._first_float(details, 'thesportsdb_home_rank', 'home_rank', 'sstats_home_rank')
        rank_away = self._first_float(details, 'thesportsdb_away_rank', 'away_rank', 'sstats_away_rank')
        if rank_home is not None and rank_away is not None:
            delta += clamp((rank_away - rank_home) * 0.015, -0.14, 0.14)
            parts += 1
        form_home = self._metric01(self._first_float(details, 'espn_home_form', 'home_form', 'api_football_home_form', 'thesportsdb_home_form_score'))
        form_away = self._metric01(self._first_float(details, 'espn_away_form', 'away_form', 'api_football_away_form', 'thesportsdb_away_form_score'))
        if form_home is not None and form_away is not None:
            delta += clamp((form_home - form_away) * 0.18, -0.12, 0.12)
            parts += 1
        if parts == 0:
            return None
        delta /= parts
        draw = clamp(0.25 - abs(delta) * 0.20, 0.14, 0.30)
        home = 0.38 + delta
        away = 1.0 - home - draw
        return self._normalize_probabilities({
            match.home_team: home,
            match.away_team: away,
            'draw': draw,
            'Draw': draw,
        })

    def _momentum_probabilities(self, match: Match, context: MatchContext) -> dict[str, float] | None:
        details = dict(getattr(context, 'details', {}) or {})
        delta = 0.0
        parts = 0
        home_att = self._metric01(self._first_float(details, 'home_attack', 'api_football_home_attack', 'api_football_home_att'))
        away_att = self._metric01(self._first_float(details, 'away_attack', 'api_football_away_attack', 'api_football_away_att'))
        home_def = self._metric01(self._first_float(details, 'home_defense', 'api_football_home_defense', 'api_football_home_def'))
        away_def = self._metric01(self._first_float(details, 'away_defense', 'api_football_away_defense', 'api_football_away_def'))
        if home_att is not None and away_def is not None:
            delta += clamp((home_att - away_def) * 0.16, -0.14, 0.14)
            parts += 1
        if away_att is not None and home_def is not None:
            delta += clamp((home_def - away_att) * -0.16, -0.14, 0.14)
            parts += 1
        home_rest = self._first_float(details, 'home_rest_days', 'espn_home_rest_days', 'sstats_home_rest_days')
        away_rest = self._first_float(details, 'away_rest_days', 'espn_away_rest_days', 'sstats_away_rest_days')
        if home_rest is not None and away_rest is not None:
            delta += clamp((home_rest - away_rest) * 0.03, -0.08, 0.08)
            parts += 1
        if parts == 0:
            return None
        delta /= parts
        draw = clamp(0.24 - abs(delta) * 0.18, 0.14, 0.30)
        home = 0.38 + delta
        away = 1.0 - home - draw
        return self._normalize_probabilities({match.home_team: home, match.away_team: away, 'draw': draw, 'Draw': draw})

    def _injury_probabilities(self, match: Match, context: MatchContext) -> dict[str, float] | None:
        details = dict(getattr(context, 'details', {}) or {})
        home_inj = self._first_float(details, 'home_injuries', 'espn_home_injuries', 'home_absences')
        away_inj = self._first_float(details, 'away_injuries', 'espn_away_injuries', 'away_absences')
        if home_inj is None and away_inj is None:
            return None
        home_inj = float(home_inj or 0.0)
        away_inj = float(away_inj or 0.0)
        delta = clamp((away_inj - home_inj) * 0.03, -0.12, 0.12)
        draw = clamp(0.24, 0.16, 0.28)
        home = 0.38 + delta
        away = 1.0 - home - draw
        return self._normalize_probabilities({match.home_team: home, match.away_team: away, 'draw': draw, 'Draw': draw})

    def _context_draw_probability(self, context: MatchContext | None) -> float | None:
        if context is None:
            return None
        details = dict(getattr(context, 'details', {}) or {})
        for key in (
            'draw_probability',
            'sstats_draw_probability',
            'api_football_draw_probability',
            'espn_draw_probability',
            'thesportsdb_draw_probability',
        ):
            value = self._safe_probability(details.get(key))
            if value is not None:
                return value
        payload = getattr(context, 'payload', None)
        if isinstance(payload, dict):
            probs = payload.get('probabilities')
            if isinstance(probs, dict):
                for key in ('draw', 'tie', 'draw_probability'):
                    value = self._safe_probability(probs.get(key))
                    if value is not None:
                        return value
        home = self._safe_probability(getattr(context, 'home_win_probability', None))
        away = self._safe_probability(getattr(context, 'away_win_probability', None))
        if home is not None and away is not None:
            return clamp(1.0 - home - away, 0.08, 0.32)
        return None

    @staticmethod
    def _normalize_probabilities(probs: dict[str, float]) -> dict[str, float]:
        team_values = {key: clamp(float(value or 0.0), 0.01, 0.98) for key, value in probs.items() if key not in {'draw', 'Draw'}}
        draw_value = clamp(float(probs.get('draw', probs.get('Draw', 0.10)) or 0.10), 0.05, 0.40)
        total = sum(team_values.values()) + draw_value
        if total <= 0:
            normalized = {**team_values, 'draw': draw_value}
        else:
            normalized = {key: value / total for key, value in team_values.items()}
            normalized['draw'] = draw_value / total
        normalized['Draw'] = normalized['draw']
        return normalized

    @staticmethod
    def _safe_probability(value: Any) -> float | None:
        try:
            if value is None:
                return None
            number = float(value)
        except Exception:
            return None
        if not math.isfinite(number):
            return None
        if number > 1.0:
            number /= 100.0
        return clamp(number, 0.01, 0.95)

    @staticmethod
    def _first_float(details: dict[str, Any], *keys: str) -> float | None:
        for key in keys:
            value = details.get(key)
            try:
                if value is None:
                    continue
                number = float(value)
            except Exception:
                continue
            if math.isfinite(number):
                return number
        return None

    @staticmethod
    def _metric01(value: Any) -> float | None:
        try:
            if value is None:
                return None
            number = float(value)
        except Exception:
            return None
        if not math.isfinite(number):
            return None
        if number > 1.0:
            number /= 100.0
        return clamp(number, 0.0, 1.0)

    def _adjust_total_probability(self, base_prob: float, point: float, expected_home: float, expected_away: float, context: MatchContext | None) -> float:
        prob = clamp(float(base_prob), 0.02, 0.98)
        details = dict(getattr(context, 'details', {}) or {}) if context is not None else {}
        env = 0.0
        form_home = self._metric01(self._first_float(details, 'espn_home_form', 'home_form', 'api_football_home_form'))
        form_away = self._metric01(self._first_float(details, 'espn_away_form', 'away_form', 'api_football_away_form'))
        if form_home is not None and form_away is not None:
            env += ((form_home + form_away) - 1.0) * 0.05
        gf_home = self._first_float(details, 'thesportsdb_home_gf_pg', 'home_gf_pg', 'sstats_home_goals_for_pg')
        gf_away = self._first_float(details, 'thesportsdb_away_gf_pg', 'away_gf_pg', 'sstats_away_goals_for_pg')
        ga_home = self._first_float(details, 'thesportsdb_home_ga_pg', 'home_ga_pg', 'sstats_home_goals_against_pg')
        ga_away = self._first_float(details, 'thesportsdb_away_ga_pg', 'away_ga_pg', 'sstats_away_goals_against_pg')
        if None not in {gf_home, gf_away, ga_home, ga_away}:
            env += ((((gf_home or 0) + (gf_away or 0) + (ga_home or 0) + (ga_away or 0)) / 4.0) - 1.15) * 0.04
        if point >= 3.5:
            env *= 0.85
        return clamp(prob + env, 0.02, 0.98)

    def _adjust_btts_probability(self, base_prob: float, expected_home: float, expected_away: float, context: MatchContext | None) -> float:
        prob = clamp(float(base_prob), 0.02, 0.98)
        details = dict(getattr(context, 'details', {}) or {}) if context is not None else {}
        boost = 0.0
        if min(expected_home, expected_away) >= 0.9:
            boost += 0.03
        if min(expected_home, expected_away) <= 0.45:
            boost -= 0.04
        form_home = self._metric01(self._first_float(details, 'espn_home_form', 'home_form', 'api_football_home_form'))
        form_away = self._metric01(self._first_float(details, 'espn_away_form', 'away_form', 'api_football_away_form'))
        if form_home is not None and form_away is not None:
            boost += ((form_home + form_away) - 1.0) * 0.03
        return clamp(prob + boost, 0.02, 0.98)


    def _calibrate_btts_selection_probability(
        self,
        selection_key: str,
        yes_prob: float,
        market_prob: float | None,
        expected_home: float,
        expected_away: float,
        context: MatchContext | None,
    ) -> float:
        prob_yes = clamp(float(yes_prob), 0.02, 0.98)
        over25 = self._context_total_probability(context, 2.5)
        if over25 is not None:
            synergy = float(getattr(self.settings, 'btts_over25_synergy_weight', 0.08) or 0.08)
            prob_yes += (float(over25) - 0.50) * synergy
        total_xg = max(float(expected_home) + float(expected_away), 0.1)
        if total_xg >= 3.1:
            prob_yes += 0.03
        elif total_xg <= 1.9:
            prob_yes -= 0.05
        if min(float(expected_home), float(expected_away)) >= 0.85:
            prob_yes += 0.025
        elif min(float(expected_home), float(expected_away)) <= 0.45:
            prob_yes -= 0.04
        market = self._to_float_safe(market_prob)
        if market is not None:
            blend = float(getattr(self.settings, 'btts_market_prior_blend', 0.12) or 0.12)
            prob_yes = prob_yes * (1.0 - blend) + market * blend
        prob_yes = clamp(prob_yes, 0.02, 0.98)
        return prob_yes if selection_key == 'yes' else clamp(1.0 - prob_yes, 0.02, 0.98)

    def _calibrate_h2h_selection_probability(
        self,
        match: Match,
        context: MatchContext | None,
        selection_key: str | None,
        base_prob: float,
        market_prob: float | None,
        xg: tuple[float, float] | None,
    ) -> float:
        prob = clamp(float(base_prob), 0.02, 0.98)
        market = self._to_float_safe(market_prob)
        side_blend = float(getattr(self.settings, 'h2h_market_prior_blend_side', 0.16) or 0.16)
        draw_blend = float(getattr(self.settings, 'h2h_market_prior_blend_draw', 0.08) or 0.08)
        if market is not None:
            blend = draw_blend if selection_key == 'draw' else side_blend
            source = str(getattr(context, 'source', '') or '')
            if source in {'espn', 'api_football', 'ensemble'}:
                blend = max(0.03, blend - 0.03)
            prob = prob * (1.0 - blend) + market * blend
        if xg is not None:
            expected_home, expected_away = float(xg[0]), float(xg[1])
            gap = expected_home - expected_away
            if selection_key == 'home':
                prob += clamp(gap * 0.05, -0.05, 0.05)
            elif selection_key == 'away':
                prob += clamp((-gap) * 0.05, -0.05, 0.05)
            elif selection_key == 'draw':
                prob -= clamp(abs(gap) * 0.06, 0.0, 0.12)
        if selection_key == 'draw':
            cap = float(getattr(self.settings, 'h2h_draw_probability_cap', 0.34) or 0.34)
            return clamp(prob, 0.05, cap)
        return clamp(prob, 0.04, 0.88)

    def _adjust_team_total_probability(self, base_prob: float, lam: float, point: float, team_side: str, context: MatchContext | None) -> float:
        prob = clamp(float(base_prob), 0.02, 0.98)
        details = dict(getattr(context, 'details', {}) or {}) if context is not None else {}
        att_key = 'api_football_home_attack' if team_side == 'home' else 'api_football_away_attack'
        form_key = 'espn_home_form' if team_side == 'home' else 'espn_away_form'
        boost = 0.0
        attack = self._metric01(self._first_float(details, att_key, 'home_attack' if team_side == 'home' else 'away_attack'))
        if attack is not None:
            boost += (attack - 0.5) * 0.08
        form = self._metric01(self._first_float(details, form_key, 'home_form' if team_side == 'home' else 'away_form'))
        if form is not None:
            boost += (form - 0.5) * 0.05
        if lam < 0.75 and point >= 1.5:
            boost -= 0.03
        return clamp(prob + boost, 0.02, 0.98)

    def _signal_stack_label(self, context: MatchContext | None) -> str:
        if context is None:
            return 'xg'
        details = dict(getattr(context, 'details', {}) or {})
        labels = ['xg']
        if self._explicit_h2h_probabilities_dummy(context):
            labels.append('explicit')
        if self._first_float(details, 'thesportsdb_home_ppg', 'home_ppg', 'sstats_home_ppg') is not None:
            labels.append('table')
        if self._first_float(details, 'espn_home_form', 'home_form', 'api_football_home_form') is not None:
            labels.append('form')
        if self._first_float(details, 'api_football_home_attack', 'home_attack') is not None:
            labels.append('attack_defense')
        if self._first_float(details, 'home_rest_days', 'espn_home_rest_days') is not None:
            labels.append('rest')
        if self._first_float(details, 'home_injuries', 'espn_home_injuries', 'home_absences') is not None or self._first_float(details, 'away_injuries', 'espn_away_injuries', 'away_absences') is not None:
            labels.append('injuries')
        if self._first_float(details, 'home_absences', 'away_absences') is not None:
            labels.append('news')
        return '+'.join(labels[:5])

    def _explicit_h2h_probabilities_dummy(self, context: MatchContext) -> bool:
        return self._safe_probability(getattr(context, 'home_win_probability', None)) is not None and self._safe_probability(getattr(context, 'away_win_probability', None)) is not None

    def _context_total_probability(self, context: MatchContext | None, point: float | None) -> float | None:
        if context is None or point is None:
            return None
        details = dict(getattr(context, 'details', {}) or {})
        direct = self._context_total_probability_for_key(details, float(point))
        if direct is not None:
            return direct
        frac = round(float(point) - math.floor(float(point)), 2)
        if frac in {0.25, 0.75}:
            low = self._context_total_probability_for_key(details, round(float(point) - 0.25, 2))
            high = self._context_total_probability_for_key(details, round(float(point) + 0.25, 2))
            if low is not None and high is not None:
                return clamp((low + high) / 2.0, 0.02, 0.98)
        return None

    @staticmethod
    def _context_total_probability_for_key(details: dict[str, Any], point: float) -> float | None:
        point_key = f"prob_over_{str(round(point, 2)).replace('.', '_')}"
        raw = details.get(point_key)
        try:
            if raw is None:
                return None
            value = float(raw)
        except Exception:
            return None
        if value > 1.0:
            value /= 100.0
        return clamp(value, 0.02, 0.98)

    def _normalize_supported_line(self, point: float | None, family: str) -> float | None:
        if point is None:
            return None
        try:
            raw_point = round(float(point), 2)
        except Exception:
            return None
        allowed = sorted(self.settings.supported_lines_for_family(family))
        if not allowed:
            return raw_point
        tolerance = float(getattr(self.settings, 'line_support_tolerance', 0.06) or 0.06)
        nearest = min(allowed, key=lambda item: abs(item - raw_point))
        if abs(nearest - raw_point) <= tolerance:
            return round(float(nearest), 2)
        return None

    def _poisson_line_probability(self, lam: float | None, point: float | None) -> float | None:
        if lam is None or point is None:
            return None
        frac = round(float(point) - math.floor(float(point)), 2)
        if frac in {0.25, 0.75}:
            lower = round(float(point) - 0.25, 2)
            upper = round(float(point) + 0.25, 2)
            low_prob = poisson_over_probability(float(lam), lower)
            high_prob = poisson_over_probability(float(lam), upper)
            if low_prob is None or high_prob is None:
                return None
            return clamp((float(low_prob) + float(high_prob)) / 2.0, 0.02, 0.98)
        direct = poisson_over_probability(float(lam), float(point))
        if direct is None:
            return None
        return clamp(float(direct), 0.02, 0.98)


    def _alias_groups(self) -> dict[str, str]:
        mapping: dict[str, str] = {}
        raw_groups = list(getattr(self.settings, 'consensus_alias_groups', []) or [])
        for raw in raw_groups:
            tokens = [self._norm_book_base(part) for part in str(raw).split('|') if self._norm_book_base(part)]
            if not tokens:
                continue
            canonical = tokens[0]
            for token in tokens:
                mapping[token] = canonical
        return mapping

    @staticmethod
    def _norm_book_base(value: str) -> str:
        text = str(value or '').strip().lower()
        for needle in (' bookmaker', 'sportsbook', ' exchange'):
            text = text.replace(needle, '')
        text = text.replace('_', ' ').replace('-', ' ')
        text = ''.join(ch for ch in text if ch.isalnum() or ch.isspace())
        return ' '.join(text.split())

    def _weighted_unique_books(self, offers: list[Offer]) -> float:
        seen: dict[str, float] = {}
        for offer in offers:
            key = self._norm_book(offer.bookmaker)
            if not key:
                continue
            seen[key] = max(seen.get(key, 0.0), self._bookmaker_weight(key))
        return sum(min(weight, 1.15) for weight in seen.values())

    def _has_sharp_book(self, offers: list[Offer]) -> bool:
        sharp = {self._norm_book(item) for item in getattr(self.settings, 'sharp_bookmakers', []) or []}
        return any(self._norm_book(offer.bookmaker) in sharp for offer in offers)


    def _required_books_for_bucket(self, family: str, point: float | None, offers: list[Offer], context: MatchContext | None) -> int:
        base = self.settings.min_books_for_family(family)
        if base <= 1:
            return 1
        norm_books = {self._norm_book(offer.bookmaker) for offer in offers if str(offer.bookmaker or '').strip()}
        weighted_books = self._weighted_unique_books(offers)
        has_preferred_book = bool(norm_books & self.target_books) or bool(norm_books & {'bet365', 'unibet', 'pinnacle', 'betfair'})
        has_sharp = self._has_sharp_book(offers)
        context_source = str(getattr(context, 'source', '') or '') if context is not None else ''
        if family == 'totals' and point in {2.5, 3.5, 4.5} and has_preferred_book and context_source in {'bzzoiro_predictions', 'sstats_form', 'sstats', 'ensemble', 'api_football', 'espn', 'thesportsdb'}:
            return 1
        if weighted_books >= float(getattr(self.settings, 'min_weighted_books_for_consensus', 1.75) or 1.75):
            return min(base, 2)
        if getattr(self.settings, 'allow_single_sharp_book', True) and has_sharp:
            return min(base, 2)
        return base

    @staticmethod
    def _select_best_offer(offers: list[Offer]) -> Offer:
        def key(offer: Offer) -> tuple[float, float, str, str]:
            return (float(offer.price), BOOKMAKER_WEIGHTS.get(str(offer.bookmaker), 1.0), str(offer.source), str(offer.bookmaker))
        return max(offers, key=key)

    def _candidate_rank_key(self, item: CandidateBet) -> tuple[float, float, float, float]:
        family_weight = float(self.settings.score_weight_for_family(item.family))
        return (
            round(item.ev_pct * family_weight, 3),
            round(item.edge_pct * family_weight, 3),
            round(item.confidence, 3),
            round(item.adjusted_probability, 4),
        )

    def _filter_and_rank(self, candidates: list[CandidateBet], rejections: dict[str, int]) -> list[CandidateBet]:
        filtered: list[CandidateBet] = []
        for item in candidates:
            min_conf = float(self.settings.min_model_confidence_for_family(item.family))
            min_ev = float(self.settings.min_ev_pct_for_family(item.family))
            min_edge = float(self.settings.min_edge_pct_for_family(item.family))
            if item.model_probability < min_conf:
                rejections['confidence_below_threshold'] += 1
                continue
            if item.ev_pct < min_ev:
                rejections['ev_below_threshold'] += 1
                continue
            if item.edge_pct < min_edge:
                rejections['edge_below_threshold'] += 1
                continue
            context_source = str((item.source_summary or {}).get('context_source') or '')
            if item.family == 'totals' and item.books_count == 1:
                if item.confidence < 58.0 or item.edge_pct < 7.0:
                    rejections['single_book_total_guard'] += 1
                    continue
            if item.family == 'h2h' and context_source == 'sstats_form':
                odds_value = float(item.odds)
                edge_prob = float(item.adjusted_probability) - float(item.market_probability)
                if odds_value >= 4.0:
                    if not (float(item.confidence) >= 65.0 or edge_prob >= 0.12):
                        rejections['very_high_odds_form_guard'] += 1
                        continue
                elif odds_value >= 3.0:
                    if not (item.sources_count >= 2 or float(item.confidence) >= 64.0 or edge_prob >= 0.10):
                        rejections['high_odds_form_guard'] += 1
                        continue
            if item.family == 'h2h':
                selection_text = str(item.selection or '').lower()
                is_draw = 'ничья' in selection_text or selection_text == 'draw'
                if is_draw:
                    if float(item.confidence) < float(getattr(self.settings, 'h2h_draw_min_confidence', 61.0) or 61.0):
                        rejections['h2h_draw_confidence_guard'] += 1
                        continue
                    if float(item.edge_pct) < float(getattr(self.settings, 'h2h_draw_min_edge_pct', 3.2) or 3.2):
                        rejections['h2h_draw_edge_guard'] += 1
                        continue
                    if float(item.ev_pct) < float(getattr(self.settings, 'h2h_draw_min_ev_pct', 2.4) or 2.4):
                        rejections['h2h_draw_ev_guard'] += 1
                        continue
                    xg_pair = (item.expected_home, item.expected_away)
                    if xg_pair[0] is not None and xg_pair[1] is not None and abs(float(xg_pair[0]) - float(xg_pair[1])) > 0.85:
                        rejections['draw_signal_too_imbalanced'] += 1
                        continue
                else:
                    if float(item.confidence) < float(getattr(self.settings, 'h2h_side_min_confidence', 56.0) or 56.0):
                        rejections['h2h_side_confidence_guard'] += 1
                        continue
                    if float(item.edge_pct) < float(getattr(self.settings, 'h2h_side_min_edge_pct', 2.0) or 2.0):
                        rejections['h2h_side_edge_guard'] += 1
                        continue
                    if float(item.ev_pct) < float(getattr(self.settings, 'h2h_side_min_ev_pct', 1.4) or 1.4):
                        rejections['h2h_side_ev_guard'] += 1
                        continue
                    if item.sources_count <= 1:
                        if float(item.confidence) < float(getattr(self.settings, 'h2h_single_source_min_confidence', 62.0) or 62.0):
                            rejections['h2h_single_source_confidence_guard'] += 1
                            continue
                        if float(item.edge_pct) < float(getattr(self.settings, 'h2h_single_source_min_edge_pct', 4.2) or 4.2):
                            rejections['h2h_single_source_edge_guard'] += 1
                            continue
                        if float(item.ev_pct) < float(getattr(self.settings, 'h2h_single_source_min_ev_pct', 3.0) or 3.0):
                            rejections['h2h_single_source_ev_guard'] += 1
                            continue
                    if bool(getattr(self.settings, 'h2h_xg_dislocation_guard_enabled', True)):
                        expected_home = item.expected_home
                        expected_away = item.expected_away
                        if expected_home is not None and expected_away is not None:
                            stronger_side = None
                            xg_diff = float(expected_home) - float(expected_away)
                            if xg_diff >= 0:
                                stronger_side = 'home'
                            else:
                                stronger_side = 'away'
                            selected_side = 'home' if item.selection == item.home_team else ('away' if item.selection == item.away_team else None)
                            if selected_side and stronger_side == selected_side:
                                if abs(xg_diff) >= float(getattr(self.settings, 'h2h_xg_dislocation_min_diff', 1.60) or 1.60):
                                    if float(item.market_probability) <= float(getattr(self.settings, 'h2h_xg_dislocation_market_max_prob', 0.37) or 0.37):
                                        min_sources = max(1, int(getattr(self.settings, 'h2h_xg_dislocation_min_sources', 2) or 2))
                                        min_conf = float(getattr(self.settings, 'h2h_xg_dislocation_min_confidence', 66.0) or 66.0)
                                        if item.sources_count < min_sources and float(item.confidence) < min_conf:
                                            rejections['h2h_xg_market_dislocation_guard'] += 1
                                            continue
            if item.family == 'btts':
                selection_text = str(item.selection or '').lower()
                is_yes = 'да' in selection_text or 'yes' in selection_text
                if is_yes:
                    if float(item.confidence) < float(getattr(self.settings, 'btts_yes_min_confidence', 56.0) or 56.0):
                        rejections['btts_yes_confidence_guard'] += 1
                        continue
                    if float(item.edge_pct) < float(getattr(self.settings, 'btts_yes_min_edge_pct', 1.8) or 1.8):
                        rejections['btts_yes_edge_guard'] += 1
                        continue
                    if float(item.ev_pct) < float(getattr(self.settings, 'btts_yes_min_ev_pct', 1.3) or 1.3):
                        rejections['btts_yes_ev_guard'] += 1
                        continue
                else:
                    if float(item.confidence) < float(getattr(self.settings, 'btts_no_min_confidence', 55.0) or 55.0):
                        rejections['btts_no_confidence_guard'] += 1
                        continue
                    if float(item.edge_pct) < float(getattr(self.settings, 'btts_no_min_edge_pct', 1.6) or 1.6):
                        rejections['btts_no_edge_guard'] += 1
                        continue
                    if float(item.ev_pct) < float(getattr(self.settings, 'btts_no_min_ev_pct', 1.1) or 1.1):
                        rejections['btts_no_ev_guard'] += 1
                        continue
            filtered.append(item)

        filtered.sort(key=self._candidate_rank_key, reverse=True)
        deduped: list[CandidateBet] = []
        used_matches: set[str] = set()
        league_counts: Counter[str] = Counter()
        family_counts: Counter[str] = Counter()
        seen_reason_signatures: Counter[tuple[str, str]] = Counter()
        max_per_league = max(1, int(getattr(self.settings, 'max_picks_per_league', 2) or 2))
        max_per_family = max(1, int(getattr(self.settings, 'max_picks_per_family', 2) or 2))
        max_same_reason = max(1, int(getattr(self.settings, 'max_same_reason_signature', 2) or 2))

        for item in filtered:
            if item.match_key in used_matches:
                continue
            if league_counts[item.league_name] >= max_per_league:
                rejections['league_diversity_guard'] += 1
                continue
            if family_counts[item.family] >= max_per_family:
                rejections['family_diversity_guard'] += 1
                continue
            signature = (item.family, '|'.join(sorted(str(reason) for reason in item.reasons[:3])))
            if seen_reason_signatures[signature] >= max_same_reason:
                rejections['reason_signature_diversity_guard'] += 1
                continue
            used_matches.add(item.match_key)
            league_counts[item.league_name] += 1
            family_counts[item.family] += 1
            seen_reason_signatures[signature] += 1
            deduped.append(item)
            if len(deduped) >= self.settings.max_picks_per_run:
                break
        if deduped or not getattr(self.settings, 'fallback_publish_mode_enabled', True):
            return deduped
        fallback_min_ev = float(getattr(self.settings, 'fallback_publish_min_ev_pct', 2.0) or 2.0)
        fallback_min_edge = float(getattr(self.settings, 'fallback_publish_min_edge_pct', 2.5) or 2.5)
        fallback_min_conf = float(getattr(self.settings, 'fallback_publish_min_confidence', 54.0) or 54.0)
        fallback_min_books = max(1, int(getattr(self.settings, 'fallback_publish_min_books', 2) or 2))
        allowed_families = {'totals', 'h2h', 'btts', 'dnb', 'doubleChance', 'teamTotals'}
        for item in sorted(candidates, key=self._candidate_rank_key, reverse=True):
            if item.family not in allowed_families:
                continue
            if float(item.confidence) < fallback_min_conf:
                continue
            if float(item.ev_pct) < fallback_min_ev or float(item.edge_pct) < fallback_min_edge:
                continue
            if int(getattr(item, 'books_count', 0) or 0) < fallback_min_books:
                continue
            if item.expected_home is not None and float(item.expected_home) < 0:
                continue
            if item.expected_away is not None and float(item.expected_away) < 0:
                continue
            try:
                item.reasons.append('fallback_publish_mode=enabled')
                if isinstance(item.source_summary, dict):
                    item.source_summary['fallback_publish_mode'] = True
            except Exception:
                pass
            rejections['fallback_publish_mode_used'] += 1
            return [item]
        rejections['fallback_publish_no_candidate'] += 1
        return deduped

    @staticmethod
    def _is_valid_probability(value: Any) -> bool:
        try:
            if value is None:
                return False
            number = float(value)
        except Exception:
            return False
        return math.isfinite(number) and 0.0 <= number <= 1.0

    @staticmethod
    def _btts_yes_probability(expected_home: float, expected_away: float) -> float:
        return clamp((1.0 - math.exp(-expected_home)) * (1.0 - math.exp(-expected_away)), 0.02, 0.98)


    def _norm_book(self, value: str) -> str:
        base = self._norm_book_base(value)
        if not getattr(self.settings, 'bookmaker_alias_relaxed', True):
            return base.replace(' ', '')
        alias = self._alias_groups().get(base)
        if alias:
            return alias.replace(' ', '')
        return base.replace(' ', '')

    def _is_target_or_consensus_book(self, bookmaker: str) -> bool:
        key = self._norm_book(bookmaker)
        return not (self.target_books or self.consensus_books) or key in self.target_books or key in self.consensus_books

    @staticmethod
    def _context_label(context: MatchContext | None) -> str:
        if context is None:
            return 'none'
        source = str(getattr(context, 'source', '') or 'unknown')
        details = dict(getattr(context, 'details', {}) or {})
        mode = str(details.get('sstats_mode') or details.get('context_mode') or '').strip()
        return f'{source}:{mode}' if mode else source

    @staticmethod
    def _coerce_context(value: Any) -> MatchContext | None:
        if value is None:
            return None
        if isinstance(value, MatchContext):
            return value
        if isinstance(value, dict):
            return MatchContext(
                source=str(value.get('source', 'unknown')),
                payload=value.get('payload', value),
                expected_home=value.get('expected_home'),
                expected_away=value.get('expected_away'),
                home_win_probability=value.get('home_win_probability'),
                away_win_probability=value.get('away_win_probability'),
                confidence=float(value.get('confidence', 58.0) or 58.0),
                details=value.get('details', {}),
            )
        return None


ValueModel = CandidateFactory
