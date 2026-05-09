from __future__ import annotations

import os
import re
from datetime import timezone
from typing import Any

from app.schemas import MatchContext

_INSTALLED = False
_DATE_RE = re.compile(r'^\d{4}-\d{2}-\d{2}$')
UTC = timezone.utc


def _ensure_stats(stats: Any) -> dict[str, Any]:
    return stats if isinstance(stats, dict) else {}


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else '').strip().lower()
    if not raw:
        return default
    return raw in {'1', 'true', 'yes', 'on', 'force'}


def _looks_like_date(value: Any) -> bool:
    return bool(_DATE_RE.match(str(value or '').strip()))


def _extract_rows(payload: Any, rows_fn: Any = None) -> list[dict[str, Any]]:
    if callable(rows_fn):
        try:
            rows = rows_fn(payload)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]
        except Exception:
            pass
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ('results', 'data', 'predictions', 'items', 'events'):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    return []


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        return float(str(value).strip().replace(',', '.'))
    except Exception:
        return None


def _apply_provider_shims(BzzoiroContextProvider: type, *, forced_v2: bool) -> None:
    if not getattr(BzzoiroContextProvider, '_harizon_url_alias_patch', False):
        original_init = BzzoiroContextProvider.__init__

        def patched_init(self, *args, **kwargs):  # type: ignore[no-untyped-def]
            original_init(self, *args, **kwargs)
            api_root = str(getattr(self, 'api_root_url', '') or '').rstrip('/')
            base_url = str(getattr(self, 'base_url', '') or api_root or 'https://sports.bzzoiro.com/api').rstrip('/')
            self.base_url = base_url
            self.url = api_root or base_url
            self.endpoint_url = api_root or base_url
            if not hasattr(self, 'max_http_requests'):
                try:
                    self.max_http_requests = max(0, int(float(
                        os.getenv('BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN')
                        or os.getenv('BZZOIRO_REQUEST_BUDGET_GRANTED')
                        or os.getenv('BZZOIRO_PER_RUN_MAX')
                        or getattr(self, 'max_pages', 1)
                        or 1
                    )))
                except Exception:
                    self.max_http_requests = 1
            if not hasattr(self, '_requests_used'):
                self._requests_used = 0

        BzzoiroContextProvider.__init__ = patched_init
        BzzoiroContextProvider._harizon_url_alias_patch = True

    # Always override _fetch_rows. Older runs proved that a previous loose alias
    # can interpret SStats-style date arguments as URL paths, causing calls like
    # https://sports.bzzoiro.com/api/2026-03-25. This wrapper explicitly detects
    # _fetch_rows(client, from_date, to_date, stats) and maps it to /predictions/.
    async def _fetch_rows(self, client, path='/predictions/', *args, **kwargs):  # type: ignore[no-untyped-def]
        headers = kwargs.pop('headers', None) or {}
        params = kwargs.pop('params', None) or {}
        stats = _ensure_stats(kwargs.pop('stats', None))

        # SStats-compatible legacy call shape:
        #   _fetch_rows(client, from_date, to_date, stats)
        if _looks_like_date(path):
            date_from = str(path).strip()
            date_to = str(args[0]).strip() if args and _looks_like_date(args[0]) else date_from
            if len(args) > 1 and isinstance(args[1], dict) and not stats:
                stats = args[1]
            params = {
                'date_from': date_from,
                'date_to': date_to,
                'upcoming': 'true',
                'tz': 'UTC',
                'page': params.get('page', 1) if isinstance(params, dict) else 1,
            }
            path_text = '/predictions/'
        else:
            if args:
                if isinstance(args[0], dict) and not params:
                    params = args[0]
                if len(args) > 1 and isinstance(args[1], dict) and not stats:
                    stats = args[1]
            path_text = str(path or '/predictions/')
            for prefix in (str(getattr(self, 'api_root_url', '') or '').rstrip('/'), str(getattr(self, 'base_url', '') or '').rstrip('/')):
                if prefix and path_text.startswith(prefix):
                    path_text = path_text.replace(prefix, '', 1) or '/predictions/'
            if not path_text.startswith('/'):
                path_text = '/' + path_text
            if path_text in {'/', '/events', '/events/'}:
                path_text = '/predictions/'

        if not headers:
            api_key = getattr(self, 'api_key', None) or os.getenv('BZZOIRO_API_KEY')
            if api_key:
                headers = {'Authorization': f'Token {api_key}'}

        fetch_paginated = getattr(self, '_fetch_paginated_rows', None)
        if callable(fetch_paginated):
            return await fetch_paginated(client, path_text, headers=headers, params=params, stats=stats)

        get_json = getattr(self, '_get_json', None)
        rows_fn = getattr(self, '_rows', None)
        if callable(get_json):
            payload = await get_json(client, path_text, headers, params, stats)
            return _extract_rows(payload, rows_fn)
        return []

    BzzoiroContextProvider._fetch_rows = _fetch_rows
    BzzoiroContextProvider._harizon_fetch_rows_alias_patch = True

    # SStats-style helpers expected by older generic enrichment/matching layers.
    # They delegate to the predictions-v2 provider when available and otherwise
    # parse the common Bzzoiro prediction/event payload shapes directly.
    if not hasattr(BzzoiroContextProvider, '_extract_team_name'):
        def _extract_team_name(self, row, side):  # type: ignore[no-untyped-def]
            side_text = str(side or '').strip().lower()
            try:
                teams_fn = getattr(self, '_prediction_teams', None)
                if callable(teams_fn):
                    home, away = teams_fn(row if isinstance(row, dict) else {})
                    return home if side_text == 'home' else away
            except Exception:
                pass
            data = row if isinstance(row, dict) else {}
            event = data.get('event') if isinstance(data.get('event'), dict) else data
            keys = [f'{side_text}_team', f'{side_text}Team', side_text, 'home' if side_text == 'home' else 'away']
            for key in keys:
                value = event.get(key) if isinstance(event, dict) else None
                if isinstance(value, dict):
                    value = value.get('name') or value.get('Name') or value.get('short_name') or value.get('shortName')
                if isinstance(value, str) and value.strip():
                    return value.strip()
            obj = event.get(f'{side_text}_team_obj') if isinstance(event, dict) else None
            if isinstance(obj, dict):
                for key in ('name', 'Name', 'short_name', 'shortName', 'display_name', 'displayName'):
                    value = obj.get(key)
                    if isinstance(value, str) and value.strip():
                        return value.strip()
            return ''
        BzzoiroContextProvider._extract_team_name = _extract_team_name
        BzzoiroContextProvider._harizon_extract_team_name_patch = True

    if not hasattr(BzzoiroContextProvider, '_extract_league_name'):
        def _extract_league_name(self, row):  # type: ignore[no-untyped-def]
            try:
                league_fn = getattr(self, '_prediction_league', None)
                if callable(league_fn):
                    value = league_fn(row if isinstance(row, dict) else {})
                    if value:
                        return str(value)
            except Exception:
                pass
            data = row if isinstance(row, dict) else {}
            event = data.get('event') if isinstance(data.get('event'), dict) else data
            league = event.get('league') if isinstance(event, dict) else None
            if isinstance(league, dict):
                return str(league.get('name') or league.get('Name') or league.get('league_name') or league.get('displayName') or '')
            return str((event.get('league_name') or event.get('leagueName') or data.get('league_name') or data.get('leagueName') or league or '') if isinstance(event, dict) else '')
        BzzoiroContextProvider._extract_league_name = _extract_league_name
        BzzoiroContextProvider._harizon_extract_league_name_patch = True

    if not hasattr(BzzoiroContextProvider, '_extract_start'):
        def _extract_start(self, row):  # type: ignore[no-untyped-def]
            try:
                start_fn = getattr(self, '_prediction_start', None)
                if callable(start_fn):
                    value = start_fn(row if isinstance(row, dict) else {})
                    if value is not None:
                        return value
            except Exception:
                pass
            try:
                from app.utils import parse_datetime
                data = row if isinstance(row, dict) else {}
                event = data.get('event') if isinstance(data.get('event'), dict) else data
                for key in ('event_date', 'date', 'start', 'startTime', 'commence_time', 'kickoff'):
                    value = event.get(key, data.get(key)) if isinstance(event, dict) else data.get(key)
                    if value not in (None, ''):
                        dt = parse_datetime(value)
                        if getattr(dt, 'tzinfo', None) is None:
                            dt = dt.replace(tzinfo=UTC)
                        return dt.astimezone(UTC)
            except Exception:
                return None
            return None
        BzzoiroContextProvider._extract_start = _extract_start
        BzzoiroContextProvider._harizon_extract_start_patch = True

    if not hasattr(BzzoiroContextProvider, '_row_to_context'):
        def _row_to_context(self, row):  # type: ignore[no-untyped-def]
            pred_to_context = getattr(self, '_prediction_to_context', None)
            if callable(pred_to_context):
                context = pred_to_context(row if isinstance(row, dict) else {}, 'loose')
                if context is not None:
                    return context
            data = row if isinstance(row, dict) else {}
            expected_home = _to_float(data.get('expected_home_goals') or data.get('expected_home') or data.get('home_xg') or data.get('homeXg'))
            expected_away = _to_float(data.get('expected_away_goals') or data.get('expected_away') or data.get('away_xg') or data.get('awayXg'))
            if expected_home is None and expected_away is None:
                return MatchContext(source='bzzoiro_predictions_v2', payload=data, confidence=56.0, details={'bzzoiro_provider_mode': 'compat_row_empty'})
            return MatchContext(
                source='bzzoiro_predictions_v2',
                payload=data,
                expected_home=expected_home,
                expected_away=expected_away,
                confidence=60.0,
                details={'bzzoiro_provider_mode': 'compat_row_to_context'},
            )
        BzzoiroContextProvider._row_to_context = _row_to_context
        BzzoiroContextProvider._harizon_row_to_context_patch = True

    if not hasattr(BzzoiroContextProvider, '_build_team_form_contexts'):
        def _build_team_form_contexts(self, matches=None, rows=None, preview=None):  # type: ignore[no-untyped-def]
            if isinstance(preview, dict):
                preview.setdefault('bzzoiro_team_form_compat', 'no_op_current_event_provider')
            return {}

        BzzoiroContextProvider._build_team_form_contexts = _build_team_form_contexts
        BzzoiroContextProvider._harizon_team_form_noop_patch = True

    if not hasattr(BzzoiroContextProvider, '_fetch_bzzoiro_contexts'):
        async def _fetch_bzzoiro_contexts(self, client=None, matches=None, *args, **kwargs):  # type: ignore[no-untyped-def]
            return {}, {
                'enabled': bool(getattr(self, 'api_key', None)),
                'compat_noop': True,
                'reason': 'real_bzzoiro_contexts_are_built_by_fetch_context',
                'requests': 0,
                'contexts_built': 0,
                'events_fetched': 0,
                'response_errors': 0,
            }, []

        BzzoiroContextProvider._fetch_bzzoiro_contexts = _fetch_bzzoiro_contexts
        BzzoiroContextProvider._harizon_nested_context_alias_patch = True


def install() -> dict[str, Any]:
    """Apply compatibility fixes for Bzzoiro provider runtime.

    The runner imports Bzzoiro through ``app.providers.bzzoiro``. Runtime patches
    may also call provider-private helpers with SStats-style signatures. This
    shim forces the public provider symbol to predictions-v2 and makes legacy
    helper calls route to ``/api/predictions/`` instead of constructing invalid
    date paths like ``/api/2026-03-25``.
    """
    global _INSTALLED
    if _INSTALLED:
        return {'status': 'already_installed'}
    forced_v2 = _truthy(os.getenv('BZZOIRO_FORCE_PREDICTIONS_V2'), True)
    try:
        import app.providers.bzzoiro as bzzoiro_module
        from app.providers.bzzoiro import BzzoiroContextProvider as CurrentProvider
    except Exception as exc:
        return {'status': 'import_error', 'error': f'{type(exc).__name__}: {exc}'}

    patched_modules: list[str] = []
    _apply_provider_shims(CurrentProvider, forced_v2=forced_v2)
    patched_modules.append(getattr(CurrentProvider, '__module__', 'current'))

    BzzoiroContextProvider = CurrentProvider
    if forced_v2:
        try:
            from app.providers.bzzoiro_predictions_v2 import BzzoiroContextProvider as PredictionsV2Provider
            _apply_provider_shims(PredictionsV2Provider, forced_v2=True)
            bzzoiro_module.BzzoiroContextProvider = PredictionsV2Provider
            BzzoiroContextProvider = PredictionsV2Provider
            patched_modules.append(getattr(PredictionsV2Provider, '__module__', 'predictions_v2'))
        except Exception as exc:
            return {'status': 'v2_import_error', 'error': f'{type(exc).__name__}: {exc}'}

    _INSTALLED = True
    return {
        'status': 'installed',
        'forced_predictions_v2': forced_v2,
        'provider_class_module': getattr(BzzoiroContextProvider, '__module__', ''),
        'patched_modules': sorted(set(patched_modules)),
        'patches': [
            'bzzoiro_symbol_predictions_v2' if forced_v2 else 'bzzoiro_symbol_current',
            'bzzoiro_date_signature_fetch_rows_to_predictions',
            'bzzoiro_sstats_style_helper_shims',
            'bzzoiro_url_alias',
            'bzzoiro_team_form_noop',
            'bzzoiro_nested_context_noop',
        ],
    }
