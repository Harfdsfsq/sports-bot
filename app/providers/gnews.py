from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
UTC = timezone.utc
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.providers.news_common import articles_to_context, build_match_news_query
from app.schemas import Match, MatchContext


class GNewsContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = str(settings.gnews_base_url or 'https://gnews.io/api/v4').rstrip('/')
        self.timeout = float(settings.gnews_timeout_seconds or 15.0)

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            'enabled': bool(getattr(self.settings, 'enable_gnews_context', True)),
            'api_key_present': bool(getattr(self.settings, 'gnews_key', None)),
            'requests': 0,
            'response_errors': 0,
            'contexts_built': 0,
            'articles_seen': 0,
            'http_statuses': [],
            'last_body_preview': None,
            'rate_limited': False,
        }
        preview: dict[str, Any] = {'sample_articles': [], 'sample_contexts': []}
        if not stats['enabled'] or not stats['api_key_present']:
            return {}, stats, preview
        cooldown_until = self._cooldown_until()
        if cooldown_until is not None:
            stats['rate_limited'] = True
            stats['cooldown_until'] = cooldown_until.isoformat()
            stats['last_body_preview'] = f'cooldown active until {cooldown_until.isoformat()}'
            return {}, stats, preview
        soccer_matches = [item for item in matches if item.sport_key == 'soccer']
        if not soccer_matches:
            return {}, stats, preview
        limit = max(0, int(getattr(self.settings, 'gnews_match_limit', 12) or 12))
        limit = min(limit, max(0, int(getattr(self.settings, 'gnews_per_run_max', 1) or 0)))
        if limit <= 0:
            stats['budget_exhausted'] = True
            return {}, stats, preview
        selected = soccer_matches[:limit]
        max_articles = max(1, min(int(getattr(self.settings, 'gnews_articles_per_match', 6) or 6), 10))
        lookback = max(24, int(getattr(self.settings, 'gnews_lookback_hours', 72) or 72))
        from_dt = (datetime.now(UTC) - timedelta(hours=lookback)).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        contexts: dict[str, MatchContext] = {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for idx, match in enumerate(selected):
                params = {
                    'apikey': str(self.settings.gnews_key),
                    'q': build_match_news_query(match),
                    'lang': 'en',
                    'max': max_articles,
                    'from': from_dt,
                    'sortby': 'publishedAt',
                    'in': 'title,description',
                }
                stats['requests'] += 1
                try:
                    response = await client.get(f'{self.base_url}/search', params=params)
                except Exception as exc:
                    stats['response_errors'] += 1
                    stats['last_body_preview'] = f'request failed: {exc}'
                    continue
                stats['http_statuses'].append(response.status_code)
                stats['last_body_preview'] = response.text[:1200]
                if response.status_code == 429:
                    stats['response_errors'] += 1
                    stats['rate_limited'] = True
                    self._activate_cooldown(minutes=max(5, int(getattr(self.settings, 'gnews_rate_limit_cooldown_minutes', 180) or 180)))
                    break
                if response.status_code != 200:
                    stats['response_errors'] += 1
                    continue
                try:
                    payload = response.json()
                except Exception:
                    stats['response_errors'] += 1
                    continue
                articles = [item for item in (payload.get('articles') or []) if isinstance(item, dict)] if isinstance(payload, dict) else []
                stats['articles_seen'] += len(articles)
                if articles and len(preview['sample_articles']) < 3:
                    preview['sample_articles'].append(articles[0])
                context = articles_to_context(match, articles, 'gnews')
                if context is not None:
                    contexts[match.match_key] = context
                    stats['contexts_built'] += 1
                    if len(preview['sample_contexts']) < 6:
                        preview['sample_contexts'].append({
                            'match_key': match.match_key,
                            'articles': len((context.payload or {}).get('articles') or []),
                            'home_win_probability': context.home_win_probability,
                            'away_win_probability': context.away_win_probability,
                        })
                if idx < len(selected) - 1:
                    await asyncio.sleep(1.05)
        return contexts, stats, preview

    def _cooldown_path(self) -> Path:
        return Path(getattr(self.settings, 'state_path', '.data/state.json')).resolve().parent / 'provider_cache' / 'gnews_rate_limit.json'

    def _cooldown_until(self) -> datetime | None:
        path = self._cooldown_path()
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None
        raw_until = payload.get('cooldown_until')
        if not raw_until:
            return None
        try:
            dt = datetime.fromisoformat(str(raw_until).replace('Z', '+00:00'))
        except Exception:
            return None
        if dt <= datetime.now(UTC):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            return None
        return dt

    def _activate_cooldown(self, *, minutes: int) -> None:
        path = self._cooldown_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        until = datetime.now(UTC) + timedelta(minutes=max(1, minutes))
        payload = {'cooldown_until': until.isoformat(), 'created_at': datetime.now(UTC).isoformat()}
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        except Exception:
            return
