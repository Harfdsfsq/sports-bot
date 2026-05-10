from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.schemas import Match, MatchContext, Offer
from app.utils import score_event_match

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / '.data' / 'exports' / 'latest-bzzoiro-odds-rekey.json'
_INSTALLED = False


def _norm(value: Any) -> str:
    return re.sub(r'[^a-z0-9]+', ' ', str(value or '').lower()).strip()


def _flat(value: Any, prefix: str = '', depth: int = 0) -> dict[str, Any]:
    if depth > 5:
        return {}
    out: dict[str, Any] = {}
    if isinstance(value, dict):
        for k, v in value.items():
            key = f'{prefix}.{k}' if prefix else str(k)
            if isinstance(v, dict):
                out.update(_flat(v, key, depth + 1))
            elif isinstance(v, list):
                out[key] = v
                for i, item in enumerate(v[:6]):
                    if isinstance(item, dict):
                        out.update(_flat(item, f'{key}.{i}', depth + 1))
            else:
                out[key] = v
    return out


def _get(flat: dict[str, Any], *needles: str) -> Any:
    normed = {re.sub(r'[^a-z0-9]+', '_', k.lower()).strip('_'): v for k, v in flat.items()}
    for needle in needles:
        n = re.sub(r'[^a-z0-9]+', '_', needle.lower()).strip('_')
        if n in normed and normed[n] not in (None, ''):
            return normed[n]
    for needle in needles:
        n = re.sub(r'[^a-z0-9]+', '_', needle.lower()).strip('_')
        for k, v in normed.items():
            if n in k and v not in (None, ''):
                return v
    return None


def _text(value: Any) -> str:
    if isinstance(value, dict):
        value = value.get('name') or value.get('title') or value.get('team_name')
    return str(value or '').strip()


def _parse_dt(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    try:
        s = str(value).strip()
        if s.endswith('Z'):
            s = s[:-1] + '+00:00'
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _key_parts(key: str) -> tuple[str, str, datetime | None]:
    parts = str(key or '').split('|')
    if len(parts) < 4:
        return '', '', None
    home, away, date = parts[1], parts[2], parts[3]
    try:
        dt = datetime.fromisoformat(date[:10]).replace(tzinfo=UTC)
    except Exception:
        dt = None
    return home, away, dt


def _context_event(context_key: str, context: MatchContext) -> tuple[str, str, str, datetime | None]:
    data = {'payload': getattr(context, 'payload', {}) or {}, 'details': getattr(context, 'details', {}) or {}}
    f = _flat(data)
    home = _text(_get(f, 'home_team', 'team_home', 'home.name', 'home_name', 'localteam_name', 'participant1Name'))
    away = _text(_get(f, 'away_team', 'team_away', 'away.name', 'away_name', 'visitorteam_name', 'participant2Name'))
    league = _text(_get(f, 'league_name', 'league.name', 'tournament.name', 'competition.name'))
    start = _parse_dt(_get(f, 'commence_time', 'start_at', 'startTime', 'kickoff', 'date', 'event_date'))
    if not home or not away:
        kh, ka, kd = _key_parts(context_key)
        home = home or kh
        away = away or ka
        start = start or kd
    return home, away, league, start


def _best_match(context_key: str, context: MatchContext, matches: list[Match], by_key: dict[str, Match]) -> Match | None:
    direct = by_key.get(str(context_key))
    if direct is not None:
        return direct
    home, away, league, start = _context_event(context_key, context)
    if not home or not away:
        return None
    start = start or datetime.now(UTC)
    best: tuple[float, Match] | None = None
    for match in matches:
        try:
            score, quality = score_event_match(
                sport='soccer',
                match_home=match.home_team,
                match_away=match.away_team,
                match_start=match.commence_time,
                match_league=match.league_name,
                event_home=home,
                event_away=away,
                event_start=start,
                event_league=league,
                exact_tolerance_hours=24.0,
                fuzzy_tolerance_hours=24.0,
            )
        except Exception:
            continue
        if best is None or score > best[0]:
            best = (score, match)
    threshold = float(os.getenv('BZZOIRO_ODDS_REKEY_MIN_SCORE', '70') or 70)
    if best is not None and best[0] >= threshold:
        return best[1]
    return None


def _offer_copy(offer: Offer, target: Match) -> Offer:
    selection = offer.selection
    team_side = offer.team_side
    if offer.family == 'h2h':
        low = str(selection or '').lower()
        if low in {'home', '1'}:
            selection = target.home_team
            team_side = 'home'
        elif low in {'away', '2'}:
            selection = target.away_team
            team_side = 'away'
    meta = dict(getattr(offer, 'metadata', {}) or {})
    meta['bzzoiro_rekeyed_to_match_key'] = target.match_key
    return Offer(
        source=offer.source,
        bookmaker=offer.bookmaker,
        family=offer.family,
        selection=selection,
        price=offer.price,
        point=offer.point,
        team_side=team_side,
        market_name=offer.market_name,
        market_key=offer.market_key,
        market_subtype=offer.market_subtype,
        source_event_id=offer.source_event_id,
        metadata=meta,
    )


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    try:
        from app.services.model import CandidateFactory
    except Exception as exc:
        return {'status': 'import_error', 'error': f'{type(exc).__name__}: {exc}'}
    current = CandidateFactory.build_candidates
    if getattr(current, '_harizon_bzzoiro_rekey_wrapper', False):
        _INSTALLED = True
        return {'status': 'already_wrapped'}
    original = current

    def wrapped(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):
        matches_list = list(matches or [])
        by_key = {m.match_key: m for m in matches_list}
        merged_offers = {str(k): list(v or []) for k, v in dict(offers_by_match or {}).items()}
        merged_contexts = {str(k): v for k, v in dict(contexts_by_match or {}).items()}
        rekeyed_contexts = 0
        rekeyed_offers = 0
        skipped = 0
        for key, value in list(merged_contexts.items()):
            contexts = value if isinstance(value, list) else [value]
            for ctx in contexts:
                if not isinstance(ctx, MatchContext) or 'bzzoiro' not in str(ctx.source).lower():
                    continue
                target = _best_match(key, ctx, matches_list, by_key)
                if target is None:
                    skipped += 1
                    continue
                target_key = target.match_key
                if target_key != key:
                    existing = merged_contexts.get(target_key)
                    if existing is None:
                        merged_contexts[target_key] = [ctx]
                    elif isinstance(existing, list):
                        if ctx not in existing:
                            existing.append(ctx)
                    else:
                        merged_contexts[target_key] = [existing, ctx]
                    rekeyed_contexts += 1
                for offer in list(merged_offers.get(key, [])):
                    if str(getattr(offer, 'source', '')).lower() != 'bzzoiro':
                        continue
                    merged_offers.setdefault(target_key, []).append(_offer_copy(offer, target))
                    rekeyed_offers += 1
        report = {
            'status': 'ok',
            'matches_seen': len(matches_list),
            'context_keys_seen': len(merged_contexts),
            'rekeyed_bzzoiro_contexts': rekeyed_contexts,
            'rekeyed_bzzoiro_offers': rekeyed_offers,
            'skipped_bzzoiro_contexts': skipped,
        }
        _write(report)
        candidates, rejections, debug = original(self, matches, merged_offers, merged_contexts, market_signals_by_match=market_signals_by_match)
        try:
            debug = dict(debug or {})
            debug['bzzoiro_odds_rekey_runtime_patch'] = report
        except Exception:
            pass
        return candidates, rejections, debug

    wrapped._harizon_bzzoiro_rekey_wrapper = True
    CandidateFactory.build_candidates = wrapped
    _INSTALLED = True
    _write({'status': 'installed'})
    return {'status': 'installed'}
