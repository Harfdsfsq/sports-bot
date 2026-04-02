from __future__ import annotations

from collections import defaultdict
from dataclasses import asdict
from typing import Any

from app.config import Settings
from app.schemas import CandidateBet, Match, MatchContext, Offer
from app.utils import (
    avg,
    clamp,
    get_outcome_key,
    get_total_selection_key,
    home_cover_probability_from_lambdas,
    implied_probability,
    normalize_bookmaker_name,
    normalize_probability_percent,
    over_probability_from_lambda,
    poisson_outcome_model,
    price_distance_pct,
    round2,
    shrink_probability,
    strip_vig_three_way,
    strip_vig_two_way,
    weighted_average,
)


class CandidateFactory:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.target_books = {normalize_bookmaker_name(name): True for name in settings.target_bookmakers if normalize_bookmaker_name(name)}
        self.consensus_books = {normalize_bookmaker_name(name): True for name in settings.consensus_bookmakers if normalize_bookmaker_name(name)}

    def build_candidates(
        self,
        matches: list[Match],
        offers_by_match: dict[str, list[Offer]],
        contexts: dict[str, MatchContext],
    ) -> tuple[list[CandidateBet], dict[str, int], dict[str, Any]]:
        all_candidates: list[CandidateBet] = []
        rejection_counts: dict[str, int] = defaultdict(int)
        debug: dict[str, Any] = {"per_match": []}

        for match in matches:
            offers = offers_by_match.get(match.match_key) or []
            if not offers:
                rejection_counts["no_offers"] += 1
                continue
            context = contexts.get(match.match_key)
            candidates, rejections, match_debug = self._build_for_match(match, offers, context)
            all_candidates.extend(candidates)
            for key, value in rejections.items():
                rejection_counts[key] += value
            if match_debug:
                debug["per_match"].append(match_debug)

        ranked = self._rank_and_limit(all_candidates)
        return ranked, dict(rejection_counts), debug

    def _build_for_match(
        self,
        match: Match,
        offers: list[Offer],
        context: MatchContext | None,
    ) -> tuple[list[CandidateBet], dict[str, int], dict[str, Any]]:
        grouped = self._prepare_groups(match, offers)
        rejections: dict[str, int] = defaultdict(int)
        candidates: list[CandidateBet] = []

        h2h_all = self._consensus_for_group(match, grouped, "h2h", None, exclude_book=None)
        model_base = self._derive_model_base(match, context, h2h_all)

        target_offers = [offer for offer in offers if self._is_target_book(offer.bookmaker)]
        fallback_sharp_offers = [offer for offer in offers if normalize_bookmaker_name(offer.bookmaker) in self.consensus_books]
        rejections["non_target_bookmaker"] += max(0, len(offers) - len(target_offers))
        selection_mode = "target_bookmakers_only"
        if not target_offers and fallback_sharp_offers:
            target_offers = fallback_sharp_offers
            selection_mode = "consensus_bookmaker_fallback"
        if not target_offers:
            debug = {
                "match_key": match.match_key,
                "sport": match.sport_key,
                "league": match.league_name,
                "home": match.home_team,
                "away": match.away_team,
                "offers_total": len(offers),
                "target_offers": 0,
                "groups": {key: sum(len(v) for v in books.values()) for key, books in grouped.items()},
                "context": asdict(context) if context is not None else None,
                "model_base": model_base,
                "candidate_count": 0,
                "mode": selection_mode,
            }
            return [], rejections, debug

        seen_candidate_keys: dict[tuple[str, str, str, str], CandidateBet] = {}
        for offer in target_offers:
            tag = self._selection_tag(match, offer)
            group_key = self._group_key(offer)
            if not tag or not group_key:
                rejections["unsupported_market_family"] += 1
                continue
            candidate, rejection_reason = self._evaluate_offer(match, offer, grouped, context, model_base)
            if candidate is None:
                rejections[rejection_reason or "offer_rejected"] += 1
                continue
            dedupe_key = (
                candidate.family,
                candidate.selection.lower(),
                "" if candidate.point is None else f"{candidate.point:.2f}",
                normalize_bookmaker_name(candidate.source_summary.get("target_bookmaker") or ""),
            )
            current = seen_candidate_keys.get(dedupe_key)
            if current is None or candidate.publication_score > current.publication_score:
                seen_candidate_keys[dedupe_key] = candidate

        candidates.extend(seen_candidate_keys.values())
        debug = {
            "match_key": match.match_key,
            "sport": match.sport_key,
            "league": match.league_name,
            "home": match.home_team,
            "away": match.away_team,
            "offers_total": len(offers),
            "target_offers": len(target_offers),
            "groups": {key: sum(len(v) for v in books.values()) for key, books in grouped.items()},
            "context": asdict(context) if context is not None else None,
            "model_base": model_base,
            "candidate_count": len(candidates),
            "mode": selection_mode,
        }
        return candidates, rejections, debug

    def _evaluate_offer(
        self,
        match: Match,
        offer: Offer,
        grouped: dict[str, dict[str, dict[str, dict[str, Any]]]],
        context: MatchContext | None,
        model_base: dict[str, Any],
    ) -> tuple[CandidateBet | None, str | None]:
        tag = self._selection_tag(match, offer)
        group_key = self._group_key(offer)
        if not tag or not group_key:
            return None, "unsupported_market_family"

        target_book_key = normalize_bookmaker_name(offer.bookmaker)
        consensus = self._consensus_for_group(match, grouped, group_key, tag, exclude_book=target_book_key)
        if consensus is None or consensus.get("books_count", 0) < self.settings.min_books_for_consensus:
            consensus = self._consensus_for_group(match, grouped, group_key, tag, exclude_book=None)
        if consensus is None:
            return None, "no_consensus_group"

        market_prob = consensus["fair_probability"]
        books_count = int(consensus.get("books_count", 0))
        sources_count = int(consensus.get("sources_count", 0))
        reference_price = consensus.get("reference_price")
        model_probability, model_reason = self._model_probability_for_offer(
            match=match,
            offer=offer,
            tag=tag,
            market_probability=market_prob,
            grouped=grouped,
            context=context,
            model_base=model_base,
            exclude_book=target_book_key,
        )
        if model_probability is None:
            return None, "no_consensus_group"

        confidence = self._build_confidence(
            match=match,
            offer=offer,
            books_count=books_count,
            sources_count=sources_count,
            context=context,
            model_base=model_base,
            model_reason=model_reason,
        )
        adjusted_probability = shrink_probability(
            model_probability,
            market_prob,
            confidence,
            self.settings.model_shrink_min,
            self.settings.model_shrink_max,
        )
        implied = implied_probability(offer.price)
        edge_pct = (adjusted_probability - implied) * 100.0
        ev_pct = (adjusted_probability * offer.price - 1.0) * 100.0

        if offer.price < self.settings.odds_min or offer.price > self.settings.odds_max:
            return None, "odds_out_of_range"
        if books_count < self.settings.min_books_publish:
            return None, "insufficient_books"
        if sources_count < self.settings.min_sources_publish:
            return None, "insufficient_sources"
        if confidence < self.settings.min_model_confidence:
            return None, "confidence_below_threshold"
        if edge_pct < self.settings.min_edge_pct:
            return None, "edge_below_threshold"
        if ev_pct < self.settings.min_ev_pct:
            return None, "ev_below_threshold"

        model_mode = "market_only" if model_reason == "consensus" else model_reason
        if model_mode == "market_only" and match.tier == "low" and (sources_count < 2 or books_count < max(self.settings.min_books_publish + 2, 4)):
            return None, "market_only_low_tier"
        if model_mode == "market_only" and context is None and sources_count < 2 and edge_pct < max(self.settings.min_edge_pct + 0.8, 2.2):
            return None, "market_only_edge_too_small"

        outlier_distance = price_distance_pct(offer.price, reference_price) if reference_price else None
        outlier_penalty = 0.0
        if outlier_distance is not None and outlier_distance > self.settings.outlier_price_tolerance_pct:
            outlier_penalty = clamp(
                outlier_distance - self.settings.outlier_price_tolerance_pct,
                0.0,
                self.settings.outlier_max_penalty,
            )
        if outlier_penalty >= self.settings.outlier_max_penalty:
            return None, "outlier_penalty_too_high"
        publication_score = self._publication_score(
            family=offer.family,
            model_mode=model_mode,
            match_tier=match.tier,
            edge_pct=edge_pct,
            ev_pct=ev_pct,
            confidence=confidence,
            books_count=books_count,
            sources_count=sources_count,
            outlier_penalty=outlier_penalty,
        )

        final_probability = adjusted_probability
        reasons = [
            f"mode={model_mode}",
            f"model={model_reason}",
            f"consensus_fair_odds={round2(consensus.get('fair_odds'))}",
            f"market_prob={round2(market_prob * 100.0)}%",
            f"model_prob={round2(model_probability * 100.0)}%",
            f"final_prob={round2(final_probability * 100.0)}%",
        ]
        if context and context.expected_home is not None and context.expected_away is not None:
            reasons.append(f"xg={round2(context.expected_home)}:{round2(context.expected_away)}")

        return CandidateBet(
            match_key=match.match_key,
            sport_key=match.sport_key,
            league_name=match.league_name,
            home_team=match.home_team,
            away_team=match.away_team,
            commence_time=match.commence_time,
            family=offer.family,
            selection=offer.selection,
            odds=offer.price,
            fair_odds=1.0 / adjusted_probability,
            implied_probability=implied,
            market_probability=market_prob,
            consensus_probability=market_prob,
            model_probability=model_probability,
            final_probability=final_probability,
            adjusted_probability=adjusted_probability,
            model_mode=model_mode,
            edge_pct=edge_pct,
            ev_pct=ev_pct,
            confidence=confidence,
            books_count=books_count,
            sources_count=sources_count,
            point=offer.point,
            expected_home=model_base.get("home_lambda"),
            expected_away=model_base.get("away_lambda"),
            reasons=reasons,
            source_summary={
                "target_source": offer.source,
                "target_bookmaker": offer.bookmaker,
                "consensus_books": consensus.get("bookmakers", []),
                "consensus_sources": consensus.get("sources", []),
                "mode": model_mode,
            },
            diagnostics={
                "offer_point": offer.point,
                "offer_team_side": offer.team_side,
                "group_key": group_key,
                "selection_tag": tag,
                "market_probability": market_prob,
                "consensus_probability": market_prob,
                "model_probability": model_probability,
                "final_probability": final_probability,
                "reference_price": reference_price,
                "consensus_fair_odds": consensus.get("fair_odds"),
                "outlier_distance_pct": outlier_distance,
                "outlier_penalty": outlier_penalty,
                "match_mode": offer.metadata.get("match_mode"),
            },
            publication_score=publication_score,
        ), None

    def _derive_model_base(self, match: Match, context: MatchContext | None, h2h_consensus: dict[str, Any] | None) -> dict[str, Any]:
        base: dict[str, Any] = {
            "family": "market_only",
            "home": None,
            "draw": None,
            "away": None,
            "btts_yes": None,
            "over25": None,
            "home_lambda": None,
            "away_lambda": None,
            "confidence_anchor": 51.0,
        }
        if h2h_consensus is not None:
            probs = h2h_consensus.get("selection_probabilities") or {}
            base["home"] = probs.get("home")
            base["draw"] = probs.get("draw")
            base["away"] = probs.get("away")
            base["confidence_anchor"] = 54.0

        if match.sport_key != "soccer" or context is None:
            return base

        home_lambda = context.expected_home
        away_lambda = context.expected_away
        if home_lambda is None and away_lambda is None:
            return base
        if home_lambda is None:
            home_lambda = max(0.55, 2.4 - float(away_lambda))
        if away_lambda is None:
            away_lambda = max(0.55, 2.4 - float(home_lambda))

        if context.home_starting is not None and context.home_starting < 11:
            home_lambda *= clamp(1.0 - (11 - context.home_starting) * 0.025, 0.82, 1.0)
        if context.away_starting is not None and context.away_starting < 11:
            away_lambda *= clamp(1.0 - (11 - context.away_starting) * 0.025, 0.82, 1.0)

        poisson = poisson_outcome_model(home_lambda, away_lambda)
        draw_base = poisson["draw"]
        if context.home_win_probability is not None and context.away_win_probability is not None:
            home_raw = max(float(context.home_win_probability), 0.01)
            away_raw = max(float(context.away_win_probability), 0.01)
            scale = home_raw + away_raw
            if scale > 0:
                g_home = (home_raw / scale) * (1.0 - draw_base)
                g_away = (away_raw / scale) * (1.0 - draw_base)
                poisson["home"] = clamp(poisson["home"] * 0.68 + g_home * 0.32, 0.02, 0.92)
                poisson["away"] = clamp(poisson["away"] * 0.68 + g_away * 0.32, 0.02, 0.92)
                poisson["draw"] = clamp(poisson["draw"] * 0.85 + draw_base * 0.15, 0.05, 0.40)
                total = poisson["home"] + poisson["draw"] + poisson["away"]
                poisson["home"] /= total
                poisson["draw"] /= total
                poisson["away"] /= total

        if base["home"] is not None and base["away"] is not None:
            poisson["home"] = clamp(poisson["home"] * 0.72 + float(base["home"]) * 0.28, 0.02, 0.92)
            poisson["draw"] = clamp(poisson["draw"] * 0.72 + float(base.get("draw") or 0.24) * 0.28, 0.05, 0.40)
            poisson["away"] = clamp(poisson["away"] * 0.72 + float(base["away"]) * 0.28, 0.02, 0.92)
            total = poisson["home"] + poisson["draw"] + poisson["away"]
            poisson["home"] /= total
            poisson["draw"] /= total
            poisson["away"] /= total

        base.update(
            {
                "family": "soccer_context",
                "home": poisson["home"],
                "draw": poisson["draw"],
                "away": poisson["away"],
                "btts_yes": poisson["btts_yes"],
                "over25": poisson["over25"],
                "home_lambda": home_lambda,
                "away_lambda": away_lambda,
                "confidence_anchor": max(context.confidence, 60.0),
            }
        )
        return base

    def _model_probability_for_offer(
        self,
        *,
        match: Match,
        offer: Offer,
        tag: str,
        market_probability: float,
        grouped: dict[str, dict[str, dict[str, dict[str, Any]]]],
        context: MatchContext | None,
        model_base: dict[str, Any],
        exclude_book: str | None,
    ) -> tuple[float | None, str]:
        if offer.family == "h2h":
            value = model_base.get(tag)
            if value is not None:
                return float(value), model_base.get("family", "market")
            return market_probability, "consensus"

        if offer.family == "dnb":
            home = model_base.get("home")
            away = model_base.get("away")
            if home is not None and away is not None and home + away > 0:
                if tag == "home":
                    return home / (home + away), "derived_dnb"
                if tag == "away":
                    return away / (home + away), "derived_dnb"
            h2h = self._consensus_for_group(match, grouped, "h2h", None, exclude_book=exclude_book)
            if h2h is not None:
                probs = h2h.get("selection_probabilities") or {}
                home_prob = probs.get("home")
                away_prob = probs.get("away")
                if home_prob is not None and away_prob is not None and home_prob + away_prob > 0:
                    if tag == "home":
                        return home_prob / (home_prob + away_prob), "h2h_derived"
                    if tag == "away":
                        return away_prob / (home_prob + away_prob), "h2h_derived"
            return market_probability, "consensus"

        if offer.family == "doubleChance":
            home = model_base.get("home")
            draw = model_base.get("draw")
            away = model_base.get("away")
            if home is not None and draw is not None and away is not None:
                if tag == "1X":
                    return home + draw, "derived_dc"
                if tag == "X2":
                    return away + draw, "derived_dc"
                if tag == "12":
                    return home + away, "derived_dc"
            h2h = self._consensus_for_group(match, grouped, "h2h", None, exclude_book=exclude_book)
            if h2h is not None:
                probs = h2h.get("selection_probabilities") or {}
                home_prob = probs.get("home")
                draw_prob = probs.get("draw")
                away_prob = probs.get("away")
                if home_prob is not None and draw_prob is not None and away_prob is not None:
                    if tag == "1X":
                        return home_prob + draw_prob, "h2h_derived"
                    if tag == "X2":
                        return away_prob + draw_prob, "h2h_derived"
                    if tag == "12":
                        return home_prob + away_prob, "h2h_derived"
            return market_probability, "consensus"

        if offer.family == "btts":
            if model_base.get("btts_yes") is not None:
                prob_yes = float(model_base["btts_yes"])
                return (prob_yes if tag == "yes" else 1.0 - prob_yes), "xg_poisson"
            return market_probability, "consensus"

        if offer.family == "totals":
            home_lambda = model_base.get("home_lambda")
            away_lambda = model_base.get("away_lambda")
            if home_lambda is not None and away_lambda is not None and offer.point is not None:
                prob_over = over_probability_from_lambda(float(home_lambda) + float(away_lambda), float(offer.point))
                if prob_over is not None:
                    return (prob_over if tag == "over" else 1.0 - prob_over), "xg_total"
            return market_probability, "consensus"

        if offer.family == "teamTotals":
            line = offer.point
            home_lambda = model_base.get("home_lambda")
            away_lambda = model_base.get("away_lambda")
            if line is not None:
                lambda_value = None
                if offer.team_side == "home":
                    lambda_value = home_lambda
                elif offer.team_side == "away":
                    lambda_value = away_lambda
                if lambda_value is not None:
                    prob_over = over_probability_from_lambda(float(lambda_value), float(line))
                    if prob_over is not None:
                        return (prob_over if tag == "over" else 1.0 - prob_over), "xg_team_total"
            return market_probability, "consensus"

        if offer.family == "spreads":
            line = offer.point
            home_lambda = model_base.get("home_lambda")
            away_lambda = model_base.get("away_lambda")
            if line is not None and home_lambda is not None and away_lambda is not None:
                if tag == "home":
                    prob = home_cover_probability_from_lambdas(float(home_lambda), float(away_lambda), float(line))
                    if prob is not None:
                        return prob, "xg_spread"
                if tag == "away":
                    prob = home_cover_probability_from_lambdas(float(home_lambda), float(away_lambda), -float(line))
                    if prob is not None:
                        return 1.0 - prob, "xg_spread"
            return market_probability, "consensus"

        return market_probability, "consensus"

    def _build_confidence(
        self,
        *,
        match: Match,
        offer: Offer,
        books_count: int,
        sources_count: int,
        context: MatchContext | None,
        model_base: dict[str, Any],
        model_reason: str,
    ) -> float:
        confidence = float(model_base.get("confidence_anchor") or 51.0)
        confidence += min(8.0, books_count * 1.7)
        confidence += min(4.0, max(0, sources_count - 1) * 2.0)
        confidence += (self.settings.score_weight_for_family(offer.family) - 1.0) * 10.0
        if context is not None:
            confidence = max(confidence, context.confidence)
            if context.expected_home is not None and context.expected_away is not None:
                confidence += 2.0
        elif model_reason == "consensus":
            confidence -= 4.0
        if match.tier == "low":
            confidence -= 3.0
        match_mode = str(offer.metadata.get("match_mode") or "")
        if match_mode == "fuzzy":
            confidence -= 5.0
        elif match_mode == "loose":
            confidence -= 2.0
        if offer.source == "odds_api_io" and offer.family in {"dnb", "doubleChance", "btts", "teamTotals"}:
            confidence += 1.5
        return clamp(confidence, 44.0, 88.0)

    def _publication_score(
        self,
        *,
        family: str,
        model_mode: str,
        match_tier: str,
        edge_pct: float,
        ev_pct: float,
        confidence: float,
        books_count: int,
        sources_count: int,
        outlier_penalty: float,
    ) -> float:
        score = edge_pct * 3.1 + ev_pct * 2.0 + (confidence - 50.0) * 1.6
        score += books_count * 2.0 + sources_count * 1.8
        score -= outlier_penalty * 1.4
        if model_mode != "market_only":
            score += 6.0
        else:
            score -= 3.0
        if match_tier == "low":
            score -= 6.0
        score *= self.settings.score_weight_for_family(family)
        return round(score, 3)

    def _rank_and_limit(self, candidates: list[CandidateBet]) -> list[CandidateBet]:
        candidates = sorted(
            candidates,
            key=lambda item: (
                item.publication_score,
                item.ev_pct,
                item.edge_pct,
                item.confidence,
            ),
            reverse=True,
        )
        result: list[CandidateBet] = []
        per_match: dict[str, int] = defaultdict(int)
        per_league: dict[str, int] = defaultdict(int)
        for candidate in candidates:
            if per_match[candidate.match_key] >= 1:
                continue
            if per_league[candidate.league_name] >= 2:
                continue
            result.append(candidate)
            per_match[candidate.match_key] += 1
            per_league[candidate.league_name] += 1
            if len(result) >= self.settings.max_picks_per_run:
                break
        return result

    def _prepare_groups(self, match: Match, offers: list[Offer]) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
        grouped: dict[str, dict[str, dict[str, dict[str, Any]]]] = defaultdict(lambda: defaultdict(dict))
        for offer in offers:
            if offer.price <= 1.0:
                continue
            tag = self._selection_tag(match, offer)
            group_key = self._group_key(offer)
            if not tag or not group_key:
                continue
            book_key = normalize_bookmaker_name(offer.bookmaker)
            quality = self.settings.bookmaker_weight(offer.bookmaker) * self.settings.source_weight(offer.source)
            current = grouped[group_key][book_key].get(tag)
            row = {
                "offer": offer,
                "tag": tag,
                "weight": quality,
                "source": offer.source,
                "bookmaker": offer.bookmaker,
            }
            if current is None:
                grouped[group_key][book_key][tag] = row
                continue
            current_quality = float(current["weight"])
            if quality > current_quality + 1e-9:
                grouped[group_key][book_key][tag] = row
                continue
            if abs(quality - current_quality) <= 1e-9 and offer.price > current["offer"].price:
                grouped[group_key][book_key][tag] = row
        return grouped

    def _consensus_for_group(
        self,
        match: Match,
        grouped: dict[str, dict[str, dict[str, dict[str, Any]]]],
        group_key: str,
        selection_tag: str | None,
        *,
        exclude_book: str | None,
    ) -> dict[str, Any] | None:
        book_map = grouped.get(group_key) or {}
        family = group_key.split("|", 1)[0]
        if family == "h2h":
            return self._aggregate_h2h(book_map, selection_tag, exclude_book)
        if family in {"totals", "teamTotals", "spreads", "dnb", "btts"}:
            if family == "spreads":
                return self._aggregate_two_way(book_map, selection_tag, exclude_book, tags=("home", "away"))
            if family == "dnb":
                return self._aggregate_two_way(book_map, selection_tag, exclude_book, tags=("home", "away"))
            if family == "btts":
                return self._aggregate_two_way(book_map, selection_tag, exclude_book, tags=("yes", "no"))
            return self._aggregate_two_way(book_map, selection_tag, exclude_book, tags=("over", "under"))
        if family == "doubleChance":
            h2h = self._consensus_for_group(match, grouped, "h2h", None, exclude_book=exclude_book)
            if h2h is not None:
                probs = h2h.get("selection_probabilities") or {}
                if probs.get("home") is not None and probs.get("draw") is not None and probs.get("away") is not None:
                    derived = {
                        "1X": clamp(float(probs["home"]) + float(probs["draw"]), 0.05, 0.99),
                        "X2": clamp(float(probs["away"]) + float(probs["draw"]), 0.05, 0.99),
                        "12": clamp(float(probs["home"]) + float(probs["away"]), 0.05, 0.99),
                    }
                    return {
                        "fair_probability": derived.get(selection_tag),
                        "fair_odds": None if derived.get(selection_tag) in {None, 0} else 1.0 / float(derived[selection_tag]),
                        "books_count": int(h2h.get("books_count", 0)),
                        "sources_count": int(h2h.get("sources_count", 0)),
                        "bookmakers": h2h.get("bookmakers", []),
                        "sources": h2h.get("sources", []),
                        "selection_probabilities": derived,
                        "reference_price": None,
                    }
            return self._aggregate_raw_implied(book_map, selection_tag, exclude_book)
        return None

    def _aggregate_h2h(
        self,
        book_map: dict[str, dict[str, dict[str, Any]]],
        selection_tag: str | None,
        exclude_book: str | None,
    ) -> dict[str, Any] | None:
        values: dict[str, list[tuple[float, float]]] = {"home": [], "draw": [], "away": []}
        raw_prices: dict[str, list[float]] = {"home": [], "draw": [], "away": []}
        used_books: list[str] = []
        used_sources: set[str] = set()
        for book_key, selections in book_map.items():
            if exclude_book and book_key == exclude_book:
                continue
            home = selections.get("home")
            away = selections.get("away")
            draw = selections.get("draw")
            if home is None or away is None:
                continue
            if draw is not None:
                stripped = strip_vig_three_way(home["offer"].price, draw["offer"].price, away["offer"].price)
                if stripped is None:
                    continue
                probs = {"home": stripped[0], "draw": stripped[1], "away": stripped[2]}
                weight = avg([home["weight"], draw["weight"], away["weight"]]) or 1.0
                raw_prices["draw"].append(draw["offer"].price)
                used_sources.add(draw["source"])
            else:
                stripped = strip_vig_two_way(home["offer"].price, away["offer"].price)
                if stripped is None:
                    continue
                no_draw_total = 1.0 - 0.0
                probs = {"home": stripped[0] / no_draw_total, "draw": 0.0, "away": stripped[1] / no_draw_total}
                weight = avg([home["weight"], away["weight"]]) or 1.0
            values["home"].append((probs["home"], weight))
            values["draw"].append((probs["draw"], weight))
            values["away"].append((probs["away"], weight))
            raw_prices["home"].append(home["offer"].price)
            raw_prices["away"].append(away["offer"].price)
            used_books.append(home["bookmaker"])
            used_sources.add(home["source"])
            used_sources.add(away["source"])
        if not values["home"] or not values["away"]:
            return None
        probs = {
            "home": weighted_average(values["home"]) or 0.0,
            "draw": weighted_average(values["draw"]) or 0.0,
            "away": weighted_average(values["away"]) or 0.0,
        }
        total = probs["home"] + probs["draw"] + probs["away"]
        if total <= 0:
            return None
        probs = {key: clamp(value / total, 0.0, 0.99) for key, value in probs.items()}
        fair_prob = probs.get(selection_tag) if selection_tag else None
        reference_price = avg(raw_prices.get(selection_tag or "") or []) if selection_tag else None
        return {
            "fair_probability": fair_prob,
            "fair_odds": None if fair_prob in {None, 0} else 1.0 / float(fair_prob),
            "books_count": len(used_books),
            "sources_count": len(used_sources),
            "bookmakers": used_books,
            "sources": sorted(used_sources),
            "selection_probabilities": probs,
            "reference_price": reference_price,
        }

    def _aggregate_two_way(
        self,
        book_map: dict[str, dict[str, dict[str, Any]]],
        selection_tag: str | None,
        exclude_book: str | None,
        *,
        tags: tuple[str, str],
    ) -> dict[str, Any] | None:
        first_tag, second_tag = tags
        first_values: list[tuple[float, float]] = []
        second_values: list[tuple[float, float]] = []
        used_books: list[str] = []
        used_sources: set[str] = set()
        raw_prices: dict[str, list[float]] = {first_tag: [], second_tag: []}
        for book_key, selections in book_map.items():
            if exclude_book and book_key == exclude_book:
                continue
            first = selections.get(first_tag)
            second = selections.get(second_tag)
            if first is None or second is None:
                continue
            stripped = strip_vig_two_way(first["offer"].price, second["offer"].price)
            if stripped is None:
                continue
            weight = avg([first["weight"], second["weight"]]) or 1.0
            first_values.append((stripped[0], weight))
            second_values.append((stripped[1], weight))
            raw_prices[first_tag].append(first["offer"].price)
            raw_prices[second_tag].append(second["offer"].price)
            used_books.append(first["bookmaker"])
            used_sources.add(first["source"])
            used_sources.add(second["source"])
        if not first_values or not second_values:
            return None
        first_prob = weighted_average(first_values)
        second_prob = weighted_average(second_values)
        if first_prob is None or second_prob is None:
            return None
        total = first_prob + second_prob
        if total <= 0:
            return None
        probs = {
            first_tag: clamp(first_prob / total, 0.01, 0.99),
            second_tag: clamp(second_prob / total, 0.01, 0.99),
        }
        fair_prob = probs.get(selection_tag or "")
        reference_price = avg(raw_prices.get(selection_tag or "") or []) if selection_tag else None
        return {
            "fair_probability": fair_prob,
            "fair_odds": None if fair_prob in {None, 0} else 1.0 / float(fair_prob),
            "books_count": len(used_books),
            "sources_count": len(used_sources),
            "bookmakers": used_books,
            "sources": sorted(used_sources),
            "selection_probabilities": probs,
            "reference_price": reference_price,
        }

    def _aggregate_raw_implied(
        self,
        book_map: dict[str, dict[str, dict[str, Any]]],
        selection_tag: str | None,
        exclude_book: str | None,
    ) -> dict[str, Any] | None:
        values: dict[str, list[tuple[float, float]]] = defaultdict(list)
        raw_prices: dict[str, list[float]] = defaultdict(list)
        used_books: list[str] = []
        used_sources: set[str] = set()
        for book_key, selections in book_map.items():
            if exclude_book and book_key == exclude_book:
                continue
            book_used = False
            for tag, record in selections.items():
                prob = implied_probability(record["offer"].price)
                if prob <= 0:
                    continue
                values[tag].append((prob, record["weight"]))
                raw_prices[tag].append(record["offer"].price)
                used_sources.add(record["source"])
                book_used = True
            if book_used:
                used_books.append(next(iter(selections.values()))["bookmaker"])
        if not values:
            return None
        probs = {tag: clamp(weighted_average(tag_values) or 0.0, 0.01, 0.99) for tag, tag_values in values.items()}
        fair_prob = probs.get(selection_tag or "")
        reference_price = avg(raw_prices.get(selection_tag or "") or []) if selection_tag else None
        return {
            "fair_probability": fair_prob,
            "fair_odds": None if fair_prob in {None, 0} else 1.0 / float(fair_prob),
            "books_count": len(used_books),
            "sources_count": len(used_sources),
            "bookmakers": used_books,
            "sources": sorted(used_sources),
            "selection_probabilities": probs,
            "reference_price": reference_price,
        }

    @staticmethod
    def _is_yes_no(value: str) -> str | None:
        raw = str(value or "").strip().lower()
        if raw in {"yes", "y", "both teams to score yes"}:
            return "yes"
        if raw in {"no", "n", "both teams to score no"}:
            return "no"
        return None

    def _selection_tag(self, match: Match, offer: Offer) -> str | None:
        if offer.family in {"h2h", "dnb", "spreads"}:
            return get_outcome_key(offer.selection, match.home_team, match.away_team)
        if offer.family in {"totals", "teamTotals"}:
            return get_total_selection_key(offer.selection)
        if offer.family == "btts":
            return self._is_yes_no(offer.selection)
        if offer.family == "doubleChance":
            raw = str(offer.selection or "").upper().replace(" ", "")
            if raw in {"1X", "X2", "12"}:
                return raw
        return None

    @staticmethod
    def _group_key(offer: Offer) -> str | None:
        if offer.family in {"h2h", "dnb", "btts", "doubleChance"}:
            return offer.family
        if offer.family == "totals":
            if offer.point is None:
                return None
            return f"totals|{float(offer.point):.2f}"
        if offer.family == "teamTotals":
            if offer.point is None or not offer.team_side:
                return None
            return f"teamTotals|{offer.team_side}|{float(offer.point):.2f}"
        if offer.family == "spreads":
            if offer.point is None:
                return None
            return f"spreads|{abs(float(offer.point)):.2f}"
        return None

    def _is_target_book(self, bookmaker: str) -> bool:
        if not self.target_books:
            return True
        return normalize_bookmaker_name(bookmaker) in self.target_books
