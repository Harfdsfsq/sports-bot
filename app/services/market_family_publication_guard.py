from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
AUDIT_PATH = ROOT / '.data' / 'exports' / 'latest-market-family-publication-guard.json'
_ALLOWED_DEFAULT = {'totals', 'spreads'}
_PRICE_CONTEXT_SOURCES = {'newsapi', 'gnews', 'newsdata', 'guardian', 'weatherapi', 'openweathermap', 'open_meteo', 'weather', 'sstats_form', 'ensemble', 'market'}
_INSTALLED = False


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ''):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _allowed() -> set[str]:
    raw = str(os.getenv('PUBLICATION_ALLOWED_MARKET_FAMILIES') or os.getenv('HARIZON_ALLOWED_PUBLICATION_FAMILIES') or 'totals,spreads')
    values = {item.strip().lower() for item in raw.split(',') if item.strip()}
    return values or set(_ALLOWED_DEFAULT)


def _min_odds_sources() -> int:
    return max(1, _to_int(os.getenv('PUBLICATION_MIN_ODDS_SOURCES') or os.getenv('TELEGRAM_MAIN_PICK_MIN_ODDS_SOURCES') or '2', 2))


def _write(event: dict[str, Any]) -> None:
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = json.loads(AUDIT_PATH.read_text(encoding='utf-8')) if AUDIT_PATH.exists() else {}
        if not isinstance(payload, dict):
            payload = {}
        payload.setdefault('active', True)
        payload['allowed_families'] = sorted(_allowed())
        payload['min_odds_sources'] = _min_odds_sources()
        payload.setdefault('events', [])
        if isinstance(payload['events'], list):
            payload['events'].append(event)
            payload['events'] = payload['events'][-300:]
        AUDIT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def _get(candidate: Any, name: str, default: Any = None) -> Any:
    try:
        if isinstance(candidate, dict):
            return candidate.get(name, default)
        return getattr(candidate, name, default)
    except Exception:
        return default


def _family(candidate: Any) -> str:
    try:
        value = _get(candidate, 'family') or _get(candidate, 'market_family') or _get(candidate, 'market')
        return str(value or '').strip().lower()
    except Exception:
        return ''


def _label(candidate: Any) -> str:
    try:
        home = _get(candidate, 'home_team', '?') or '?'
        away = _get(candidate, 'away_team', '?') or '?'
        fam = _get(candidate, 'family', '?') or '?'
        sel = _get(candidate, 'selection', '?') or '?'
        odds = _get(candidate, 'odds', '') or ''
        return f'{home} — {away} | {fam} | {sel} @{odds}'
    except Exception:
        return repr(candidate)[:500]


def _source_summary(candidate: Any) -> dict[str, Any]:
    value = _get(candidate, 'source_summary', {})
    return value if isinstance(value, dict) else {}


def _raw_bucket_offers(candidate: Any) -> list[dict[str, Any]]:
    value = _get(candidate, 'raw_bucket_offers', [])
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _seq_len(value: Any) -> int:
    if isinstance(value, (list, tuple, set)):
        return len([x for x in value if str(x or '').strip()])
    if isinstance(value, dict):
        return len(value)
    if isinstance(value, str):
        return len([x for x in re.split(r'[,;+|/]', value) if x.strip()])
    return 0


def _odds_source_count(candidate: Any) -> tuple[int, str]:
    summary = _source_summary(candidate)
    for key in ('odds_sources_count', 'odds_source_count', 'independent_odds_sources', 'independent_odds_source_count'):
        if key in summary:
            value = _to_int(summary.get(key), -1)
            if value >= 0:
                return value, f'source_summary.{key}'
    for key in ('odds_sources', 'offer_sources', 'price_sources', 'independent_sources'):
        if key in summary:
            count = _seq_len(summary.get(key))
            if count > 0:
                return count, f'source_summary.{key}'
    sources: set[str] = set()
    candidate_family = _family(candidate)
    candidate_point = _get(candidate, 'point', None)
    for offer in _raw_bucket_offers(candidate):
        source = str(offer.get('source') or '').strip().lower()
        if not source or source in _PRICE_CONTEXT_SOURCES:
            continue
        family = str(offer.get('family') or '').strip().lower()
        if candidate_family and family and family != candidate_family:
            continue
        if candidate_point not in (None, '') and offer.get('point') not in (None, ''):
            try:
                if abs(float(str(candidate_point).replace(',', '.')) - float(str(offer.get('point')).replace(',', '.'))) > 0.01:
                    continue
            except Exception:
                pass
        sources.add(source)
    if sources:
        return len(sources), 'raw_bucket_offers.sources'
    count = _to_int(_get(candidate, 'sources_count', 0), 0)
    return count, 'candidate.sources_count'


def _reject_reason(candidate: Any) -> str | None:
    family = _family(candidate)
    if not family:
        return 'missing_market_family'
    if family not in _allowed():
        return f'blocked_market_family:{family};allowed={"/".join(sorted(_allowed()))}'
    if _truthy(os.getenv('PUBLICATION_REQUIRE_MIN_ODDS_SOURCES'), True):
        count, basis = _odds_source_count(candidate)
        required = _min_odds_sources()
        if count < required:
            return f'insufficient_publication_odds_sources:{count}<{required};basis={basis}'
    return None


def _filter_candidates(candidates: Any, rejections: dict[str, int] | None = None) -> tuple[Any, list[dict[str, Any]]]:
    if not isinstance(candidates, list) or not candidates:
        return candidates, []
    kept: list[Any] = []
    blocked: list[dict[str, Any]] = []
    for candidate in candidates:
        reason = _reject_reason(candidate)
        if reason:
            blocked.append({'label': _label(candidate), 'family': _family(candidate), 'reason': reason})
            if isinstance(rejections, dict):
                rejections[reason] = int(rejections.get(reason, 0) or 0) + 1
                rejections['publication_family_guard_blocked'] = int(rejections.get('publication_family_guard_blocked', 0) or 0) + 1
        else:
            kept.append(candidate)
    return kept, blocked


def _install_env() -> None:
    allowed_csv = ','.join(sorted(_allowed()))
    strict_values = {
        'PUBLICATION_ALLOWED_MARKET_FAMILIES': allowed_csv,
        'HARIZON_ALLOWED_PUBLICATION_FAMILIES': allowed_csv,
        'MAIN_PUBLISH_ALLOWED_FAMILIES': allowed_csv,
        'TELEGRAM_ALLOWED_MARKET_FAMILIES': allowed_csv,
        'CONTROLLED_FALLBACK_ALLOWED_FAMILIES': allowed_csv,
        'CONTROLLED_FALLBACK_TIER_A_ALLOWED_FAMILIES': allowed_csv,
        'CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES': allowed_csv,
        'CONTROLLED_RESCUE_ALLOWED_FAMILIES': allowed_csv,
        'POST_INTEGRITY_RESCUE_ALLOWED_FAMILIES': allowed_csv,
        'MARKET_FAMILY_PUBLICATION_GUARD_ENABLED': 'true',
        'PUBLICATION_REQUIRE_MIN_ODDS_SOURCES': 'true',
        'PUBLICATION_MIN_ODDS_SOURCES': str(_min_odds_sources()),
        'TELEGRAM_MAIN_PICK_MIN_ODDS_SOURCES': str(_min_odds_sources()),
        'CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM': 'true',
        'H2H_PUBLICATION_ENABLED': 'false',
        'BTTS_PUBLICATION_ENABLED': 'false',
        'DNB_PUBLICATION_ENABLED': 'false',
        'DOUBLE_CHANCE_PUBLICATION_ENABLED': 'false',
        'TEAM_TOTALS_PUBLICATION_ENABLED': 'false',
        'TOTALS_PUBLICATION_ENABLED': 'true',
        'SPREADS_PUBLICATION_ENABLED': 'true',
    }
    for key, value in strict_values.items():
        os.environ[key] = value


def _patch_candidate_factory() -> None:
    try:
        from app.services.model import CandidateFactory
    except Exception as exc:
        _write({'candidate_factory_patch': 'import_failed', 'error': f'{type(exc).__name__}: {exc}'})
        return
    if getattr(CandidateFactory, '_harizon_market_family_publication_guard', False):
        return
    original = CandidateFactory.build_candidates

    def build_candidates_patched(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):  # type: ignore[no-untyped-def]
        candidates, rejections, debug = original(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=market_signals_by_match)
        rejections = dict(rejections or {})
        kept, blocked = _filter_candidates(candidates, rejections)
        if blocked:
            debug = dict(debug or {})
            debug['market_family_publication_guard'] = {
                'enabled': True,
                'allowed_families': sorted(_allowed()),
                'min_odds_sources': _min_odds_sources(),
                'blocked_count': len(blocked),
                'blocked_sample': blocked[:25],
            }
            _write({'candidate_factory_filter': True, 'before': len(candidates or []), 'after': len(kept or []), 'blocked': blocked[:25]})
        return kept, rejections, debug

    CandidateFactory.build_candidates = build_candidates_patched
    CandidateFactory._harizon_market_family_publication_guard = True
    _write({'candidate_factory_patch': 'installed'})


def _patch_telegram_publisher() -> None:
    try:
        from app.services.telegram import TelegramPublisher
    except Exception as exc:
        _write({'telegram_publisher_patch': 'import_failed', 'error': f'{type(exc).__name__}: {exc}'})
        return
    if getattr(TelegramPublisher, '_harizon_market_family_publication_guard', False):
        return
    original_publish = TelegramPublisher.publish

    async def publish_patched(self, bets, bankroll_summary=None):  # type: ignore[no-untyped-def]
        kept, blocked = _filter_candidates(bets)
        if blocked:
            _write({'telegram_publisher_filter': True, 'before': len(bets or []), 'after': len(kept or []), 'blocked': blocked[:25]})
        if isinstance(bets, list) and bets and not kept:
            return 0, []
        return await original_publish(self, kept, bankroll_summary=bankroll_summary)

    TelegramPublisher.publish = publish_patched
    TelegramPublisher._harizon_market_family_publication_guard = True
    _write({'telegram_publisher_patch': 'installed'})


def _is_pick_text(text: str) -> bool:
    low = str(text or '').lower()
    return any(token in low for token in (
        'лучшая ставка', 'лучшие ставки', 'контролируемый прогноз', 'контролируемый резерв',
        'controlled fallback', '🛡 профиль сигнала', '🎯 ставка:',
    ))


def _text_block_reasons(text: str) -> list[str]:
    if not _is_pick_text(text):
        return []
    low = str(text or '').lower()
    reasons: list[str] = []
    has_allowed_market = bool(re.search(r'🎯\s*ставка:\s*(?:тотал|фора|handicap|spread)', low, re.I))
    if not has_allowed_market:
        reasons.append('telegram_market_not_totals_or_spreads')
    # Last-mile text guard for reports/messages that include source counts.
    match = re.search(r'odds\s+sources\s+(\d+)', low)
    if match and int(match.group(1)) < _min_odds_sources():
        reasons.append(f'telegram_insufficient_odds_sources:{match.group(1)}<{_min_odds_sources()}')
    blocked_patterns = [
        (r'🎯\s*ставка:\s*исход', 'telegram_h2h_outcome_blocked'),
        (r'\bп\s*[12]\b|\bп1\b|\bп2\b|\b1x2\b|\bh2h\b', 'telegram_h2h_selection_blocked'),
        (r'ничья|\bdraw\b', 'telegram_draw_blocked'),
        (r'обе\s+забьют|\bbtts\b', 'telegram_btts_blocked'),
        (r'двойной\s+шанс|double\s*chance', 'telegram_double_chance_blocked'),
        (r'\bdnb\b|draw\s*no\s*bet|фора\s*\(?0\)?', 'telegram_dnb_blocked'),
        (r'индивидуальный\s+тотал|team\s*total', 'telegram_team_total_blocked'),
    ]
    for pattern, reason in blocked_patterns:
        if re.search(pattern, low, re.I):
            reasons.append(reason)
    return sorted(set(reasons))


def _fake_response(url: Any, reasons: list[str] | None = None):
    try:
        import httpx
        payload = {'ok': True, 'result': {'message_id': 0, 'date': 0, 'text': 'blocked_by_market_family_publication_guard', 'blocked_by_market_family_publication_guard': True, 'reasons': reasons or []}}
        return httpx.Response(200, json=payload, request=httpx.Request('POST', str(url or 'https://api.telegram.org/bot/sendMessage')))
    except Exception:
        return None


def _patch_httpx() -> None:
    try:
        import httpx
    except Exception:
        return
    if getattr(httpx.AsyncClient, '_harizon_market_family_publication_guard', False):
        return
    original_post = httpx.AsyncClient.post

    async def post_patched(self, url, *args, **kwargs):  # type: ignore[no-untyped-def]
        if 'api.telegram.org' in str(url or '') and 'sendMessage' in str(url or ''):
            text = ''
            payload = kwargs.get('json')
            if isinstance(payload, dict) and isinstance(payload.get('text'), str):
                text = payload.get('text') or ''
            elif isinstance(kwargs.get('data'), dict):
                text = str((kwargs.get('data') or {}).get('text') or '')
            reasons = _text_block_reasons(text)
            if reasons:
                _write({'httpx_text_block': True, 'reasons': reasons, 'text_preview': text[:1400]})
                fake = _fake_response(url, reasons)
                if fake is not None:
                    return fake
        return await original_post(self, url, *args, **kwargs)

    httpx.AsyncClient.post = post_patched
    httpx.AsyncClient._harizon_market_family_publication_guard = True
    _write({'httpx_patch': 'installed'})


def _patch_requests() -> None:
    try:
        import requests
    except Exception:
        return
    if getattr(requests, '_harizon_market_family_publication_guard', False):
        return
    original_post = requests.post

    def post_patched(url, *args, **kwargs):  # type: ignore[no-untyped-def]
        if 'api.telegram.org' in str(url or '') and 'sendMessage' in str(url or ''):
            text = ''
            payload = kwargs.get('json')
            if isinstance(payload, dict) and isinstance(payload.get('text'), str):
                text = payload.get('text') or ''
            elif isinstance(kwargs.get('data'), dict):
                text = str((kwargs.get('data') or {}).get('text') or '')
            reasons = _text_block_reasons(text)
            if reasons:
                _write({'requests_text_block': True, 'reasons': reasons, 'text_preview': text[:1400]})
                class _Response:
                    status_code = 200
                    text = '{"ok":true,"result":{"blocked_by_market_family_publication_guard":true}}'
                    def json(self):
                        return {'ok': True, 'result': {'blocked_by_market_family_publication_guard': True, 'reasons': reasons}}
                    def raise_for_status(self):
                        return None
                return _Response()
        return original_post(url, *args, **kwargs)

    requests.post = post_patched
    requests._harizon_market_family_publication_guard = True
    _write({'requests_patch': 'installed'})


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    if not _truthy(os.getenv('MARKET_FAMILY_PUBLICATION_GUARD_ENABLED'), True):
        return {'status': 'disabled'}
    _install_env()
    _patch_candidate_factory()
    _patch_telegram_publisher()
    _patch_httpx()
    _patch_requests()
    _INSTALLED = True
    _write({'install': 'done', 'allowed_families': sorted(_allowed()), 'min_odds_sources': _min_odds_sources()})
    return {'status': 'installed', 'allowed_families': sorted(_allowed()), 'min_odds_sources': _min_odds_sources()}
