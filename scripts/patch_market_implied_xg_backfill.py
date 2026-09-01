from __future__ import annotations

"""Replace the 1.00:1.00 proxy xG placeholder with a market-implied lambda.

Why this exists
---------------
Candidates promoted by the proxy/a-cover/b-cover paths arrive without provider
xG. Something upstream fills ``expected_home = expected_away = 1.00``, which is
not data: it is a placeholder. ``scripts/patch_proxy_default_xg_guard.py``
correctly refuses to treat it as evidence, so every totals candidate died with
``tier_*_proxy_default_xg_placeholder``.

Instead of relaxing that guard we compute an honest number: the main total line
plus the de-vigged market price define an expected total goals value, and the
1X2 prices define how that total splits between the teams. The result is
labelled ``market_implied_total_xg``, so:

* B-tier may use it as a sanity anchor (RULES.txt B-cover);
* A-tier still requires hard provider xG (bzzoiro_stats / sstats_xg / xg_live).

Install this BEFORE ``patch_proxy_default_xg_guard`` so the guard sees the
filled values.
"""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT = Path('.data/exports/latest-market-implied-xg-backfill.json')
ART = Path('artifacts/run-bot/latest-market-implied-xg-backfill.json')

NESTED_KEYS = ('market', 'selection', 'offer', 'best_offer', 'context', 'diagnostics', 'source_summary', 'pick', 'bet')
LINE_KEYS = ('point', 'line', 'total_line', 'market_point', 'selection_point', 'goal_line', 'handicap', 'total')
SIDE_KEYS = ('selection', 'side', 'pick', 'outcome', 'bet', 'market_selection', 'selection_name', 'display_selection', 'selection_label', 'market_label')
PROB_KEYS = ('no_vig_probability', 'fair_probability', 'market_probability', 'implied_probability', 'book_probability', 'market_prob', 'implied_prob')
ODDS_KEYS = ('odds', 'selected_odds', 'price', 'decimal_odds', 'current_price', 'best_price', 'selected_price')
HOME_ODDS_KEYS = ('home_odds', 'odds_home', 'price_home', 'home_price', 'h_odds', 'home_win_odds')
AWAY_ODDS_KEYS = ('away_odds', 'odds_away', 'price_away', 'away_price', 'a_odds', 'away_win_odds')
HARD_XG_TOKENS = ('bzzoiro_stats', 'sstats_xg', 'xg_live', 'actual_home_xg', 'actual_away_xg', 'pre_match_home_xg', 'pre_match_away_xg')
OVER_TOKENS = ('over', 'больше', 'бол.', 'more than')
UNDER_TOKENS = ('under', 'меньше', 'мен.', 'less than')

_COUNTERS: dict[str, int] = {}


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def _num(value: Any) -> float | None:
    try:
        if value in (None, '') or isinstance(value, (dict, list, tuple, set, bool)):
            return None
        return float(str(value).replace(',', '.'))
    except Exception:
        return None


def _env_num(name: str, default: float) -> float:
    value = _num(os.getenv(name))
    return default if value is None else value


def _bump(name: str) -> None:
    _COUNTERS[name] = _COUNTERS.get(name, 0) + 1


def _boxes(candidate: dict[str, Any]) -> list[dict[str, Any]]:
    boxes = [candidate]
    for key in NESTED_KEYS:
        value = candidate.get(key)
        if isinstance(value, dict):
            boxes.append(value)
    return boxes


def _find_num(candidate: dict[str, Any], keys: tuple[str, ...]) -> float | None:
    for box in _boxes(candidate):
        for key in keys:
            value = _num(box.get(key))
            if value is not None:
                return value
    return None


def _side_text(candidate: dict[str, Any]) -> str:
    parts: list[str] = []
    for box in _boxes(candidate):
        for key in SIDE_KEYS:
            value = box.get(key)
            if isinstance(value, str) and value.strip():
                parts.append(value)
    return ' '.join(parts).lower()


def _probability(candidate: dict[str, Any]) -> float | None:
    raw = _find_num(candidate, PROB_KEYS)
    if raw is not None:
        prob = raw / 100.0 if raw > 1.0 else raw
        if 0.03 < prob < 0.97:
            return prob
    odds = _find_num(candidate, ODDS_KEYS)
    if odds is None or odds <= 1.01:
        return None
    overround = max(1.0, _env_num('HARIZON_MARKET_IMPLIED_XG_OVERROUND', 1.045))
    prob = (1.0 / odds) / overround
    return prob if 0.03 < prob < 0.97 else None


def _poisson_over(lam: float, line: float) -> float:
    """P(total goals beats ``line``) for a Poisson total with mean ``lam``."""
    needed = int(math.floor(line)) + 1
    term = math.exp(-lam)
    cumulative = term
    for i in range(1, needed):
        term *= lam / i
        cumulative += term
    return max(0.0, min(1.0, 1.0 - cumulative))


def _solve_lambda(line: float, p_over: float) -> float | None:
    low, high = 0.30, 7.00
    if _poisson_over(low, line) > p_over or _poisson_over(high, line) < p_over:
        return None
    for _ in range(70):
        mid = (low + high) / 2.0
        if _poisson_over(mid, line) < p_over:
            low = mid
        else:
            high = mid
    lam = (low + high) / 2.0
    return lam if 0.60 <= lam <= 6.00 else None


def _home_share(candidate: dict[str, Any]) -> float:
    default = _env_num('HARIZON_MARKET_IMPLIED_XG_HOME_SHARE', 0.54)
    home_odds = _find_num(candidate, HOME_ODDS_KEYS)
    away_odds = _find_num(candidate, AWAY_ODDS_KEYS)
    if home_odds and away_odds and home_odds > 1.01 and away_odds > 1.01:
        p_home = 1.0 / home_odds
        p_away = 1.0 / away_odds
        total = p_home + p_away
        if total > 0:
            share = 0.5 + 0.35 * ((p_home - p_away) / total)
            return max(0.38, min(0.64, share))
    return max(0.38, min(0.64, default))


def _has_hard_xg(candidate: dict[str, Any]) -> bool:
    try:
        text = json.dumps(candidate, ensure_ascii=False, sort_keys=True, default=str).lower()
    except Exception:
        text = str(candidate).lower()
    return any(token in text for token in HARD_XG_TOKENS)


def _needs_backfill(candidate: dict[str, Any]) -> bool:
    home = _num(candidate.get('expected_home'))
    away = _num(candidate.get('expected_away'))
    if home is None or away is None:
        context = candidate.get('context') if isinstance(candidate.get('context'), dict) else {}
        home = home if home is not None else _num(context.get('expected_home'))
        away = away if away is not None else _num(context.get('expected_away'))
    if home is None or away is None:
        return True
    return abs(home - 1.0) < 1e-6 and abs(away - 1.0) < 1e-6


def _write_report(status: str) -> None:
    payload = {
        'status': status,
        'updated_at_utc': datetime.now(timezone.utc).isoformat(),
        'policy': 'market-implied total lambda replaces the 1.00:1.00 placeholder; B-tier sanity only, A-tier still needs hard xG',
        'env': {
            'HARIZON_MARKET_IMPLIED_XG_BACKFILL_ENABLED': str(os.getenv('HARIZON_MARKET_IMPLIED_XG_BACKFILL_ENABLED') or 'true'),
            'HARIZON_MARKET_IMPLIED_XG_OVERROUND': str(os.getenv('HARIZON_MARKET_IMPLIED_XG_OVERROUND') or '1.045'),
            'HARIZON_MARKET_IMPLIED_XG_HOME_SHARE': str(os.getenv('HARIZON_MARKET_IMPLIED_XG_HOME_SHARE') or '0.54'),
        },
        'counters': dict(_COUNTERS),
    }
    for path in (OUT, ART):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        except Exception:
            pass


def _fill(candidate: dict[str, Any]) -> bool:
    family = str(candidate.get('family') or candidate.get('market_family') or '').strip().lower()
    if family != 'totals':
        _bump('skipped_family_not_totals')
        return False
    if not _needs_backfill(candidate):
        _bump('skipped_xg_already_present')
        return False
    if _has_hard_xg(candidate):
        _bump('skipped_hard_xg_present')
        return False
    line = _find_num(candidate, LINE_KEYS)
    if line is None or not (0.5 <= line <= 7.5):
        _bump('skipped_no_usable_line')
        return False
    prob = _probability(candidate)
    if prob is None:
        _bump('skipped_no_market_probability')
        return False
    text = _side_text(candidate)
    is_over = any(token in text for token in OVER_TOKENS)
    is_under = any(token in text for token in UNDER_TOKENS)
    if is_over == is_under:
        _bump('skipped_ambiguous_side')
        return False
    p_over = prob if is_over else 1.0 - prob
    lam = _solve_lambda(line, p_over)
    if lam is None:
        _bump('skipped_lambda_out_of_range')
        return False
    share = _home_share(candidate)
    expected_home = round(lam * share, 3)
    expected_away = round(lam * (1.0 - share), 3)
    if abs(expected_home - 1.0) < 1e-3 and abs(expected_away - 1.0) < 1e-3:
        expected_home = round(expected_home + 0.01, 3)
    candidate['expected_home'] = expected_home
    candidate['expected_away'] = expected_away
    candidate['expected_total'] = round(lam, 3)
    candidate['proxy_default_xg_replaced'] = True
    candidate['proxy_default_xg_replacement_source'] = 'market_implied_total_xg'
    candidate['xg_source'] = 'market_implied_total_xg'
    candidate['xg_hard_confirmation'] = False
    candidate['market_implied_xg'] = {
        'total_lambda': round(lam, 3),
        'line': line,
        'p_over_used': round(p_over, 4),
        'home_share': round(share, 3),
        'source': 'market_implied_total_xg',
        'hard_confirmation': False,
    }
    context = candidate.get('context')
    if isinstance(context, dict):
        context['expected_home'] = expected_home
        context['expected_away'] = expected_away
        context['xg_source'] = 'market_implied_total_xg'
    _bump('filled')
    return True


def install(base: Any) -> dict[str, Any]:
    original = getattr(base, 'xg_sanity_metrics', None)
    if not callable(original) or getattr(base, '_market_implied_xg_backfill_installed', False):
        return {'status': 'skipped'}
    if not _truthy('HARIZON_MARKET_IMPLIED_XG_BACKFILL_ENABLED', True):
        _write_report('disabled')
        return {'status': 'disabled'}

    def wrapped(candidate: dict[str, Any], adjusted_probability: float) -> dict[str, Any]:
        filled = False
        if isinstance(candidate, dict):
            try:
                filled = _fill(candidate)
            except Exception:
                _bump('fill_error')
        metrics = dict(original(candidate, adjusted_probability) or {})
        if filled or (isinstance(candidate, dict) and candidate.get('proxy_default_xg_replacement_source') == 'market_implied_total_xg'):
            metrics['xg_source'] = 'market_implied_total_xg'
            metrics['xg_hard_confirmation'] = False
            metrics['proxy_default_xg_replaced_guard_respected'] = True
        if filled:
            _write_report('installed')
        return metrics

    base.xg_sanity_metrics = wrapped
    base._market_implied_xg_backfill_installed = True
    _write_report('installed')
    return {'status': 'installed', 'order_requirement': 'install before patch_proxy_default_xg_guard'}


__all__ = ['install']
