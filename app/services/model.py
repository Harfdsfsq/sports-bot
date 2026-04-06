from __future__ import annotations

import math
from collections import defaultdict
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
                match_candidates.extend(
                    self._build_totals_candidates(match, families['totals'], context, rejections)
                )
            if families.get('h2h'):
                match_candidates.extend(
                    self._build_h2h_candidates(match, families['h2h'], context, rejections)
                )
            if families.get('spreads'):
                match_candidates.extend(
                    self._build_spread_candidates(match, families['spreads'], context, rejections)
                )

            match_candidates.sort(
                key=lambda item: (item.ev_pct, item.edge_pct, item.confidence),
                reverse=True,
            )
            if match_candidates:
                candidates.append(match_candidates[0])
                debug_rows.append(
                    {
                        'match_key': match_key,
                        'picked': match_candidates[0].selection,
                        'count': len(match_candidates),
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

        expected_total = context.expected_home + context.expected_away
        result: list[CandidateBet] = []

        for (selection, point), bucket in buckets.items():
            low = selection.lower()
            if point is None or not (low.startswith('over') or low.startswith('under')):
                continue

            if len({self._norm_book(item.bookmaker) for item in bucket}) < self.settings.min_books_publish:
                rejections['insufficient_books'] += 1
                continue

            market_prob = mean(implied_probability(item.price) for item in bucket)
            if not self._is_valid_number(market_prob):
                rejections['invalid_market_probability_totals'] += 1
                continue

            over_prob = poisson_over_probability(expected_total, point)
            if over_prob is None:
                rejections['missing_model_probability_totals'] += 1
                continue
            if not self._is_valid_number(over_prob):
                rejections['invalid_model_probability_totals'] += 1
                continue

            over_prob = clamp(float(over_prob), 0.0, 1.0)
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
                    'model=xg_total',
                    f'consensus_fair_odds={1 / max(market_prob, 0.01):.2f}',
                ],
                expected_home=context.expected_home,
                expected_away=context.expected_away,
                model_mode='xg_total',
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

        denom = max(context.expected_home + context.expected_away, 0.1)
        home_base = clamp(context.expected_home / denom, 0.05, 0.85)
        away_base = clamp(context.expected_away / denom, 0.05, 0.85)
        draw_base = clamp(1.0 - abs(home_base - away_base) - 0.35, 0.08, 0.30)
        scale = home_base + away_base + draw_base
        probs = {
            match.home_team: home_base / scale,
            match.away_team: away_base / scale,
            'draw': draw_base / scale,
            'Draw': draw_base / scale,
        }

        result: list[CandidateBet] = []
        for selection, bucket in buckets.items():
            if len({self._norm_book(item.bookmaker) for item in bucket}) < self.settings.min_books_publish:
                rejections['insufficient_books'] += 1
                continue

            model_prob = probs.get(selection)
            if model_prob is None:
                rejections['missing_model_probability_h2h'] += 1
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
                reasons=['mode=soccer_context', 'model=1x2_from_xg'],
                expected_home=context.expected_home,
                expected_away=context.expected_away,
                model_mode='soccer_context',
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
            if len({self._norm_book(item.bookmaker) for item in books}) < self.settings.min_books_publish:
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
                reasons=['mode=xg_spread', 'model=xg_spread'],
                expected_home=context.expected_home,
                expected_away=context.expected_away,
                model_mode='xg_spread',
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
    ) -> CandidateBet | None:
        if not self._is_valid_number(market_prob) or not self._is_valid_number(model_prob):
            return None

        market_prob = clamp(float(market_prob), 0.0, 1.0)
        model_prob = clamp(float(model_prob), 0.0, 1.0)

        books = {offer.bookmaker for offer in offers}
        sources = {offer.source for offer in offers}
        if len(books) < self.settings.min_books_publish:
            return None
        if len(sources) < self.settings.min_sources_publish:
            return None

        best_price = max(offer.price for offer in offers)
        if not (self.settings.odds_min <= best_price <= self.settings.odds_max):
            return None

        confidence = clamp(48 + len(books) * 4 + len(sources) * 5, 0, 100)
        adjusted = shrink_probability(model_prob, market_prob, confidence)
        fair_odds = 1.0 / max(adjusted, 0.01)
        ev_pct = (adjusted * best_price - 1.0) * 100.0
        edge_pct = (adjusted - market_prob) * 100.0

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
            },
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
            filtered.append(item)

        filtered.sort(
            key=lambda item: (
                round(item.ev_pct, 3),
                round(item.edge_pct, 3),
                round(item.confidence, 3),
            ),
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

    @staticmethod
    def _is_valid_number(value: Any) -> bool:
        if value is None:
            return False
        try:
            number = float(value)
        except (TypeError, ValueError):
            return False
        return math.isfinite(number)


ValueModel = CandidateFactory
