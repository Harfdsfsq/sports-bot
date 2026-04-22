from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

UTC = timezone.utc
_PATCH_APPLIED = False


def _clean_numeric_text(value: Any) -> str:
    text = str(value or '').strip()
    if not text:
        return ''
    text = text.replace('\xa0', ' ').replace(',', '.')
    text = text.replace('−', '-').replace('–', '-').replace('—', '-')
    text = re.sub(r'\s+', '', text)
    return text


def _smart_float(value: Any) -> float | None:
    if value in (None, ''):
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float) and (math.isnan(value) or math.isinf(value)):
            return None
        return float(value)
    text = _clean_numeric_text(value)
    if not text:
        return None
    pct = text.endswith('%')
    if pct:
        text = text[:-1]
    # Keep only the first numeric token.
    match = re.search(r'-?\d+(?:\.\d+)?', text)
    if not match:
        return None
    try:
        number = float(match.group(0))
    except Exception:
        return None
    if math.isnan(number) or math.isinf(number):
        return None
    return number


def _smart_unit_percent(value: Any) -> float | None:
    number = _smart_float(value)
    if number is None:
        return None
    text = _clean_numeric_text(value)
    had_percent = text.endswith('%') if text else False
    if had_percent or number > 1.0:
        number /= 100.0
    return max(0.0, min(1.0, number))


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _provider_state_path(settings: Any, filename: str) -> Path:
    base = Path(getattr(settings, 'state_path', '.data/state.json')).resolve().parent
    path = base / 'provider_cache' / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _load_json(path: Path, default: dict[str, Any]) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
        return payload if isinstance(payload, dict) else dict(default)
    except Exception:
        return dict(default)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    except Exception:
        return


def _zero_yield_state(settings: Any, provider_name: str) -> tuple[int, datetime | None]:
    path = _provider_state_path(settings, f'{provider_name}_health.json')
    payload = _load_json(path, {'zero_runs': 0, 'cooldown_until': None})
    cooldown_raw = payload.get('cooldown_until')
    cooldown_until = None
    if cooldown_raw:
        try:
            cooldown_until = datetime.fromisoformat(str(cooldown_raw).replace('Z', '+00:00')).astimezone(UTC)
        except Exception:
            cooldown_until = None
    return int(payload.get('zero_runs') or 0), cooldown_until


def _set_zero_yield_state(settings: Any, provider_name: str, *, zero_runs: int, cooldown_until: datetime | None) -> None:
    path = _provider_state_path(settings, f'{provider_name}_health.json')
    payload = {
        'updated_at': _now_utc().isoformat(),
        'zero_runs': max(0, int(zero_runs)),
        'cooldown_until': cooldown_until.isoformat() if cooldown_until else None,
    }
    _write_json(path, payload)


def _wrap_zero_yield_provider(provider_cls: Any, provider_name: str, *, cooldown_hours: int = 6, zero_threshold: int = 3) -> None:
    if getattr(provider_cls, f'_runtime_{provider_name}_zero_yield_patch_applied', False):
        return
    original = provider_cls.fetch_context

    async def patched(self, matches):
        zero_runs, cooldown_until = _zero_yield_state(self.settings, provider_name)
        if cooldown_until and cooldown_until > _now_utc():
            stats = {
                'enabled': True,
                'api_key_present': True,
                'requests': 0,
                'response_errors': 0,
                'contexts_built': 0,
                'cooldown_active': True,
                'cooldown_until': cooldown_until.isoformat(),
                'last_body_preview': f'health backoff active until {cooldown_until.isoformat()}',
            }
            preview = {'sample_rows': [], 'sample_contexts': []}
            return {}, stats, preview

        contexts, stats, preview = await original(self, matches)
        built = int((stats or {}).get('contexts_built') or 0)
        requests = int((stats or {}).get('requests') or 0)
        if requests > 0 and built <= 0:
            zero_runs += 1
        else:
            zero_runs = 0
        new_cooldown = None
        if zero_runs >= zero_threshold:
            new_cooldown = _now_utc() + timedelta(hours=max(1, cooldown_hours))
            if isinstance(stats, dict):
                stats['health_backoff'] = True
                stats['health_backoff_until'] = new_cooldown.isoformat()
        _set_zero_yield_state(self.settings, provider_name, zero_runs=zero_runs, cooldown_until=new_cooldown)
        return contexts, stats, preview

    provider_cls.fetch_context = patched
    setattr(provider_cls, f'_runtime_{provider_name}_zero_yield_patch_applied', True)


def _get_nested_values(obj: Any) -> Iterable[Any]:
    if isinstance(obj, dict):
        for value in obj.values():
            yield value
    elif isinstance(obj, list):
        for value in obj:
            yield value


def _find_first(data: Any, keys: set[str], depth: int = 0) -> Any:
    if depth > 5:
        return None
    if isinstance(data, dict):
        for key, value in data.items():
            if str(key).lower() in keys and value not in (None, '', [], {}):
                return value
        for value in data.values():
            found = _find_first(value, keys, depth + 1)
            if found not in (None, '', [], {}):
                return found
    elif isinstance(data, list):
        for value in data:
            found = _find_first(value, keys, depth + 1)
            if found not in (None, '', [], {}):
                return found
    return None


def _ctx_lookup(bet: Any, *aliases: str) -> Any:
    keys = {alias.lower() for alias in aliases}
    for source in (
        getattr(bet, 'analysis', None),
        getattr(bet, 'diagnostics', None),
        getattr(bet, 'source_summary', None),
    ):
        found = _find_first(source, keys)
        if found not in (None, '', [], {}):
            return found
    return None


def _fmt_pct(value: Any) -> str | None:
    number = _smart_unit_percent(value)
    if number is None:
        number = _smart_float(value)
        if number is None:
            return None
        if number > 1.0:
            return f'{number:.1f}%'
        return f'{number * 100.0:.1f}%'
    return f'{number * 100.0:.1f}%'


def _fmt_num(value: Any, digits: int = 2) -> str | None:
    number = _smart_float(value)
    if number is None:
        return None
    return f'{number:.{digits}f}'


def _side_from_selection(bet: Any) -> str | None:
    family = str(getattr(bet, 'family', '') or '').lower()
    if family not in {'h2h', 'dnb', 'spreads'}:
        return None
    team_side = str(getattr(bet, 'team_side', '') or '').lower().strip()
    if team_side in {'home', 'away'}:
        return team_side
    selection = str(getattr(bet, 'selection', '') or '').strip().lower()
    selection_key = str(getattr(bet, 'selection_key', '') or '').strip().lower()
    home = str(getattr(bet, 'home_team', '') or '').strip().lower()
    away = str(getattr(bet, 'away_team', '') or '').strip().lower()
    compact = re.sub(r'\([^)]*\)', '', selection).strip()
    if selection in {'1', 'п1', 'home'} or selection_key == 'home' or compact == home:
        return 'home'
    if selection in {'2', 'п2', 'away'} or selection_key == 'away' or compact == away:
        return 'away'
    if home and selection.startswith(home):
        return 'home'
    if away and selection.startswith(away):
        return 'away'
    return None


def _xg_comment(bet: Any) -> str | None:
    eh = _smart_float(getattr(bet, 'expected_home', None))
    ea = _smart_float(getattr(bet, 'expected_away', None))
    if eh is None or ea is None:
        return None
    family = str(getattr(bet, 'family', '') or '').lower()
    side = _side_from_selection(bet)
    total = eh + ea
    if family == 'totals':
        line = _smart_float(getattr(bet, 'point', None))
        sel = str(getattr(bet, 'selection', '') or '').lower()
        if line is not None:
            return f'xG-модель даёт {eh:.2f}:{ea:.2f} (сумма {total:.2f}) против линии {line:g}.'.strip()
        return f'xG-модель даёт {eh:.2f}:{ea:.2f} (сумма {total:.2f}).'
    if family in {'h2h', 'dnb', 'spreads'} and side in {'home', 'away'}:
        diff = eh - ea
        if side == 'away' and diff >= 0.20:
            return f'По xG преимущество скорее у хозяев ({eh:.2f}:{ea:.2f}), поэтому ставка идёт против профиля моментов и требует сильного рыночного/контекстного подтверждения.'
        if side == 'home' and diff <= -0.20:
            return f'По xG преимущество скорее у гостей ({eh:.2f}:{ea:.2f}), поэтому ставка идёт против профиля моментов и требует сильного рыночного/контекстного подтверждения.'
    return f'По модели ожидаемые голы: {eh:.2f}:{ea:.2f}.'.strip()


def _detail_lines(bet: Any) -> list[str]:
    lines: list[str] = []

    # Form
    home_form = _ctx_lookup(bet, 'home_form', 'api_football_home_form', 'futrix_home_form')
    away_form = _ctx_lookup(bet, 'away_form', 'api_football_away_form', 'futrix_away_form')
    home_form_str = _ctx_lookup(bet, 'home_form_string', 'home_form_last5', 'standings_home_form', 'form_home')
    away_form_str = _ctx_lookup(bet, 'away_form_string', 'away_form_last5', 'standings_away_form', 'form_away')
    form_parts: list[str] = []
    if home_form_str or away_form_str:
        if home_form_str:
            form_parts.append(f'{getattr(bet, "home_team", "Хозяева")}: {home_form_str}')
        if away_form_str:
            form_parts.append(f'{getattr(bet, "away_team", "Гости")}: {away_form_str}')
    else:
        home_form_pct = _fmt_pct(home_form)
        away_form_pct = _fmt_pct(away_form)
        if home_form_pct or away_form_pct:
            if home_form_pct:
                form_parts.append(f'{getattr(bet, "home_team", "Хозяева")}: {home_form_pct}')
            if away_form_pct:
                form_parts.append(f'{getattr(bet, "away_team", "Гости")}: {away_form_pct}')
    if form_parts:
        lines.append('Форма: ' + ' | '.join(form_parts) + '.')

    # Table / standings
    home_rank = _ctx_lookup(bet, 'home_rank', 'standings_home_rank', 'table_home_rank', 'home_position')
    away_rank = _ctx_lookup(bet, 'away_rank', 'standings_away_rank', 'table_away_rank', 'away_position')
    home_points = _ctx_lookup(bet, 'home_points', 'standings_home_points', 'table_home_points')
    away_points = _ctx_lookup(bet, 'away_points', 'standings_away_points', 'table_away_points')
    if home_rank or away_rank or home_points or away_points:
        home_bits = []
        away_bits = []
        if home_rank not in (None, ''):
            home_bits.append(f'#{int(float(home_rank))}')
        if home_points not in (None, ''):
            home_bits.append(f'{int(float(home_points))} очков')
        if away_rank not in (None, ''):
            away_bits.append(f'#{int(float(away_rank))}')
        if away_points not in (None, ''):
            away_bits.append(f'{int(float(away_points))} очков')
        table_parts = []
        if home_bits:
            table_parts.append(f'{getattr(bet, "home_team", "Хозяева")}: ' + ', '.join(home_bits))
        if away_bits:
            table_parts.append(f'{getattr(bet, "away_team", "Гости")}: ' + ', '.join(away_bits))
        if table_parts:
            lines.append('Турнирная таблица: ' + ' | '.join(table_parts) + '.')

    # Attack / defense
    ha = _ctx_lookup(bet, 'home_attack', 'api_football_home_attack')
    aa = _ctx_lookup(bet, 'away_attack', 'api_football_away_attack')
    hd = _ctx_lookup(bet, 'home_defense', 'api_football_home_defense')
    ad = _ctx_lookup(bet, 'away_defense', 'api_football_away_defense')
    gf_h = _ctx_lookup(bet, 'home_gf_pg', 'gf_home_pg')
    gf_a = _ctx_lookup(bet, 'away_gf_pg', 'gf_away_pg')
    ga_h = _ctx_lookup(bet, 'home_ga_pg', 'ga_home_pg')
    ga_a = _ctx_lookup(bet, 'away_ga_pg', 'ga_away_pg')
    atk_parts = []
    if ha is not None or hd is not None or gf_h is not None or ga_h is not None:
        sub = []
        if ha is not None:
            x = _fmt_pct(ha)
            if x: sub.append(f'атака {x}')
        if hd is not None:
            x = _fmt_pct(hd)
            if x: sub.append(f'оборона {x}')
        if gf_h is not None:
            x = _fmt_num(gf_h)
            if x: sub.append(f'GF/м {x}')
        if ga_h is not None:
            x = _fmt_num(ga_h)
            if x: sub.append(f'GA/м {x}')
        if sub:
            atk_parts.append(f'{getattr(bet, "home_team", "Хозяева")}: ' + ', '.join(sub))
    if aa is not None or ad is not None or gf_a is not None or ga_a is not None:
        sub = []
        if aa is not None:
            x = _fmt_pct(aa)
            if x: sub.append(f'атака {x}')
        if ad is not None:
            x = _fmt_pct(ad)
            if x: sub.append(f'оборона {x}')
        if gf_a is not None:
            x = _fmt_num(gf_a)
            if x: sub.append(f'GF/м {x}')
        if ga_a is not None:
            x = _fmt_num(ga_a)
            if x: sub.append(f'GA/м {x}')
        if sub:
            atk_parts.append(f'{getattr(bet, "away_team", "Гости")}: ' + ', '.join(sub))
    if atk_parts:
        lines.append('Командный профиль: ' + ' | '.join(atk_parts) + '.')

    # Weather / injuries / context source
    condition = _ctx_lookup(bet, 'weather_condition')
    temp = _ctx_lookup(bet, 'weather_temp_c')
    wind = _ctx_lookup(bet, 'weather_wind_kph')
    precip = _ctx_lookup(bet, 'weather_precip_mm')
    weather_bits = []
    if condition:
        weather_bits.append(str(condition))
    if temp not in (None, ''):
        weather_bits.append(f'{_smart_float(temp):.1f}°C')
    if wind not in (None, ''):
        weather_bits.append(f'ветер {_smart_float(wind):.1f} км/ч')
    if precip not in (None, '') and _smart_float(precip) and _smart_float(precip) > 0:
        weather_bits.append(f'осадки {_smart_float(precip):.1f} мм')
    if weather_bits:
        lines.append('Погода/условия: ' + ', '.join(weather_bits) + '.')

    context_source = _ctx_lookup(bet, 'context_source') or getattr(bet, 'source_summary', {}).get('context_source') if isinstance(getattr(bet, 'source_summary', None), dict) else None
    context_conf = _ctx_lookup(bet, 'context_confidence')
    quality = _ctx_lookup(bet, 'quality_score')
    source_bits = []
    if context_source:
        source_bits.append(f'контекст {context_source}')
    if context_conf is not None:
        x = _fmt_num(context_conf, 1)
        if x:
            source_bits.append(f'context confidence {x}')
    if quality is not None:
        x = _fmt_num(quality, 1)
        if x:
            source_bits.append(f'quality {x}')
    if source_bits:
        lines.append('Источник сигнала: ' + ', '.join(source_bits) + '.')

    return lines


def _detailed_explanation(publisher: Any, bet: Any, selection_text: str, original_expl) -> str:
    text = original_expl(publisher, bet, selection_text)
    paragraphs = [p.strip() for p in str(text or '').split('\n\n') if p.strip()]

    xg = _xg_comment(bet)
    if xg and all(xg not in p for p in paragraphs):
        paragraphs.append(xg)

    for line in _detail_lines(bet):
        if line and all(line not in p for p in paragraphs):
            paragraphs.append(line)

    # Let the explanation be richer than the default 3 blocks.
    return '\n\n'.join(paragraphs[:7])


def _patch_api_football() -> None:
    from app.providers.api_football import ApiFootballContextProvider
    if not getattr(ApiFootballContextProvider, '_runtime_provider_docs_fix_applied', False):
        ApiFootballContextProvider._to_float = staticmethod(_smart_float)
        ApiFootballContextProvider._to_unit_percent = staticmethod(_smart_unit_percent)
        ApiFootballContextProvider._runtime_provider_docs_fix_applied = True


def _patch_weather() -> None:
    from app.providers.weather_common import WeatherContextEnricher
    if not getattr(WeatherContextEnricher, '_runtime_provider_docs_fix_applied', False):
        WeatherContextEnricher._to_float = staticmethod(_smart_float)
        WeatherContextEnricher._runtime_provider_docs_fix_applied = True


def _patch_oddspapi() -> None:
    from app.providers.oddspapi import OddsPapiProvider
    if getattr(OddsPapiProvider, '_runtime_provider_docs_fix_applied', False):
        return

    def patched_retry_delay_seconds(response):
        raw_values = [response.headers.get('Retry-After')]
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            error = payload.get('error')
            if isinstance(error, dict):
                raw_values.append(error.get('retryAfter'))
                retry_ms = error.get('retryMs')
                if retry_ms not in (None, ''):
                    number = _smart_float(retry_ms)
                    if number is not None:
                        return max(1.0, min(number / 1000.0, 30.0))
        for raw in raw_values:
            number = _smart_float(raw)
            if number is not None:
                return max(1.0, min(number, 30.0))
        # Docs say endpoint cooldown is 1000ms; use a sane 1s default instead of multi-hour hard fallback.
        return 1.0

    def patched_activate_rate_limit_cooldown(self, response):
        retry_seconds = float(patched_retry_delay_seconds(response))
        # Keep the provider out only briefly; current implementation over-penalizes with many hours.
        floor_minutes = max(1, int(getattr(self.settings, 'oddspapi_rate_limit_cooldown_minutes', 20) or 20))
        until = _now_utc() + timedelta(minutes=floor_minutes)
        if retry_seconds > 60.0:
            until = _now_utc() + timedelta(seconds=retry_seconds)
        path = self._cooldown_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        details = ''
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            error = payload.get('error')
            if isinstance(error, dict):
                details = str(error.get('details') or error.get('message') or '')
        _write_json(path, {
            'cooldown_until': until.isoformat(),
            'created_at': _now_utc().isoformat(),
            'details': details[:400],
        })

    OddsPapiProvider._retry_delay_seconds = staticmethod(patched_retry_delay_seconds)
    OddsPapiProvider._activate_rate_limit_cooldown = patched_activate_rate_limit_cooldown
    OddsPapiProvider._runtime_provider_docs_fix_applied = True


def _patch_zero_yield_providers() -> None:
    from app.providers.futrixmetrics import FutrixMetricsContextProvider
    from app.providers.bzzoiro import BzzoiroContextProvider
    _wrap_zero_yield_provider(FutrixMetricsContextProvider, 'futrixmetrics', cooldown_hours=8, zero_threshold=3)
    _wrap_zero_yield_provider(BzzoiroContextProvider, 'bzzoiro', cooldown_hours=6, zero_threshold=3)


def _patch_telegram() -> None:
    from app.services.telegram import TelegramPublisher
    if getattr(TelegramPublisher, '_runtime_provider_docs_fix_applied', False):
        return
    original_expl = TelegramPublisher._build_explanation

    def patched_build_explanation(self, bet, selection_text):
        return _detailed_explanation(self, bet, selection_text, original_expl)

    TelegramPublisher._build_explanation = patched_build_explanation
    TelegramPublisher._runtime_provider_docs_fix_applied = True


def _apply() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    try:
        _patch_api_football()
        _patch_weather()
        _patch_oddspapi()
        _patch_zero_yield_providers()
        _patch_telegram()
        _PATCH_APPLIED = True
    except Exception:
        # Never kill the bot at import time.
        return


_apply()
