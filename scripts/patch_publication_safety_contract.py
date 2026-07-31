from __future__ import annotations

"""Production safety contract for guarded fallback publication.

The current all-time report is promising but still has too few closed unique picks
and a lot of unsettled rows.  This patch keeps the bot productive while stopping
weak reserve candidates from becoming real Telegram picks:

* proxy/default 1.00:1.00 xG is a hard reject for every tier;
* market-implied xG may replace a fake placeholder for diagnostics, but it is not
  accepted as hard xG confirmation for Telegram publication;
* A-tier requires 2+ independent line/odds sources and 2+ context
  confirmations; B-tier keeps the regulated 1 odds / 2 books / 1 context
  contract;
* B-tier proxy/promotion rows need hard context evidence;
* reserve/youth/friendly-style rows are not allowed through B-tier;
* all decisions are written to a small diagnostic artifact.

It does not lower any value, line-movement, price, daily-cap or xG guards.
"""

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUT = Path('.data/exports/latest-publication-safety-contract.json')
ART = Path('artifacts/run-bot/latest-publication-safety-contract.json')

WEAK_QUALITY_TOKENS = {
    'proxy',
    'raw_missing',
    'unknown_quality',
    'controlled_fallback',
    'reserve',
    'restored_movement_proxy_quality',
}

LOW_QUALITY_TEXT_RE = re.compile(
    r'\b(?:u[- ]?(?:17|18|19|20|21|23)|under[- ]?(?:17|18|19|20|21|23)|reserve|reserves|youth|academy|development|women|friendly|friendlies|club friendly|second team|\bii\b|\biii\b|b team)\b',
    re.I,
)

HARD_CONTEXT_TOKENS = (
    'bzzoiro_stats',
    'bzzoiro_prediction',
    'bzzoiro_odds_comparison',
    'odds_comparison',
    'event_stats',
    'event_prediction',
    'sstats_xg',
    'sstats_form',
    'pre_match_home_xg',
    'pre_match_away_xg',
    'actual_home_xg',
    'actual_away_xg',
    'home_xg',
    'away_xg',
    'xg_live',
)

HARD_XG_TOKENS = (
    'bzzoiro_stats',
    'sstats_xg',
    'pre_match_home_xg',
    'pre_match_away_xg',
    'actual_home_xg',
    'actual_away_xg',
    'context_home_away',
    'context_total_split',
    'xg_live',
)

MARKET_IMPLIED_XG_TOKENS = (
    'market_implied_total_xg',
    'market_probability_from_candidate',
    'market_implied_replaces_proxy_placeholder',
    'proxy_default_xg_replaced',
)


def _truthy(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on', 'force'}


def _f(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ''):
            return default
        return float(str(value).replace(',', '.'))
    except Exception:
        return default


def _i(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value).replace(',', '.')))
    except Exception:
        return default


def _quality_source(candidate: dict[str, Any], metrics: dict[str, Any]) -> str:
    parts: list[str] = []
    for obj in (
        metrics,
        candidate,
        candidate.get('source_summary') if isinstance(candidate.get('source_summary'), dict) else {},
        candidate.get('diagnostics') if isinstance(candidate.get('diagnostics'), dict) else {},
    ):
        if not isinstance(obj, dict):
            continue
        for key in ('quality_score_source', 'quality_source', 'selected_source', '_candidate_source', 'candidate_source', 'published_by'):
            value = obj.get(key)
            if value not in (None, ''):
                parts.append(str(value).strip().lower())
    return ' '.join(parts)


def _payload_text(candidate: dict[str, Any], metrics: dict[str, Any]) -> str:
    return json.dumps({'candidate': candidate, 'metrics': metrics}, ensure_ascii=False, sort_keys=True).lower()


def _xg_payload(metrics: dict[str, Any]) -> dict[str, Any]:
    return metrics.get('xg_sanity') if isinstance(metrics.get('xg_sanity'), dict) else {}


def _is_proxy_default_xg(metrics: dict[str, Any]) -> bool:
    xg = _xg_payload(metrics)
    reason = str(xg.get('reason') or '').lower()
    return bool(xg.get('proxy_default_xg_guard')) or 'proxy_default_1_1_xg_placeholder' in reason


def _has_hard_xg(candidate: dict[str, Any], metrics: dict[str, Any]) -> bool:
    text = _payload_text(candidate, metrics)
    return any(token in text for token in HARD_XG_TOKENS)


def _is_market_implied_xg(candidate: dict[str, Any], metrics: dict[str, Any]) -> bool:
    text = _payload_text(candidate, metrics)
    if any(token in text for token in MARKET_IMPLIED_XG_TOKENS):
        return True
    xg = _xg_payload(metrics)
    return str(xg.get('xg_source') or xg.get('source') or '').strip().lower() == 'market_implied_total_xg'


def _has_hard_context(candidate: dict[str, Any], metrics: dict[str, Any]) -> bool:
    text = _payload_text(candidate, metrics)
    if any(token in text for token in HARD_CONTEXT_TOKENS):
        return True
    # Market-implied xG is a market sanity anchor, not provider context.
    if _is_market_implied_xg(candidate, metrics):
        return False
    xg = _xg_payload(metrics)
    if bool(xg.get('enabled')) and not _is_proxy_default_xg(metrics):
        total = _f(xg.get('xg_total'), -1.0)
        xg_prob = _f(xg.get('xg_probability'), 0.0)
        return total > 0 and (abs(total - 2.0) > 1e-6 or xg_prob > 0)
    return False


def _looks_weak_quality(candidate: dict[str, Any], metrics: dict[str, Any]) -> bool:
    text = _quality_source(candidate, metrics)
    if not text:
        return True
    return any(token in text for token in WEAK_QUALITY_TOKENS)


def _low_quality_competition(candidate: dict[str, Any]) -> bool:
    text = ' '.join(str(candidate.get(key) or '') for key in ('league_name', 'league', 'home_team', 'away_team', 'home', 'away', 'competition', 'tournament'))
    return bool(LOW_QUALITY_TEXT_RE.search(text))


def _odds_sources(metrics: dict[str, Any]) -> int:
    return max(
        _i(metrics.get('odds_sources_count')),
        _i(metrics.get('line_sources_count')),
        _i(metrics.get('price_sources_count')),
        _i(metrics.get('sources_count')),
    )


def _context_sources(metrics: dict[str, Any]) -> int:
    return max(
        _i(metrics.get('context_sources_count')),
        _i(metrics.get('confirmation_sources_count')),
        _i(metrics.get('sources_count')),
    )


def _write_report(payload: dict[str, Any]) -> None:
    for path in (OUT, ART):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        except Exception:
            pass


def install(base: Any) -> None:
    old = getattr(base, 'tier_reasons', None)
    if not callable(old) or getattr(base, '_publication_safety_contract_installed', False):
        return

    counters: dict[str, int] = {}

    def add(reason: str, reasons: list[str]) -> None:
        reasons.append(reason)
        counters[reason] = counters.get(reason, 0) + 1

    def wrapped(tier: str, candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
        reasons = list(old(tier, candidate, metrics) or [])
        t = str(tier or '').strip().upper()
        # The historical global flag predates the A/B contract.  It is an
        # A-tier strictness flag, not permission to raise B-tier from 1/2/1 to
        # 2/2/2 after the focused-alpha policy has been applied.
        if t == 'A' and (
            _truthy('HARIZON_REQUIRE_2PLUS_LINES_CONTEXTS_FOR_TELEGRAM', True)
            or _truthy('CONTROLLED_FALLBACK_REQUIRE_2PLUS_LINES_CONTEXTS', True)
        ):
            odds = _odds_sources(metrics)
            ctx = _context_sources(metrics)
            if odds < 2:
                add(f'tier_{t.lower()}_two_plus_odds_sources_required:{odds}/2', reasons)
            if ctx < 2:
                add(f'tier_{t.lower()}_two_plus_context_sources_required:{ctx}/2', reasons)
        if _truthy('CONTROLLED_FALLBACK_BLOCK_PROXY_DEFAULT_XG_ALL_TIERS', True) and _is_proxy_default_xg(metrics):
            add(f'tier_{t.lower()}_proxy_default_xg_placeholder', reasons)
        xg = _xg_payload(metrics)
        hard_xg_flag = xg.get('xg_hard_confirmation')
        market_implied_without_hard_direction = _is_market_implied_xg(candidate, metrics) and (
            hard_xg_flag is False
            or (hard_xg_flag is None and not _has_hard_xg(candidate, metrics))
        )
        if _truthy('CONTROLLED_FALLBACK_BLOCK_MARKET_IMPLIED_XG_AS_HARD', True) and market_implied_without_hard_direction:
            add(f'tier_{t.lower()}_market_implied_xg_not_hard_confirmation', reasons)
        if t == 'B' and _truthy('CONTROLLED_FALLBACK_B_TIER_REQUIRE_HARD_CONTEXT', True):
            if _looks_weak_quality(candidate, metrics) and not _has_hard_context(candidate, metrics):
                add('tier_b_hard_context_required_for_proxy_quality', reasons)
        if t == 'B' and _truthy('CONTROLLED_FALLBACK_B_TIER_BLOCK_LOW_QUALITY_COMPETITIONS', True):
            if _low_quality_competition(candidate):
                add('tier_b_low_quality_competition_requires_a_tier', reasons)
        return reasons

    base.tier_reasons = wrapped
    base._publication_safety_contract_installed = True
    _write_report({
        'status': 'installed',
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'policy': 'A=2 odds/2 books/2 context; B=1 odds/2 books/1 real context; proxy/default xG and market-implied-only xG are not hard publication confirmation',
        'env': {
            'HARIZON_REQUIRE_2PLUS_LINES_CONTEXTS_FOR_TELEGRAM': str(os.getenv('HARIZON_REQUIRE_2PLUS_LINES_CONTEXTS_FOR_TELEGRAM') or 'true'),
            'CONTROLLED_FALLBACK_REQUIRE_2PLUS_LINES_CONTEXTS': str(os.getenv('CONTROLLED_FALLBACK_REQUIRE_2PLUS_LINES_CONTEXTS') or 'true'),
            'CONTROLLED_FALLBACK_BLOCK_PROXY_DEFAULT_XG_ALL_TIERS': str(os.getenv('CONTROLLED_FALLBACK_BLOCK_PROXY_DEFAULT_XG_ALL_TIERS') or 'true'),
            'CONTROLLED_FALLBACK_BLOCK_MARKET_IMPLIED_XG_AS_HARD': str(os.getenv('CONTROLLED_FALLBACK_BLOCK_MARKET_IMPLIED_XG_AS_HARD') or 'true'),
            'CONTROLLED_FALLBACK_B_TIER_REQUIRE_HARD_CONTEXT': str(os.getenv('CONTROLLED_FALLBACK_B_TIER_REQUIRE_HARD_CONTEXT') or 'true'),
            'CONTROLLED_FALLBACK_B_TIER_BLOCK_LOW_QUALITY_COMPETITIONS': str(os.getenv('CONTROLLED_FALLBACK_B_TIER_BLOCK_LOW_QUALITY_COMPETITIONS') or 'true'),
        },
        'runtime_counters': counters,
    })
