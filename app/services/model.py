from __future__ import annotations

from collections import defaultdict
from statistics import mean
from typing import Any

from app.config import Settings
from app.schemas import CandidateBet, Match, MatchContext, Offer
from app.utils import clamp, implied_probability, poisson_over_probability, russian_selection, shrink_probability

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

            match_candidates.sort(key=lambda item: (item.ev_pct, item.edge_pct, item.confidence), reverse=True)
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
        if context is None or context.expected_home is None or context.expected_away is None:
            rejections['missing_context_totals'] += 1
            return []

        buckets: dict[tuple[str, float | None], list[Offer]] = defaultdict(list)
        for offer in offers:
            buckets[(offer.selection, offer.point)].append(offer)

        allowed_total_points = {1.5, 2.5, 3.5, 4.5}
        expected_total = context.expected_home + context.expected_away
        result: list[CandidateBet] = []
        for (selection, point), bucket in buckets.items():
            low = selection.lower()
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
            market_prob = mean(implied_probability(item.price) for item in bucket)
            explicit_total_prob = self._context_total_probability(context, point)
            if explicit_total_prob is not None:
                over_prob = explicit_total_prob
                model_reason = 'context_total_probability'
            else:
                over_prob = poisson_over_probability(expected_total, point)
                model_reason = 'xg_total'
            model_prob = over_prob if low.startswith('over') else (1.0 - over_prob)
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
                    f'consensus_fair_odds={1 / max(market_prob, 0.01):.2f}',
                    f'line={point:g}',
                    f'context={self._context_label(context)}',
                ],
                expected_home=context.expected_home,
                expected_away=context.expected_away,
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
        if context is None or context.expected_home is None or context.expected_away is None:
            rejections['missing_context_h2h'] += 1
            return []
        buckets: dict[str, list[Offer]] = defaultdict(list)
        for offer in offers:
            buckets[offer.selection].append(offer)

        probs = self._derive_h2h_probabilities(match, context)
        result: list[CandidateBet] = []
        for selection, bucket in buckets.items():
            required_books = self._required_books_for_bucket('h2h', None, bucket, context)
            if len({self._norm_book(item.bookmaker) for item in bucket}) < required_books:
                rejections['insufficient_books'] += 1
                continue
            model_prob = probs.get(selection)
            if model_prob is None:
                continue
            market_prob = mean(implied_probability(item.price) for item in bucket)
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
                    'model=1x2_from_xg',
                    f'context={self._context_label(context)}',
                ],
                expected_home=context.expected_home,
                expected_away=context.expected_away,
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
        if context is None or context.expected_home is None or context.expected_away is None:
            rejections['missing_context_spreads'] += 1
            return []
        result: list[CandidateBet] = []
        diff = context.expected_home - context.expected_away
        for offer in offers:
            if offer.point is None:
                continue
            books = [item for item in offers if item.selection == offer.selection and item.point == offer.point]
            required_books = self._required_books_for_bucket('spreads', offer.point, books, context)
            if len({self._norm_book(item.bookmaker) for item in books}) < required_books:
                rejections['insufficient_books'] += 1
                continue
            team_side = (offer.team_side or '').lower()
            if not team_side:
                continue
            if team_side == 'home':
                model_prob = clamp(0.50 + diff * 0.10, 0.05, 0.95)
            else:
                model_prob = clamp(0.50 - diff * 0.10, 0.05, 0.95)
            market_prob = mean(implied_probability(item.price) for item in books)
            candidate = self._candidate_from_bucket(
                match=match,
                family='spreads',
                selection=offer.selection,
                point=offer.point,
                offers=books,
                market_prob=market_prob,
                model_prob=model_prob,
                reasons=[
                    'mode=xg_spread',
                    'model=xg_spread',
                    f'context={self._context_label(context)}',
                ],
                expected_home=context.expected_home,
                expected_away=context.expected_away,
                model_mode='xg_spread',
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

        base_confidence = clamp(48 + len(books) * 4 + len(sources) * 5, 0, 100)
        context_confidence = float(getattr(context, 'confidence', 58.0) or 58.0) if context is not None else base_confidence
        confidence = (base_confidence * 0.60) + (context_confidence * 0.40)
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
        confidence = clamp(confidence, 0, 100)

        adjusted = shrink_probability(model_prob, market_prob, confidence, shrink_min, shrink_max)
        fair_odds = 1.0 / max(adjusted, 0.01)
        ev_pct = (adjusted * best_price - 1.0) * 100.0
        edge_pct = (adjusted - market_prob) * 100.0

        context_details = dict(getattr(context, 'details', {}) or {}) if context is not None else {}
        reasons = list(reasons)
        reasons.append(f'selected_book={best_offer.bookmaker}')
        reasons.append(f'selected_source={best_offer.source}')
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
                'context_mode': context_details.get('sstats_mode'),
                'home_recent_count': context_details.get('home_recent_count'),
                'away_recent_count': context_details.get('away_recent_count'),
                'home_goals_for_avg': context_details.get('home_goals_for_avg'),
                'home_goals_against_avg': context_details.get('home_goals_against_avg'),
                'away_goals_for_avg': context_details.get('away_goals_for_avg'),
                'away_goals_against_avg': context_details.get('away_goals_against_avg'),
                'raw_model_probability': round(float(model_prob), 4),
                'adjusted_probability': round(float(adjusted), 4),
                'market_probability': round(float(market_prob), 4),
            },
        )

    def _derive_h2h_probabilities(self, match: Match, context: MatchContext) -> dict[str, float]:
        denom = max((context.expected_home or 0.0) + (context.expected_away or 0.0), 0.1)
        xg_home = clamp((context.expected_home or 0.0) / denom, 0.08, 0.80)
        xg_away = clamp((context.expected_away or 0.0) / denom, 0.08, 0.80)
        gap = abs((context.expected_home or 0.0) - (context.expected_away or 0.0))
        xg_draw = clamp(0.28 - gap * 0.06, 0.10, 0.30)
        xg_probs = self._normalize_probabilities({
            match.home_team: xg_home,
            match.away_team: xg_away,
            'draw': xg_draw,
            'Draw': xg_draw,
        })

        ctx_home = self._safe_probability(context.home_win_probability)
        ctx_away = self._safe_probability(context.away_win_probability)
        if ctx_home is None or ctx_away is None:
            return xg_probs

        ctx_draw = clamp(1.0 - ctx_home - ctx_away, 0.08, 0.30)
        ctx_probs = self._normalize_probabilities({
            match.home_team: ctx_home,
            match.away_team: ctx_away,
            'draw': ctx_draw,
            'Draw': ctx_draw,
        })

        source = str(context.source or '')
        if source == 'sstats_form':
            xg_weight = 0.42
            ctx_weight = 0.58
        elif source == 'bzzoiro_predictions':
            xg_weight = 0.30
            ctx_weight = 0.70
        else:
            xg_weight = 0.60
            ctx_weight = 0.40

        blended = {
            match.home_team: xg_probs[match.home_team] * xg_weight + ctx_probs[match.home_team] * ctx_weight,
            match.away_team: xg_probs[match.away_team] * xg_weight + ctx_probs[match.away_team] * ctx_weight,
            'draw': xg_probs['draw'] * xg_weight + ctx_probs['draw'] * ctx_weight,
            'Draw': xg_probs['Draw'] * xg_weight + ctx_probs['Draw'] * ctx_weight,
        }
        return self._normalize_probabilities(blended)

    @staticmethod
    def _normalize_probabilities(probs: dict[str, float]) -> dict[str, float]:
        team_values = {
            key: clamp(float(value or 0.0), 0.01, 0.98)
            for key, value in probs.items()
            if key not in {'draw', 'Draw'}
        }
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
            return clamp(float(value), 0.01, 0.95)
        except Exception:
            return None

    def _context_total_probability(self, context: MatchContext | None, point: float | None) -> float | None:
        if context is None or point is None:
            return None
        details = dict(getattr(context, 'details', {}) or {})
        point_key = f'prob_over_{str(point).replace('.', '_')}'
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

    def _required_books_for_bucket(
        self,
        family: str,
        point: float | None,
        offers: list[Offer],
        context: MatchContext | None,
    ) -> int:
        base = max(1, int(getattr(self.settings, 'min_books_publish', 1) or 1))
        if base <= 1:
            return 1

        norm_books = {self._norm_book(offer.bookmaker) for offer in offers if str(offer.bookmaker or '').strip()}
        has_preferred_book = bool(norm_books & self.target_books) or bool(norm_books & {'bet365', 'unibet'})
        context_source = str(getattr(context, 'source', '') or '') if context is not None else ''

        # Permit a single strong bookmaker only for mainstream totals,
        # and only when there is model context beyond raw odds.
        if family == 'totals' and point in {2.5, 3.5} and has_preferred_book and context_source in {'bzzoiro_predictions', 'sstats_form', 'sstats'}:
            return 1

        return base

    @staticmethod
    def _select_best_offer(offers: list[Offer]) -> Offer:
        def key(offer: Offer) -> tuple[float, float, str, str]:
            return (
                float(offer.price),
                BOOKMAKER_WEIGHTS.get(str(offer.bookmaker), 1.0),
                str(offer.source),
                str(offer.bookmaker),
            )

        return max(offers, key=key)

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
            filtered.append(item)
        filtered.sort(
            key=lambda item: (round(item.ev_pct, 3), round(item.edge_pct, 3), round(item.confidence, 3)),
            reverse=True,
        )
        deduped: list[CandidateBet] = []
        used_matches: set[str] = set()
        for item in filtered:
            if item.match_key in used_matches:
                continue
            used_matches.add(item.match_key)
            deduped.append(item)
            if len(deduped) >= self.settings.max_picks_per_run:
                break
        return deduped

    @staticmethod
    def _norm_book(value: str) -> str:
        return value.strip().lower()

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
