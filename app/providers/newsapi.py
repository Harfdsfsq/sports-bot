from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.providers.news_common import articles_to_context, build_match_news_query
from app.schemas import Match, MatchContext

UTC = timezone.utc


class NewsApiContextProvider:
    """
    Combined news provider.

    Priority:
    1) Currents (better free quota)
    2) NewsAPI (fallback)
    """

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = str(settings.newsapi_base_url or 'https://newsapi.org/v2').rstrip('/')
        self.currents_base_url = str(
            os.getenv('CURRENTS_BASE_URL')
            or getattr(settings, 'currents_base_url', None)
            or 'https://api.currentsapi.services/v1'
        ).rstrip('/')
        self.timeout = float(
            os.getenv('NEWS_CONTEXT_TIMEOUT_SECONDS')
            or getattr(settings, 'newsapi_timeout_seconds', 15.0)
            or 15.0
        )
        self.newsapi_key = str(getattr(settings, 'newsapi_key', None) or os.getenv('NEWSAPI_KEY') or '').strip()
        self.currents_key = str(
            getattr(settings, 'currents_key', None)
            or os.getenv('CURRENTS_API_KEY')
            or os.getenv('CURRENTS_KEY')
            or ''
        ).strip()
        self.cache_ttl_minutes = max(
            30,
            int(os.getenv('NEWS_CONTEXT_CACHE_TTL_MINUTES') or getattr(settings, 'news_context_cache_ttl_minutes', 180) or 180),
        )

    async def fetch_context(self, matches: list[Match]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            'enabled': bool(getattr(self.settings, 'enable_newsapi_context', True)),
            'api_key_present': bool(self.newsapi_key or self.currents_key),
            'currents_enabled': bool(self.currents_key),
            'newsapi_enabled': bool(self.newsapi_key),
            'requests': 0,
            'currents_requests': 0,
            'newsapi_requests': 0,
            'cache_hits': 0,
            'response_errors': 0,
            'contexts_built': 0,
            'articles_seen': 0,
            'http_statuses': [],
            'last_body_preview': None,
            'rate_limited': False,
            'currents_rate_limited': False,
            'newsapi_rate_limited': False,
        }
        preview: dict[str, Any] = {'sample_articles': [], 'sample_contexts': []}

        if not stats['enabled'] or not stats['api_key_present']:
            return {}, stats, preview

        soccer_matches = [item for item in matches if item.sport_key == 'soccer']
        if not soccer_matches:
            return {}, stats, preview

        currents_limit = max(0, int(os.getenv('CURRENTS_MATCH_LIMIT') or 4))
        currents_limit = min(currents_limit, max(0, int(getattr(self.settings, 'currents_news_per_run_max', 3) or 0)))
        newsapi_limit = max(0, int(getattr(self.settings, 'newsapi_match_limit', 4) or 4))
        newsapi_limit = min(newsapi_limit, max(0, int(getattr(self.settings, 'newsapi_per_run_max', 1) or 0)))
        if currents_limit <= 0 and newsapi_limit <= 0:
            stats['budget_exhausted'] = True
            return {}, stats, preview
        page_size_currents = max(1, min(int(os.getenv('CURRENTS_ARTICLES_PER_MATCH') or 4), 20))
        page_size_newsapi = max(1, min(int(getattr(self.settings, 'newsapi_articles_per_match', 4) or 4), 20))
        lookback = max(24, int(os.getenv('CURRENTS_LOOKBACK_HOURS') or getattr(self.settings, 'newsapi_lookback_hours', 48) or 48))
        from_dt = (datetime.now(UTC) - timedelta(hours=lookback)).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
        target_limit = max(currents_limit, newsapi_limit, 1)
        selected = soccer_matches[:target_limit]
        cache = self._load_cache()

        currents_cooldown = self._cooldown_until('currents')
        newsapi_cooldown = self._cooldown_until('newsapi')
        if currents_cooldown is not None:
            stats['currents_rate_limited'] = True
        if newsapi_cooldown is not None:
            stats['newsapi_rate_limited'] = True

        contexts: dict[str, MatchContext] = {}
        async with httpx.AsyncClient(timeout=self.timeout) as client:
            for index, match in enumerate(selected):
                articles: list[dict[str, Any]] = []
                provider_used: str | None = None

                if self.currents_key and index < currents_limit and not stats['currents_rate_limited']:
                    cached = self._cache_get(cache, 'currents', match.match_key)
                    if cached is not None:
                        stats['cache_hits'] += 1
                        articles = cached
                        provider_used = 'currents'
                    else:
                        fetched = await self._fetch_currents_articles(
                            client=client,
                            match=match,
                            from_dt=from_dt,
                            page_size=page_size_currents,
                            stats=stats,
                        )
                        if fetched is not None:
                            self._cache_put(cache, 'currents', match.match_key, fetched)
                            articles = fetched
                            provider_used = 'currents'

                if not articles and self.newsapi_key and index < newsapi_limit and not stats['newsapi_rate_limited']:
                    cached = self._cache_get(cache, 'newsapi', match.match_key)
                    if cached is not None:
                        stats['cache_hits'] += 1
                        articles = cached
                        provider_used = 'newsapi'
                    else:
                        fetched = await self._fetch_newsapi_articles(
                            client=client,
                            match=match,
                            from_dt=from_dt,
                            page_size=page_size_newsapi,
                            stats=stats,
                        )
                        if fetched is not None:
                            self._cache_put(cache, 'newsapi', match.match_key, fetched)
                            articles = fetched
                            provider_used = 'newsapi'

                if not articles or not provider_used:
                    continue

                stats['articles_seen'] += len(articles)
                if articles and len(preview['sample_articles']) < 3:
                    preview['sample_articles'].append(articles[0])

                context = articles_to_context(match, articles, provider_used)
                if context is None:
                    continue
                contexts[match.match_key] = context
                stats['contexts_built'] += 1
                if len(preview['sample_contexts']) < 6:
                    preview['sample_contexts'].append(
                        {
                            'match_key': match.match_key,
                            'provider': provider_used,
                            'articles': len((context.payload or {}).get('articles') or []),
                            'home_win_probability': context.home_win_probability,
                            'away_win_probability': context.away_win_probability,
                        }
                    )

        self._write_cache(cache)
        stats['rate_limited'] = bool(stats['currents_rate_limited'] and stats['newsapi_rate_limited'])
        return contexts, stats, preview

    async def _fetch_currents_articles(
        self,
        *,
        client: httpx.AsyncClient,
        match: Match,
        from_dt: str,
        page_size: int,
        stats: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        params = {
            'query': build_match_news_query(match),
            'language': 'en',
            'category': 'sports',
            'type': 1,
            'start_date': from_dt,
            'page_number': 1,
            'page_size': page_size,
            'limit': page_size,
            'apiKey': self.currents_key,
        }
        headers = {'Authorization': self.currents_key}
        stats['requests'] += 1
        stats['currents_requests'] += 1
        try:
            response = await client.get(f'{self.currents_base_url}/search', params=params, headers=headers)
        except Exception as exc:
            stats['response_errors'] += 1
            stats['last_body_preview'] = f'currents request failed: {exc}'
            return None

        stats['http_statuses'].append(response.status_code)
        stats['last_body_preview'] = response.text[:1200]
        if response.status_code == 429:
            stats['response_errors'] += 1
            stats['currents_rate_limited'] = True
            self._activate_cooldown('currents', minutes=max(30, int(os.getenv('CURRENTS_RATE_LIMIT_COOLDOWN_MINUTES') or 720)))
            return None
        if response.status_code in {401, 403}:
            stats['response_errors'] += 1
            stats['currents_rate_limited'] = True
            self._activate_cooldown('currents', minutes=max(60, int(os.getenv('CURRENTS_AUTH_ERROR_COOLDOWN_MINUTES') or 1440)))
            return None
        if response.status_code != 200:
            stats['response_errors'] += 1
            return None

        try:
            payload = response.json()
        except Exception:
            stats['response_errors'] += 1
            return None
        rows = payload.get('news') if isinstance(payload, dict) else None
        if not isinstance(rows, list):
            return []

        normalized: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            normalized.append(
                {
                    'title': row.get('title'),
                    'description': row.get('description'),
                    'content': row.get('description'),
                    'publishedAt': row.get('published'),
                    'url': row.get('url'),
                }
            )
        return normalized

    async def _fetch_newsapi_articles(
        self,
        *,
        client: httpx.AsyncClient,
        match: Match,
        from_dt: str,
        page_size: int,
        stats: dict[str, Any],
    ) -> list[dict[str, Any]] | None:
        params = {
            'q': build_match_news_query(match),
            'searchIn': 'title,description',
            'language': 'en',
            'from': from_dt,
            'sortBy': 'publishedAt',
            'pageSize': page_size,
        }
        headers = {'X-Api-Key': self.newsapi_key}
        stats['requests'] += 1
        stats['newsapi_requests'] += 1
        try:
            response = await client.get(f'{self.base_url}/everything', params=params, headers=headers)
        except Exception as exc:
            stats['response_errors'] += 1
            stats['last_body_preview'] = f'newsapi request failed: {exc}'
            return None

        stats['http_statuses'].append(response.status_code)
        stats['last_body_preview'] = response.text[:1200]
        if response.status_code == 429:
            stats['response_errors'] += 1
            stats['newsapi_rate_limited'] = True
            self._activate_cooldown('newsapi', minutes=max(30, int(os.getenv('NEWSAPI_RATE_LIMIT_COOLDOWN_MINUTES') or 720)))
            return None
        if response.status_code in {401, 403}:
            stats['response_errors'] += 1
            stats['newsapi_rate_limited'] = True
            self._activate_cooldown('newsapi', minutes=max(60, int(os.getenv('NEWSAPI_AUTH_ERROR_COOLDOWN_MINUTES') or 1440)))
            return None
        if response.status_code != 200:
            stats['response_errors'] += 1
            return None

        try:
            payload = response.json()
        except Exception:
            stats['response_errors'] += 1
            return None
        articles = [item for item in (payload.get('articles') or []) if isinstance(item, dict)] if isinstance(payload, dict) else []
        return articles

    def _cache_path(self) -> Path:
        return Path(getattr(self.settings, 'state_path', '.data/state.json')).resolve().parent / 'provider_cache' / 'news_context_cache.json'

    def _load_cache(self) -> dict[str, Any]:
        path = self._cache_path()
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return {'entries': {}}

    def _cache_get(self, cache: dict[str, Any], provider: str, match_key: str) -> list[dict[str, Any]] | None:
        entry = (cache.get('entries') or {}).get(f'{provider}::{match_key}')
        if not isinstance(entry, dict):
            return None
        try:
            fetched_at = datetime.fromisoformat(str(entry.get('fetched_at')).replace('Z', '+00:00')).astimezone(UTC)
        except Exception:
            return None
        if datetime.now(UTC) - fetched_at > timedelta(minutes=self.cache_ttl_minutes):
            return None
        rows = entry.get('articles')
        return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else None

    def _cache_put(self, cache: dict[str, Any], provider: str, match_key: str, articles: list[dict[str, Any]]) -> None:
        cache.setdefault('entries', {})[f'{provider}::{match_key}'] = {
            'fetched_at': datetime.now(UTC).isoformat(),
            'articles': articles[:20],
        }

    def _write_cache(self, cache: dict[str, Any]) -> None:
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
        except Exception:
            return

    def _cooldown_path(self, provider: str) -> Path:
        return Path(getattr(self.settings, 'state_path', '.data/state.json')).resolve().parent / 'provider_cache' / f'{provider}_news_rate_limit.json'

    def _read_provider_cooldown_until(self, provider: str) -> datetime | None:
        path = self._cooldown_path(provider)
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None
        raw_until = payload.get('cooldown_until')
        if not raw_until:
            return None
        try:
            dt = datetime.fromisoformat(str(raw_until).replace('Z', '+00:00')).astimezone(UTC)
        except Exception:
            return None
        if dt <= datetime.now(UTC):
            try:
                path.unlink(missing_ok=True)
            except Exception:
                pass
            return None
        return dt

    def _cooldown_until(self, provider: str | None = None) -> datetime | None:
        if provider:
            return self._read_provider_cooldown_until(provider)

        configured_providers: list[str] = []
        if self.currents_key:
            configured_providers.append('currents')
        if self.newsapi_key:
            configured_providers.append('newsapi')
        if not configured_providers:
            return None

        cooldowns = {
            provider_name: self._read_provider_cooldown_until(provider_name)
            for provider_name in configured_providers
        }
        active_cooldowns = [value for value in cooldowns.values() if value is not None]

        if not active_cooldowns:
            return None
        if len(active_cooldowns) < len(configured_providers):
            return None
        return min(active_cooldowns)

    def _activate_cooldown(self, provider: str, *, minutes: int) -> None:
        path = self._cooldown_path(provider)
        path.parent.mkdir(parents=True, exist_ok=True)
        until = datetime.now(UTC) + timedelta(minutes=max(1, minutes))
        payload = {'cooldown_until': until.isoformat(), 'created_at': datetime.now(UTC).isoformat()}
        try:
            path.write_text(json.dumps(payload, ensure_ascii=False), encoding='utf-8')
        except Exception:
            return
