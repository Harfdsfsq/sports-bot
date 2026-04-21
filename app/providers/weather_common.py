from __future__ import annotations

import json
import os
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, MatchContext
from app.utils import clamp, parse_datetime

UTC = timezone.utc


def _env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


class WeatherContextEnricher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.weatherapi_key = str(
            os.getenv('WEATHERAPI_KEY')
            or getattr(settings, 'weatherapi_key', None)
            or ''
        ).strip()
        self.openweather_key = str(
            os.getenv('OPENWEATHERMAP_API_KEY')
            or os.getenv('OPENWEATHER_API_KEY')
            or os.getenv('OPENWEATHERMAP_KEY')
            or getattr(settings, 'openweathermap_api_key', None)
            or getattr(settings, 'openweather_api_key', None)
            or ''
        ).strip()
        self.weatherapi_enabled = _env_bool('WEATHERAPI_ENABLED', True)
        self.openweather_enabled = _env_bool('OPENWEATHERMAP_ENABLED', True)
        self.timeout = float(os.getenv('WEATHER_TIMEOUT_SECONDS') or getattr(settings, 'weather_timeout_seconds', 8.0) or 8.0)
        self.cache_ttl_minutes = max(30, int(os.getenv('WEATHER_CACHE_TTL_MINUTES') or getattr(settings, 'weather_cache_ttl_minutes', 180) or 180))
        self.wind_penalty_kph = float(os.getenv('WEATHER_WIND_PENALTY_KPH') or 18.0)
        self.severe_wind_kph = float(os.getenv('WEATHER_SEVERE_WIND_KPH') or 25.0)
        self.rain_penalty_mm = float(os.getenv('WEATHER_RAIN_PENALTY_MM') or 1.0)
        self.severe_rain_mm = float(os.getenv('WEATHER_SEVERE_RAIN_MM') or 4.0)

    async def enrich_context(
        self,
        client: httpx.AsyncClient,
        match: Match,
        fixture: dict[str, Any],
        context: MatchContext,
    ) -> tuple[MatchContext, dict[str, Any]]:
        stats: dict[str, Any] = {
            'enabled': bool(self.weatherapi_key or self.openweather_key),
            'cache_hit': False,
            'requests': 0,
            'response_errors': 0,
            'provider': None,
            'enriched': False,
        }
        if not stats['enabled']:
            return context, stats

        location = self._location_from_fixture(match, fixture)
        if location is None:
            stats['reason'] = 'missing_location'
            return context, stats

        cache = self._load_cache()
        cache_key = self._cache_key(location, match.commence_time)
        cached = self._cache_get(cache, cache_key)
        payload: dict[str, Any] | None = cached
        if cached is not None:
            stats['cache_hit'] = True

        if payload is None:
            if self.weatherapi_enabled and self.weatherapi_key:
                payload = await self._fetch_weatherapi(client, location, match.commence_time, stats)
            if payload is None and self.openweather_enabled and self.openweather_key:
                payload = await self._fetch_openweather(client, location, match.commence_time, stats)
            if payload is not None:
                self._cache_put(cache, cache_key, payload)
                self._write_cache(cache)

        if payload is None:
            stats['reason'] = 'no_weather_payload'
            return context, stats

        updated = self._apply_weather(match, context, location, payload)
        stats['enriched'] = True
        return updated, stats

    def _location_from_fixture(self, match: Match, fixture_row: dict[str, Any]) -> dict[str, str] | None:
        fixture = fixture_row.get('fixture') or {}
        venue = fixture.get('venue') or {}
        league = fixture_row.get('league') or {}
        city = str(venue.get('city') or '').strip()
        venue_name = str(venue.get('name') or '').strip()
        country = str(league.get('country') or '').strip()
        query = ''
        if city and country:
            query = f'{city}, {country}'
        elif city:
            query = city
        elif venue_name and country:
            query = f'{venue_name}, {country}'
        elif venue_name:
            query = venue_name
        elif country:
            query = f'{match.home_team}, {country}'
        else:
            query = match.home_team
        if not query.strip():
            return None
        return {
            'query': query.strip(),
            'city': city,
            'venue': venue_name,
            'country': country,
        }

    async def _fetch_weatherapi(
        self,
        client: httpx.AsyncClient,
        location: dict[str, str],
        kickoff: datetime,
        stats: dict[str, Any],
    ) -> dict[str, Any] | None:
        stats['requests'] += 1
        try:
            response = await client.get(
                'https://api.weatherapi.com/v1/forecast.json',
                params={
                    'key': self.weatherapi_key,
                    'q': location['query'],
                    'days': 2,
                    'aqi': 'no',
                    'alerts': 'no',
                },
                timeout=self.timeout,
            )
        except Exception:
            stats['response_errors'] += 1
            return None
        if response.status_code != 200:
            stats['response_errors'] += 1
            return None
        try:
            payload = response.json()
        except Exception:
            stats['response_errors'] += 1
            return None
        forecast = payload.get('forecast') or {}
        forecast_days = forecast.get('forecastday') or []
        candidates: list[dict[str, Any]] = []
        for day in forecast_days:
            for hour in day.get('hour') or []:
                if isinstance(hour, dict):
                    candidates.append(hour)
        if not candidates:
            current = payload.get('current') or {}
            if isinstance(current, dict) and current:
                candidates = [current]
        target = self._closest_weatherapi_slot(candidates, kickoff)
        if target is None:
            return None
        stats['provider'] = 'weatherapi'
        return {
            'source': 'weatherapi',
            'city': str((payload.get('location') or {}).get('name') or location.get('city') or '').strip(),
            'country': str((payload.get('location') or {}).get('country') or location.get('country') or '').strip(),
            'temp_c': self._to_float(target.get('temp_c')),
            'wind_kph': self._to_float(target.get('wind_kph')),
            'precip_mm': self._to_float(target.get('precip_mm')),
            'condition': str(((target.get('condition') or {}).get('text')) or '').strip(),
        }

    async def _fetch_openweather(
        self,
        client: httpx.AsyncClient,
        location: dict[str, str],
        kickoff: datetime,
        stats: dict[str, Any],
    ) -> dict[str, Any] | None:
        stats['requests'] += 1
        try:
            response = await client.get(
                'https://api.openweathermap.org/data/2.5/forecast',
                params={
                    'q': location['query'],
                    'appid': self.openweather_key,
                    'units': 'metric',
                    'cnt': 16,
                },
                timeout=self.timeout,
            )
        except Exception:
            stats['response_errors'] += 1
            return None
        if response.status_code != 200:
            stats['response_errors'] += 1
            return None
        try:
            payload = response.json()
        except Exception:
            stats['response_errors'] += 1
            return None
        rows = [item for item in (payload.get('list') or []) if isinstance(item, dict)]
        target = self._closest_openweather_slot(rows, kickoff)
        if target is None:
            return None
        weather_rows = target.get('weather') or []
        first_weather = weather_rows[0] if weather_rows and isinstance(weather_rows[0], dict) else {}
        stats['provider'] = 'openweathermap'
        return {
            'source': 'openweathermap',
            'city': str((payload.get('city') or {}).get('name') or location.get('city') or '').strip(),
            'country': str((payload.get('city') or {}).get('country') or location.get('country') or '').strip(),
            'temp_c': self._to_float(((target.get('main') or {}).get('temp'))),
            'wind_kph': self._mps_to_kph(self._to_float(((target.get('wind') or {}).get('speed')))),
            'precip_mm': self._openweather_precip_mm(target),
            'condition': str(first_weather.get('description') or first_weather.get('main') or '').strip(),
        }

    def _apply_weather(
        self,
        match: Match,
        context: MatchContext,
        location: dict[str, str],
        payload: dict[str, Any],
    ) -> MatchContext:
        details = deepcopy(dict(getattr(context, 'details', {}) or {}))
        temp_c = self._to_float(payload.get('temp_c'))
        wind_kph = self._to_float(payload.get('wind_kph')) or 0.0
        precip_mm = self._to_float(payload.get('precip_mm')) or 0.0
        condition = str(payload.get('condition') or '').lower()

        factor = 1.0
        reasons: list[str] = []

        if wind_kph >= self.severe_wind_kph:
            factor -= 0.08
            reasons.append('severe_wind')
        elif wind_kph >= self.wind_penalty_kph:
            factor -= 0.04
            reasons.append('wind')

        if precip_mm >= self.severe_rain_mm:
            factor -= 0.08
            reasons.append('severe_rain')
        elif precip_mm >= self.rain_penalty_mm:
            factor -= 0.04
            reasons.append('rain')

        if temp_c is not None and temp_c <= 0.0:
            factor -= 0.04
            reasons.append('freezing')
        elif temp_c is not None and temp_c >= 32.0:
            factor -= 0.03
            reasons.append('heat')

        if any(token in condition for token in ('snow', 'storm', 'thunder', 'hail')):
            factor -= 0.05
            reasons.append('severe_condition')

        factor = clamp(factor, 0.78, 1.03)

        expected_home = getattr(context, 'expected_home', None)
        expected_away = getattr(context, 'expected_away', None)
        if expected_home is not None:
            expected_home = clamp(float(expected_home) * factor, 0.15, 4.80)
        if expected_away is not None:
            expected_away = clamp(float(expected_away) * factor, 0.15, 4.80)

        details.update(
            {
                'weather_source': str(payload.get('source') or ''),
                'weather_query': location.get('query'),
                'weather_city': str(payload.get('city') or location.get('city') or '').strip(),
                'weather_country': str(payload.get('country') or location.get('country') or '').strip(),
                'weather_condition': str(payload.get('condition') or '').strip(),
                'weather_temp_c': round(temp_c, 2) if temp_c is not None else None,
                'weather_wind_kph': round(wind_kph, 2),
                'weather_precip_mm': round(precip_mm, 2),
                'weather_total_factor': round(factor, 3),
                'weather_adjustment_reasons': reasons,
                'weather_adjustment_applied': bool(reasons),
                'weather_home_team': match.home_team,
                'weather_away_team': match.away_team,
            }
        )

        confidence = float(getattr(context, 'confidence', 58.0) or 58.0)
        if reasons:
            confidence = clamp(confidence + 0.8, 50.0, 78.0)

        return MatchContext(
            source=context.source,
            payload=dict(getattr(context, 'payload', {}) or {}),
            expected_home=expected_home,
            expected_away=expected_away,
            home_win_probability=getattr(context, 'home_win_probability', None),
            away_win_probability=getattr(context, 'away_win_probability', None),
            home_starting=getattr(context, 'home_starting', None),
            away_starting=getattr(context, 'away_starting', None),
            confidence=confidence,
            profits=dict(getattr(context, 'profits', {}) or {}),
            details=details,
        )

    def _cache_path(self) -> Path:
        return Path(getattr(self.settings, 'state_path', '.data/state.json')).resolve().parent / 'provider_cache' / 'weather_context_cache.json'

    def _load_cache(self) -> dict[str, Any]:
        path = self._cache_path()
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return {'entries': {}}

    def _cache_key(self, location: dict[str, str], kickoff: datetime) -> str:
        start = kickoff.astimezone(UTC).replace(minute=0, second=0, microsecond=0).isoformat()
        return f"{location.get('query', '').strip().lower()}::{start}"

    def _cache_get(self, cache: dict[str, Any], key: str) -> dict[str, Any] | None:
        entry = (cache.get('entries') or {}).get(key)
        if not isinstance(entry, dict):
            return None
        try:
            fetched_at = parse_datetime(str(entry.get('fetched_at') or ''))
        except Exception:
            return None
        if datetime.now(UTC) - fetched_at > timedelta(minutes=self.cache_ttl_minutes):
            return None
        payload = entry.get('payload')
        return payload if isinstance(payload, dict) else None

    @staticmethod
    def _cache_put(cache: dict[str, Any], key: str, payload: dict[str, Any]) -> None:
        cache.setdefault('entries', {})[key] = {
            'fetched_at': datetime.now(UTC).isoformat(),
            'payload': payload,
        }

    def _write_cache(self, cache: dict[str, Any]) -> None:
        path = self._cache_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            path.write_text(json.dumps(cache, ensure_ascii=False), encoding='utf-8')
        except Exception:
            return

    @staticmethod
    def _closest_weatherapi_slot(rows: list[dict[str, Any]], kickoff: datetime) -> dict[str, Any] | None:
        best: tuple[float, dict[str, Any]] | None = None
        kickoff_ts = kickoff.astimezone(UTC).timestamp()
        for row in rows:
            epoch = row.get('time_epoch')
            slot_ts = None
            if epoch not in (None, ''):
                try:
                    slot_ts = float(epoch)
                except Exception:
                    slot_ts = None
            if slot_ts is None:
                raw_time = row.get('last_updated') or row.get('time')
                try:
                    slot_ts = parse_datetime(str(raw_time)).timestamp()
                except Exception:
                    continue
            diff = abs(slot_ts - kickoff_ts)
            if best is None or diff < best[0]:
                best = (diff, row)
        return best[1] if best else None

    @staticmethod
    def _closest_openweather_slot(rows: list[dict[str, Any]], kickoff: datetime) -> dict[str, Any] | None:
        best: tuple[float, dict[str, Any]] | None = None
        kickoff_ts = kickoff.astimezone(UTC).timestamp()
        for row in rows:
            ts = row.get('dt')
            try:
                slot_ts = float(ts)
            except Exception:
                continue
            diff = abs(slot_ts - kickoff_ts)
            if best is None or diff < best[0]:
                best = (diff, row)
        return best[1] if best else None

    @staticmethod
    def _openweather_precip_mm(row: dict[str, Any]) -> float:
        rain = row.get('rain') or {}
        snow = row.get('snow') or {}
        value = 0.0
        for block in (rain, snow):
            if isinstance(block, dict):
                for key in ('3h', '1h'):
                    try:
                        value += float(block.get(key) or 0.0)
                    except Exception:
                        continue
        return value

    @staticmethod
    def _mps_to_kph(value: float | None) -> float | None:
        if value is None:
            return None
        return value * 3.6

    @staticmethod
    def _to_float(value: Any) -> float | None:
        try:
            if value in (None, ''):
                return None
            return float(value)
        except Exception:
            return None
