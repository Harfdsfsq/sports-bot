from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from app.config import Settings
from app.providers.news_common import articles_to_context, build_match_news_query
from app.schemas import Match, MatchContext


class NewsApiContextProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = str(settings.newsapi_base_url or 'https://newsapi.org/v2').rstrip('/')
        self.timeout = float(settings.newsapi_timeout_seconds or 15.0)

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            'enabled': bool(getattr(self.settings, 'enable_newsapi_context', True)),
            'api_key_present': bool(getattr(self.settings, 'newsapi_key', None)),
            'requests': 0,
            'response_errors': 0,
            'contexts_built': 0,
            'articles_seen': 0,
            'http_statuses': [],
            'last_body_preview': None,
        }
        preview: dict[str, Any] = {'sample_articles': [], 'sample_contexts': []}
        if not stats['enabled'] or not stats['api_key_present']:
            return {}, stats, preview
        soccer_matches = [item for item in matches if item.sport_key == 'soccer']
        if not soccer_matches:
            return {}, stats, preview
        limit = max(1, int(getattr(self.settings, 'newsapi_match_limit', 12) or 12))
        selected = soccer_matches[:limit]
        page_size = max(1, min(int(getattr(self.settings, 'newsapi_articles_per_match', 6) or 6), 20))
        lookback = max(24, int(getattr(self.settings, 'newsapi_lookback_hours', 72) or 72))
        from_dt = (datetime.now(UTC) - timedelta(hours=lookback)).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        contexts: dict[str, MatchContext] = {}
        headers = {'X-Api-Key': str(self.settings.newsapi_key)}
        async with httpx.AsyncClient(timeout=self.timeout, headers=headers) as client:
            for match in selected:
                params = {
                    'q': build_match_news_query(match),
                    'searchIn': 'title,description',
                    'language': 'en',
                    'from': from_dt,
                    'sortBy': 'publishedAt',
                    'pageSize': page_size,
                }
                stats['requests'] += 1
                try:
                    response = await client.get(f'{self.base_url}/everything', params=params)
                except Exception as exc:
                    stats['response_errors'] += 1
                    stats['last_body_preview'] = f'request failed: {exc}'
                    continue
                stats['http_statuses'].append(response.status_code)
                stats['last_body_preview'] = response.text[:1200]
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
                context = articles_to_context(match, articles, 'newsapi')
                if context is None:
                    continue
                contexts[match.match_key] = context
                stats['contexts_built'] += 1
                if len(preview['sample_contexts']) < 6:
                    preview['sample_contexts'].append({
                        'match_key': match.match_key,
                        'articles': len((context.payload or {}).get('articles') or []),
                        'home_win_probability': context.home_win_probability,
                        'away_win_probability': context.away_win_probability,
                    })
        return contexts, stats, preview
