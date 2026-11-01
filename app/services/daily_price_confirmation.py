"""Final exact-price confirmation. Never substitute a different book or line."""
from __future__ import annotations

import math
from datetime import timedelta

from app.services.daily_goal_probability import expected_value
from app.services.daily_quality_policy import TIER_LIMITS
from app.services.market_period import is_full_match_market
from app.services.daily_match_registry import parse_time


def confirm(candidate, offers, requested_at, now):
    tier = candidate.source_summary.get('publication_tier', 'B')
    p, price = candidate.adjusted_probability, candidate.odds
    if candidate.family != 'totals' or not all(math.isfinite(v) for v in (p, price)) or not 0 < p < 1 or price <= 1:
        return False, 'invalid_candidate'
    side = candidate.selection_key
    matches = []
    for offer in offers:
        at = parse_time(offer.metadata.get('fetched_at_utc'))
        if (offer.family != 'totals' or not is_full_match_market(offer) or offer.point != candidate.point
                or offer.bookmaker.strip().lower() != candidate.bookmaker.strip().lower()
                or offer.source != candidate.source_summary.get('selected_source', offer.source)
                or str(offer.selection).lower() != side or not at
                or at < requested_at-timedelta(seconds=1) or not timedelta(seconds=-5) <= now-at <= timedelta(seconds=60)):
            continue
        if math.isfinite(offer.price) and offer.price > 1:
            matches.append(offer.price)
    if not matches:
        return False, 'exact_quote_not_confirmed'
    available = min(matches)
    if available < price:
        return False, 'price_worsened'
    # Advertise the conservative original price, never upgrade an old selection
    # without re-running its model/quality contract. It is executable or better.
    push = float(candidate.source_summary.get('model_push_probability', 0))
    ev = expected_value(p, price, push)
    floor = TIER_LIMITS.get(tier, TIER_LIMITS['B'])
    if not math.isfinite(ev) or not 0 <= push < 1 or ev < floor['ev'] or (p-1/price)*100 < floor['edge']:
        return False, 'value_not_confirmed'
    candidate.ev_pct = ev
    candidate.source_summary['final_price_confirmation'] = {
        'observed_at': now.isoformat(), 'available_price': available,
        'advertised_price': price, 'book': candidate.bookmaker, 'ev_pct': ev}
    return True, 'confirmed'
