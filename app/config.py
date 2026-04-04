from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        case_sensitive=False,
        extra='ignore',
    )

    app_name: str = 'sports-value-bot'
    app_env: str = 'production'
    app_timezone: str = 'Europe/Moscow'

    state_path: str = '.data/state.json'
    debug_path: str = '.data/debug-last-run.json'
    storage_export_dir: str = '.data/exports'

    publish_dry_run: bool = False

    run_sports: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ['soccer'])
    run_days_ahead: int = 4
    publish_window_hours: int = 48
    min_kickoff_lead_minutes: int = 30

    target_bookmakers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ['Bet365', 'Unibet']
    )
    consensus_bookmakers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ['Bet365', 'Unibet']
    )
    odds_api_io_bookmakers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ['Bet365', 'Unibet']
    )

    min_books_publish: int = 2
    min_sources_publish: int = 1
    min_model_confidence: float = 0.55

    min_books: int = 2
    min_sources: int = 1
    min_edge_pct: float = 2.5
    min_ev_pct: float = 1.75
    max_picks_per_run: int = 5
    odds_min: float = 1.70
    odds_max: float = 3.50

    max_matches_for_odds_fetch: int = 300
    max_matches_for_pricing: int = 300

    allow_low_tier: bool = False

    telegram_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices('TELEGRAM_TOKEN', 'TELEGRAM_BOT_TOKEN'),
    )
    telegram_chat_id: str | None = None

    # External providers
    the_odds_api_key: str | None = None
    the_odds_api_enabled: bool = False
    the_odds_api_timeout_seconds: float = 30.0

    odds_api_io_key: str | None = None
    odds_api_io_timeout_seconds: float = 30.0

    sstats_api_key: str | None = None
    sstats_timeout_seconds: float = 30.0
    enable_sstats_context: bool = True
    match_start_tolerance_hours: float = 3.0
    fallback_match_start_tolerance_hours: float = 12.0

    api_football_key: str | None = None
    api_football_predictions_limit: int = 4

    bzzoiro_api_key: str | None = None

    bookies_api_enabled: bool = True
    bookies_api_base_url: str | None = None
    bookies_api_login: str | None = None
    bookies_api_key: str | None = None
    bookies_api_token: str | None = None
    bookies_api_use_for_backfill_only: bool = False
    bookies_api_odds_task: str = 'odds'
    bookies_api_odds_fetch_limit: int = 40
    bookies_api_timeout_seconds: float = 30.0

    @field_validator(
        'run_sports',
        'target_bookmakers',
        'consensus_bookmakers',
        'odds_api_io_bookmakers',
        mode='before',
    )
    @classmethod
    def parse_listish(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []
            if raw.startswith('[') and raw.endswith(']'):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return [str(item).strip() for item in parsed if str(item).strip()]
                except json.JSONDecodeError:
                    pass
            return [item.strip() for item in raw.split(',') if item.strip()]
        return [str(value).strip()]

    @field_validator(
        'telegram_token',
        'telegram_chat_id',
        'the_odds_api_key',
        'odds_api_io_key',
        'sstats_api_key',
        'api_football_key',
        'bzzoiro_api_key',
        'bookies_api_base_url',
        'bookies_api_login',
        'bookies_api_key',
        'bookies_api_token',
        mode='before',
    )
    @classmethod
    def empty_string_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def tzinfo(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.app_timezone)
        except Exception:
            return ZoneInfo('UTC')

    @property
    def telegram_bot_token(self) -> str | None:
        return self.telegram_token

    @property
    def bookies_api_sports(self) -> list[str]:
        return self.run_sports

    @property
    def target_books(self) -> list[str]:
        return self.target_bookmakers

    @property
    def consensus_books(self) -> list[str]:
        return self.consensus_bookmakers


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
