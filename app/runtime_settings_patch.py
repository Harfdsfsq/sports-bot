from __future__ import annotations

import os
from typing import Any


def _env_bool(name: str, default: bool | None = None) -> bool | None:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == '':
        return default
    return str(raw).strip().lower() in {'1', 'true', 'yes', 'on'}


def _cap_setting(settings: Any, attr_name: str, cap_value: int | float, *, env_name: str | None = None) -> None:
    env_key = env_name or attr_name.upper()
    if os.getenv(env_key) not in (None, ''):
        return
    current = getattr(settings, attr_name, None)
    if current in (None, ''):
        return
    try:
        current_number = float(current)
        cap_number = float(cap_value)
    except Exception:
        return
    if current_number > cap_number:
        casted: Any = int(cap_number) if isinstance(current, int) and float(cap_number).is_integer() else cap_number
        object.__setattr__(settings, attr_name, casted)


def apply_api_runtime_overrides(settings: Any) -> Any:
    """
    Runtime-only quota and feature toggles.

    This keeps the main Settings class untouched while still allowing
    additional API integrations and conservative free-tier defaults.
    """
    # Auto-enable dormant providers when keys are present unless explicitly disabled.
    auto_flags: list[tuple[str, str]] = [
        ('FUTRIXMETRICS_API_KEY', 'enable_futrixmetrics_context'),
        ('ODDSPAPI_API_KEY', 'enable_oddspapi'),
        ('ALLSPORTSAPI_API_KEY', 'enable_allsportsapi'),
    ]
    for env_key, attr_name in auto_flags:
        if not os.getenv(env_key):
            continue
        explicit_flag = _env_bool(attr_name.upper(), None)
        if explicit_flag is False:
            continue
        try:
            current_value = bool(getattr(settings, attr_name))
        except Exception:
            current_value = False
        if not current_value:
            try:
                object.__setattr__(settings, attr_name, True)
            except Exception:
                pass

    # Conservative free-tier caps unless the operator explicitly overrides them.
    _cap_setting(settings, 'newsapi_match_limit', 4, env_name='NEWSAPI_MATCH_LIMIT')
    _cap_setting(settings, 'newsapi_articles_per_match', 4, env_name='NEWSAPI_ARTICLES_PER_MATCH')
    _cap_setting(settings, 'newsapi_lookback_hours', 48, env_name='NEWSAPI_LOOKBACK_HOURS')

    _cap_setting(settings, 'gnews_match_limit', 3, env_name='GNEWS_MATCH_LIMIT')
    _cap_setting(settings, 'gnews_articles_per_match', 4, env_name='GNEWS_ARTICLES_PER_MATCH')
    _cap_setting(settings, 'gnews_lookback_hours', 48, env_name='GNEWS_LOOKBACK_HOURS')

    _cap_setting(settings, 'api_football_context_match_limit', 10, env_name='API_FOOTBALL_CONTEXT_MATCH_LIMIT')
    _cap_setting(settings, 'api_football_predictions_limit', 8, env_name='API_FOOTBALL_PREDICTIONS_LIMIT')

    _cap_setting(settings, 'oddspapi_match_limit', 8, env_name='ODDSPAPI_MATCH_LIMIT')
    _cap_setting(settings, 'oddspapi_tournament_limit', 2, env_name='ODDSPAPI_TOURNAMENT_LIMIT')
    _cap_setting(settings, 'oddspapi_min_fetch_interval_minutes', 480, env_name='ODDSPAPI_MIN_FETCH_INTERVAL_MINUTES')

    _cap_setting(settings, 'allsportsapi_match_limit', 8, env_name='ALLSPORTSAPI_MATCH_LIMIT')
    _cap_setting(settings, 'allsportsapi_min_fetch_interval_minutes', 240, env_name='ALLSPORTSAPI_MIN_FETCH_INTERVAL_MINUTES')

    _cap_setting(settings, 'futrixmetrics_context_match_limit', 4, env_name='FUTRIXMETRICS_CONTEXT_MATCH_LIMIT')
    _cap_setting(settings, 'futrixmetrics_limit_per_team', 50, env_name='FUTRIXMETRICS_LIMIT_PER_TEAM')

    return settings
