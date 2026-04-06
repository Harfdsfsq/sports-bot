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

    def build_candidates(
        self,
        matches: list[Match],
        offers_by_match: dict[str, list[Offer]],
        contexts_by_match: dict[str, Any],
    ) -> tuple[list[CandidateBet], dict[str, int], dict[str, Any]]:
        candidates: list[CandidateBet] = []
        rejections: dict[str, int] = defaultdict(int)
        debug_rows: list[dict[str, Any]] = []
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
        xg = self._validated_expected_goals(context)
        if xg is None:
            rejections['missing_context_totals'] += 1
            return []
        expected_home, expected_away = xg

        buckets: dict[tuple[str, float | None], list[Offer]] = defaultdict(list)
        for offer in offers:
            buckets[(offer.selection, offer.point)].append(offer)

        allowed_total_points = {1.5, 2.5, 3.5, 4.5}
        expected_total = expected_home + expected_away
        result: list[CandidateBet] = []
        for (selection, point), bucket in buckets.items():
            low = str(selection or '').lower()
            if point is None or not (low.startswith('over') or low.startswith('under')):
                continue
            point = round(float(point), 2)
            if point not in allowed_total_points:
                rejections['unsupported_total_line'] += 1
                continue
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
                over_prob = poisson_over_probability(expected_total, point)
                model_reason = 'xg_total'
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

        buckets: dict[str, list[Offer]] = defaultdict(list)
        for offer in offers:
            buckets[offer.selection].append(offer)

        result: list[CandidateBet] = []
        for selection, bucket in buckets.items():
            required_books = self._required_books_for_bucket('h2h', None, bucket, context)
            if len({self._norm_book(item.bookmaker) for item in bucket}) < required_books:
                rejections['insufficient_books'] += 1
                continue
            model_prob = probs.get(selection)
            if model_prob is None:
                continue
            market_prob = self._fair_market_probability_h2h(match, offers, selection)
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
                    'model=1x2_ensemble',
                    f'context={self._context_label(context)}',
                ],
                expected_home=self._validated_expected_goals(context)[0] if self._validated_expected_goals(context) else None,
                expected_away=self._validated_expected_goals(context)[1] if self._validated_expected_goals(context) else None,
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
        xg = self._validated_expected_goals(context)
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
                reasons=['mode=xg_spread', 'model=xg_spread', f'context={self._context_label(context)}'],
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
        xg = self._validated_expected_goals(context)
        if xg is None:
            rejections['missing_context_btts'] += 1
            return []
        expected_home, expected_away = xg
        yes_prob = self._btts_yes_probability(expected_home, expected_away)
        if not self._is_valid_probability(yes_prob):
            rejections['missing_model_probability_btts'] += 1
            return []
        buckets: dict[str, list[Offer]] = defaultdict(list)
        for offer in offers:
            key = self._yes_no_key(offer.selection)
            if key:
                buckets[key].append(offer)
        result: list[CandidateBet] = []
        for key, bucket in buckets.items():
            required_books = self._required_books_for_bucket('btts', None, bucket, context)
            if len({self._norm_book(item.bookmaker) for item in bucket}) < required_books:
                rejections['insufficient_books'] += 1
                continue
            market_prob = self._fair_market_probability_yes_no(bucket, offers, key, selector=self._yes_no_key)
            model_prob = float(yes_prob) if key == 'yes' else 1.0 - float(yes_prob)
            candidate = self._candidate_from_bucket(
                match=match,
                family='btts',
                selection='Обе забьют: Да' if key == 'yes' else 'Обе забьют: Нет',
                point=None,
                offers=bucket,
                market_prob=market_prob,
                model_prob=model_prob,
                reasons=['mode=btts_poisson', 'model=btts_from_xg', f'context={self._context_label(context)}'],
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
        xg = self._validated_expected_goals(context)
        if xg is None:
            rejections['missing_context_team_totals'] += 1
            return []
        expected_home, expected_away = xg
        buckets: dict[tuple[str, float | None, str], list[Offer]] = defaultdict(list)
        for offer in offers:
            buckets[(offer.selection, offer.point, str(offer.team_side or '').lower())].append(offer)
        result: list[CandidateBet] = []
        for (selection, point, team_side), bucket in buckets.items():
            low = str(selection or '').lower()
            if point is None or team_side not in {'home', 'away'}:
                continue
            if not (low.startswith('over') or low.startswith('under')):
                continue
            required_books = self._required_books_for_bucket('teamTotals', point, bucket, context)
            if len({self._norm_book(item.bookmaker) for item in bucket}) < required_books:
                rejections['insufficient_books'] += 1
                continue
            lam = expected_home if team_side == 'home' else expected_away
            over_prob = poisson_over_probability(lam, float(point))
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
                reasons=['mode=team_total_poisson', f'model={team_side}_team_total', f'context={self._context_label(context)}'],
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
            },
        )

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
        for raw_name, weight in BOOKMAKER_WEIGHTS.items():
            if self._norm_book(raw_name) == normalized:
                return weight
        return 1.0

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

    def _derive_h2h_probabilities(self, match: Match, context: MatchContext | None) -> dict[str, float] | None:
        if context is None:
            return None
        weighted_parts: list[tuple[dict[str, float], float]] = []
        xg = self._validated_expected_goals(context)
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
            }), 0.42))
        explicit = self._explicit_h2h_probabilities(match, context)
        if explicit is not None:
            source = str(context.source or '')
            explicit_weight = 0.52 if source in {'api_football', 'espn', 'ensemble', 'thesportsdb'} else 0.40
            weighted_parts.append((explicit, explicit_weight))
        strength = self._strength_probabilities(match, context)
        if strength is not None:
            weighted_parts.append((strength, 0.18))
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
        ppg_home = self._first_float(details, 'thesportsdb_home_ppg', 'home_ppg')
        ppg_away = self._first_float(details, 'thesportsdb_away_ppg', 'away_ppg')
        if ppg_home is not None and ppg_away is not None:
            delta += clamp((ppg_home - ppg_away) * 0.12, -0.18, 0.18)
            parts += 1
        rank_home = self._first_float(details, 'thesportsdb_home_rank', 'home_rank')
        rank_away = self._first_float(details, 'thesportsdb_away_rank', 'away_rank')
        if rank_home is not None and rank_away is not None:
            delta += clamp((rank_away - rank_home) * 0.015, -0.14, 0.14)
            parts += 1
        form_home = self._first_float(details, 'espn_home_form', 'home_form')
        form_away = self._first_float(details, 'espn_away_form', 'away_form')
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

    def _context_total_probability(self, context: MatchContext | None, point: float | None) -> float | None:
        if context is None or point is None:
            return None
        details = dict(getattr(context, 'details', {}) or {})
        point_key = f"prob_over_{str(point).replace('.', '_')}"
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

    def _required_books_for_bucket(self, family: str, point: float | None, offers: list[Offer], context: MatchContext | None) -> int:
        base = max(1, int(getattr(self.settings, 'min_books_publish', 1) or 1))
        if base <= 1:
            return 1
        norm_books = {self._norm_book(offer.bookmaker) for offer in offers if str(offer.bookmaker or '').strip()}
        has_preferred_book = bool(norm_books & self.target_books) or bool(norm_books & {'bet365', 'unibet'})
        context_source = str(getattr(context, 'source', '') or '') if context is not None else ''
        if family == 'totals' and point in {2.5, 3.5, 4.5} and has_preferred_book and context_source in {'bzzoiro_predictions', 'sstats_form', 'sstats', 'ensemble', 'api_football'}:
            return 1
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
            if item.model_probability < self.settings.min_model_confidence:
                rejections['confidence_below_threshold'] += 1
                continue
            if item.ev_pct < self.settings.min_ev_pct:
                rejections['edge_below_threshold'] += 1
                continue
            if item.edge_pct < self.settings.min_edge_pct:
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
            if item.family == 'h2h' and 'ничья' in str(item.selection).lower():
                xg_pair = (item.expected_home, item.expected_away)
                if xg_pair[0] is not None and xg_pair[1] is not None and abs(float(xg_pair[0]) - float(xg_pair[1])) > 0.85:
                    rejections['draw_signal_too_imbalanced'] += 1
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

    @staticmethod
    def _norm_book(value: str) -> str:
        return str(value or '').strip().lower()

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
