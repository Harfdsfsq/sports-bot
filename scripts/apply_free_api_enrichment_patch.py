from __future__ import annotations

from pathlib import Path

RUNNER_PATH = Path("app/services/runner.py")
WEATHER_PATH = Path("app/providers/weather_common.py")


def patch_runner_scorebat() -> bool:
    if not RUNNER_PATH.exists():
        print(f"skip: {RUNNER_PATH} not found")
        return False
    src = RUNNER_PATH.read_text(encoding="utf-8")
    original = src

    if "self.scorebat = self._safe_provider('app.providers.scorebat', 'ScorebatContextProvider')" not in src:
        marker = "        self.gnews = self._safe_provider('app.providers.gnews', 'GNewsContextProvider')\n"
        src = src.replace(marker, marker + "        self.scorebat = self._safe_provider('app.providers.scorebat', 'ScorebatContextProvider')\n", 1)

    if "'scorebat': self.scorebat" not in src:
        marker = "            'gnews': self.gnews,\n"
        src = src.replace(marker, marker + "            'scorebat': self.scorebat,\n", 1)

    if "provider_name == 'scorebat'" not in src:
        marker = "        if provider_name == 'gnews':\n            explicit = bool(getattr(self.settings, 'enable_gnews_context', default))\n            return explicit or bool(getattr(self.settings, 'gnews_key', None))\n"
        src = src.replace(
            marker,
            marker + "        if provider_name == 'scorebat':\n"
                     "            return str(__import__('os').getenv('ENABLE_SCOREBAT_CONTEXT', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'}\n",
            1,
        )

    if "module_name.endswith('scorebat')" not in src:
        marker = "        if module_name.endswith('gnews') and not self._provider_enabled('gnews', default=True):\n            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')\n            return None\n"
        src = src.replace(
            marker,
            marker + "        if module_name.endswith('scorebat') and not self._provider_enabled('scorebat', default=True):\n"
                     "            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')\n"
                     "            return None\n",
            1,
        )

    if "'scorebat': self._select_provider_context_matches" not in src:
        marker = "                'gnews': self._select_provider_context_matches(context_target_matches, 'gnews', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n"
        src = src.replace(marker, marker + "                'scorebat': self._select_provider_context_matches(context_target_matches, 'scorebat', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n", 1)

    if "(scorebat_contexts, scorebat_stats, scorebat_preview)" not in src:
        marker = "                (gnews_contexts, gnews_stats, gnews_preview),\n            ) = await asyncio.gather(\n"
        src = src.replace(marker, "                (gnews_contexts, gnews_stats, gnews_preview),\n                (scorebat_contexts, scorebat_stats, scorebat_preview),\n            ) = await asyncio.gather(\n", 1)

        marker_call = "                self._fetch_provider(self.gnews, 'fetch_context', provider_targets['gnews'], empty_data={}),\n            )\n"
        src = src.replace(marker_call, "                self._fetch_provider(self.gnews, 'fetch_context', provider_targets['gnews'], empty_data={}),\n                self._fetch_provider(self.scorebat, 'fetch_context', provider_targets['scorebat'], empty_data={}),\n            )\n", 1)

    if "'scorebat': scorebat_contexts" not in src:
        marker = "                'gnews': gnews_contexts,\n"
        src = src.replace(marker, marker + "                'scorebat': scorebat_contexts,\n", 1)

    if "'scorebat': scorebat_stats" not in src:
        marker = "                'gnews': gnews_stats,\n"
        src = src.replace(marker, marker + "                'scorebat': scorebat_stats,\n", 1)

    if src != original:
        RUNNER_PATH.write_text(src, encoding="utf-8")
        print("patched: app/services/runner.py scorebat")
        return True
    print("already patched or no changes: app/services/runner.py scorebat")
    return False


OPENMETEO_METHODS = '''
    async def _geocode_openmeteo(
        self,
        client: httpx.AsyncClient,
        location: dict[str, str],
        stats: dict[str, Any],
    ) -> dict[str, Any] | None:
        cache = self._load_cache()
        query = str(location.get('query') or '').strip()
        if not query:
            return None
        cache_key = f"geocode::{query.lower()}"
        cached = self._cache_get(cache, cache_key)
        if cached is not None:
            stats['cache_hit'] = True
            return cached
        stats['requests'] += 1
        try:
            response = await client.get(
                'https://geocoding-api.open-meteo.com/v1/search',
                params={'name': query, 'count': 1, 'language': 'en', 'format': 'json'},
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
        results = payload.get('results') if isinstance(payload, dict) else None
        if not isinstance(results, list) or not results:
            return None
        first = results[0]
        if not isinstance(first, dict):
            return None
        try:
            lat = float(first.get('latitude'))
            lon = float(first.get('longitude'))
        except Exception:
            return None
        geocoded = {
            'latitude': lat,
            'longitude': lon,
            'city': str(first.get('name') or location.get('city') or '').strip(),
            'country': str(first.get('country') or location.get('country') or '').strip(),
        }
        self._cache_put(cache, cache_key, geocoded)
        self._write_cache(cache)
        return geocoded

    async def _fetch_openmeteo(
        self,
        client: httpx.AsyncClient,
        location: dict[str, str],
        kickoff: datetime,
        stats: dict[str, Any],
    ) -> dict[str, Any] | None:
        geocoded = await self._geocode_openmeteo(client, location, stats)
        if geocoded is None:
            return None
        stats['requests'] += 1
        try:
            response = await client.get(
                'https://api.open-meteo.com/v1/forecast',
                params={
                    'latitude': geocoded['latitude'],
                    'longitude': geocoded['longitude'],
                    'hourly': 'temperature_2m,precipitation,wind_speed_10m',
                    'timezone': 'UTC',
                    'forecast_days': 2,
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
        hourly = payload.get('hourly') if isinstance(payload, dict) else {}
        if not isinstance(hourly, dict):
            return None
        times = hourly.get('time') or []
        best_idx = None
        best_diff = None
        target = kickoff.astimezone(UTC)
        for idx, raw_time in enumerate(times):
            try:
                slot = datetime.fromisoformat(str(raw_time)).replace(tzinfo=UTC)
            except Exception:
                continue
            diff = abs((slot - target).total_seconds())
            if best_diff is None or diff < best_diff:
                best_diff = diff
                best_idx = idx
        if best_idx is None:
            return None

        def at(name: str) -> float | None:
            values = hourly.get(name) or []
            try:
                return self._to_float(values[best_idx])
            except Exception:
                return None

        stats['provider'] = 'openmeteo'
        return {
            'source': 'openmeteo',
            'city': geocoded.get('city') or location.get('city') or '',
            'country': geocoded.get('country') or location.get('country') or '',
            'temp_c': at('temperature_2m'),
            'wind_kph': at('wind_speed_10m'),
            'precip_mm': at('precipitation'),
            'condition': 'open-meteo forecast',
        }

    async def _fetch_meteostat_rapidapi(
        self,
        client: httpx.AsyncClient,
        location: dict[str, str],
        kickoff: datetime,
        stats: dict[str, Any],
    ) -> dict[str, Any] | None:
        if not getattr(self, 'meteostat_key', ''):
            return None
        geocoded = await self._geocode_openmeteo(client, location, stats)
        if geocoded is None:
            return None
        start = kickoff.astimezone(UTC).date().isoformat()
        end = (kickoff.astimezone(UTC) + timedelta(days=1)).date().isoformat()
        stats['requests'] += 1
        try:
            response = await client.get(
                'https://meteostat.p.rapidapi.com/point/hourly',
                params={'lat': geocoded['latitude'], 'lon': geocoded['longitude'], 'start': start, 'end': end, 'tz': 'UTC'},
                headers={'x-rapidapi-key': self.meteostat_key, 'x-rapidapi-host': self.meteostat_host},
                timeout=float(getattr(self, 'meteostat_timeout', self.timeout)),
            )
        except Exception:
            stats['response_errors'] += 1
            return None
        if response.status_code in {401, 403}:
            stats['response_errors'] += 1
            stats['auth_failed'] = True
            stats['last_body_preview'] = response.text[:500]
            return None
        if response.status_code == 429:
            stats['response_errors'] += 1
            stats['rate_limited'] = True
            stats['last_body_preview'] = response.text[:500]
            return None
        if response.status_code != 200:
            stats['response_errors'] += 1
            stats['last_body_preview'] = response.text[:500]
            return None
        try:
            payload = response.json()
        except Exception:
            stats['response_errors'] += 1
            return None
        rows = payload.get('data') if isinstance(payload, dict) else []
        if not isinstance(rows, list) or not rows:
            return None
        target = kickoff.astimezone(UTC)
        best = None
        best_diff = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            try:
                slot = parse_datetime(str(row.get('time')))
            except Exception:
                continue
            diff = abs((slot - target).total_seconds())
            if best_diff is None or diff < best_diff:
                best = row
                best_diff = diff
        if best is None:
            return None
        stats['provider'] = 'meteostat_rapidapi'
        return {
            'source': 'meteostat_rapidapi',
            'city': geocoded.get('city') or location.get('city') or '',
            'country': geocoded.get('country') or location.get('country') or '',
            'temp_c': self._to_float(best.get('temp')),
            'wind_kph': self._to_float(best.get('wspd')),
            'precip_mm': self._to_float(best.get('prcp')) or 0.0,
            'condition': 'meteostat hourly',
        }

'''


def patch_weather_common() -> bool:
    if not WEATHER_PATH.exists():
        print(f"skip: {WEATHER_PATH} not found")
        return False
    src = WEATHER_PATH.read_text(encoding="utf-8")
    original = src

    if "self.openmeteo_enabled" not in src:
        marker = "        self.openweather_enabled = _env_bool('OPENWEATHERMAP_ENABLED', True)\n"
        src = src.replace(
            marker,
            marker +
            "        self.openmeteo_enabled = _env_bool('OPENMETEO_ENABLED', True) and _env_bool('WEATHER_OPENMETEO_FALLBACK_ENABLED', True)\n"
            "        self.meteostat_key = str(os.getenv('METEOSTAT_RAPIDAPI_KEY') or '').strip()\n"
            "        self.meteostat_host = str(os.getenv('METEOSTAT_RAPIDAPI_HOST') or 'meteostat.p.rapidapi.com').strip()\n"
            "        self.meteostat_enabled = bool(self.meteostat_key) and _env_bool('METEOSTAT_RAPIDAPI_ENABLED', True) and _env_bool('WEATHER_METEOSTAT_FALLBACK_ENABLED', True)\n"
            "        self.meteostat_timeout = float(os.getenv('METEOSTAT_TIMEOUT_SECONDS') or 10.0)\n",
            1,
        )

    src = src.replace(
        "            'enabled': bool(self.weatherapi_key or self.openweather_key),",
        "            'enabled': bool(self.weatherapi_key or self.openweather_key or self.openmeteo_enabled or self.meteostat_key),",
        1,
    )

    fetch_marker = "            if payload is None and self.openweather_enabled and self.openweather_key:\n                payload = await self._fetch_openweather(client, location, match.commence_time, stats)\n"
    if fetch_marker in src and "_fetch_openmeteo(client" not in src:
        src = src.replace(
            fetch_marker,
            fetch_marker +
            "            if payload is None and self.openmeteo_enabled:\n"
            "                payload = await self._fetch_openmeteo(client, location, match.commence_time, stats)\n"
            "            if payload is None and self.meteostat_enabled:\n"
            "                payload = await self._fetch_meteostat_rapidapi(client, location, match.commence_time, stats)\n",
            1,
        )

    if "def _fetch_openmeteo" not in src:
        marker = "    def _apply_weather(\n"
        src = src.replace(marker, OPENMETEO_METHODS + "\n" + marker, 1)

    if src != original:
        WEATHER_PATH.write_text(src, encoding="utf-8")
        print("patched: app/providers/weather_common.py openmeteo/meteostat")
        return True
    print("already patched or no changes: app/providers/weather_common.py")
    return False


def main() -> int:
    patch_runner_scorebat()
    patch_weather_common()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
