from __future__ import annotations

from collections import defaultdict
from statistics import mean

from app.config import Settings
from app.schemas import CandidateBet, Match, MatchContext, Offer
from app.utils import clamp, implied_probability, poisson_over_probability, shrink_probability

BOOKMAKER_WEIGHTS = {
    'Pinnacle': 1.20,
    'Betfair': 1.15,
    'Bet365': 1.08,
    'Unibet': 1.03,
}
SOURCE_WEIGHTS = {
    'odds_api_io': 1.0,
    'the_odds_api': 0.98,
    'sstats': 0.92,
}


class ValueModel:
    def __init__(self, settings: Settings):
        self.settings = settings

    def build_candidates(
        self,
        matches: list[Match],
        offers_by_match: dict[str, list[Offer]],
        contexts_by_match: dict[str, MatchContext],
    ) -> list[CandidateBet]:
        candidates: list[CandidateBet] = []
        matches_by_key = {m.match_key: m for m in matches}
        for match_key, offers in offers_by_match.items():
            match = matches_by_key.get(match_key)
            if not match:
                continue
            families: dict[str, list[Offer]] = defaultdict(list)
            for offer in offers:
                families[offer.family].append(offer)
            context = contexts_by_match.get(match_key)
            if 'totals' in families and context and context.expected_home and context.expected_away:
                candidates.extend(self._build_totals_candidates(match, families['totals'], context))
            if 'h2h' in families and context:
                candidates.extend(self._build_h2h_candidates(match, families['h2h'], context))
        return self._filter_and_rank(candidates)

    def _build_totals_candidates(self, match: Match, offers: list[Offer], context: MatchContext) -> list[CandidateBet]:
        buckets: dict[tuple[str, float | None], list[Offer]] = defaultdict(list)
        for offer in offers:
            buckets[(offer.selection, offer.point)].append(offer)
        expected_total = (context.expected_home or 0.0) + (context.expected_away or 0.0)
        result: list[CandidateBet] = []
        for (selection, point), bucket in buckets.items():
            if point is None or len(bucket) < self.settings.min_books:
                continue
            if not selection.lower().startswith(('over', 'under')):
                continue
            market_prob = mean(implied_probability(o.price) for o in bucket)
            over_prob = poisson_over_probability(expected_total, point)
            model_prob = over_prob if selection.lower().startswith('over') else (1.0 - over_prob)
            candidate = self._candidate_from_bucket(
                match=match,
                family='totals',
                selection=selection,
                point=point,
                offers=bucket,
                market_prob=market_prob,
                model_prob=model_prob,
                reasons=[
                    f'xG expected total {expected_total:.2f}',
                    f'SStats split {context.expected_home:.2f}:{context.expected_away:.2f}',
                ],
                expected_home=context.expected_home,
                expected_away=context.expected_away,
            )
            if candidate:
                result.append(candidate)
        return result

    def _build_h2h_candidates(self, match: Match, offers: list[Offer], context: MatchContext) -> list[CandidateBet]:
        if context.expected_home is None or context.expected_away is None:
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
            'Draw': draw_base / scale,
        }
        result: list[CandidateBet] = []
        for selection, bucket in buckets.items():
            if len(bucket) < self.settings.min_books:
                continue
            model_prob = probs.get(selection)
            if model_prob is None:
                continue
            market_prob = mean(implied_probability(o.price) for o in bucket)
            candidate = self._candidate_from_bucket(
                match=match,
                family='h2h',
                selection=selection,
                point=None,
                offers=bucket,
                market_prob=market_prob,
                model_prob=model_prob,
                reasons=[
                    f'xG split {context.expected_home:.2f}:{context.expected_away:.2f}',
                    '1X2 derived from expected goal share',
                ],
                expected_home=context.expected_home,
                expected_away=context.expected_away,
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
    ) -> CandidateBet | None:
        weighted = []
        books = set()
        sources = set()
        best_price = 0.0
        for offer in offers:
            books.add(offer.bookmaker)
            sources.add(offer.source)
            best_price = max(best_price, offer.price)
            book_weight = BOOKMAKER_WEIGHTS.get(offer.bookmaker, 1.0)
            source_weight = SOURCE_WEIGHTS.get(offer.source, 1.0)
            weighted.append((offer.price, book_weight * source_weight))
        if len(books) < self.settings.min_books or len(sources) < self.settings.min_sources:
            return None
        if not (self.settings.odds_min <= best_price <= self.settings.odds_max):
            return None
        confidence = clamp(48 + len(books) * 4 + len(sources) * 5, 0, 100)
        adjusted = shrink_probability(model_prob, market_prob, confidence)
        fair_odds = 1.0 / adjusted
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
            model_probability=model_prob,
            adjusted_probability=adjusted,
            edge_pct=edge_pct,
            ev_pct=ev_pct,
            confidence=confidence,
            books_count=len(books),
            sources_count=len(sources),
            expected_home=expected_home,
            expected_away=expected_away,
            reasons=reasons,
            source_summary={
                'books': sorted(books),
                'sources': sorted(sources),
                'offers_seen': len(offers),
            },
        )

    def _filter_and_rank(self, candidates: list[CandidateBet]) -> list[CandidateBet]:
        filtered = [
            item for item in candidates
            if item.ev_pct >= self.settings.min_ev_pct and item.edge_pct >= self.settings.min_edge_pct
        ]
        filtered.sort(
            key=lambda x: (
                round(x.ev_pct, 3),
                round(x.edge_pct, 3),
                round(x.confidence, 3),
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
