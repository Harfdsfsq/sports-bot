from __future__ import annotations

from functools import cached_property, lru_cache
from typing import Any
from zoneinfo import ZoneInfo

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
        populate_by_name=True,
    )

    app_name: str = 'sports-value-bot'
    app_env: str = Field('development', alias='APP_ENV')
    app_timezone: str = Field('Europe/Moscow', alias='APP_TIMEZONE')

    state_path: str = Field('.data/state.json', alias='STATE_PATH')
    debug_path: str = Field('.data/debug-last-run.json', alias='DEBUG_PATH')
    storage_export_dir: str = Field('.data/exports', alias='STORAGE_EXPORT_DIR')

    publish_dry_run: bool = Field(True, alias='PUBLISH_DRY_RUN')
    run_sports: list[str] = Field(default_factory=lambda: ['soccer'], alias='RUN_SPORTS')
    run_days_ahead: int = Field(4, alias='RUN_DAYS_AHEAD')
    publish_window_hours: int = Field(48, alias='PUBLISH_WINDOW_HOURS')
    min_kickoff_lead_minutes: int = Field(30, alias='MIN_KICKOFF_LEAD_MINUTES')
    allow_low_tier: bool = Field(False, alias='ALLOW_LOW_TIER')

    target_bookmakers: list[str] = Field(default_factory=lambda: ['Bet365', 'Unibet'], alias='TARGET_BOOKMAKERS')
    consensus_bookmakers: list[str] = Field(default_factory=lambda: ['Bet365', 'Unibet'], alias='CONSENSUS_BOOKMAKERS')
    odds_api_io_bookmakers: list[str] = Field(default_factory=lambda: ['Bet365', 'Unibet'], alias='ODDS_API_IO_BOOKMAKERS')

    min_books_publish: int = Field(2, alias='MIN_BOOKS_PUBLISH')
    min_sources_publish: int = Field(1, alias='MIN_SOURCES_PUBLISH')
    min_model_confidence: float = Field(0.55, alias='MIN_MODEL_CONFIDENCE')
    max_matches_for_odds_fetch: int = Field(300, alias='MAX_MATCHES_FOR_ODDS_FETCH')
    max_matches_for_pricing: int = Field(300, alias='MAX_MATCHES_FOR_PRICING')

    min_books: int = 2
    min_sources: int = 1
    min_edge_pct: float = 2.5
    min_ev_pct: float = 1.75
    max_picks_per_run: int = 5
    odds_min: float = 1.70
    odds_max: float = 3.00

    telegram_token: str | None = Field(None, alias='TELEGRAM_TOKEN')
    telegram_chat_id: str | None = Field(None, alias='TELEGRAM_CHAT_ID')

    odds_api_io_key: str | None = Field(None, alias='ODDS_API_IO_KEY')
    sstats_api_key: str | None = Field(None, alias='SSTATS_API_KEY')
    api_football_key: str | None = Field(None, alias='API_FOOTBALL_KEY')
    api_football_predictions_limit: int = Field(4, alias='API_FOOTBALL_PREDICTIONS_LIMIT')
    bzzoiro_api_key: str | None = Field(None, alias='BZZOIRO_API_KEY')

    bookies_api_enabled: bool = Field(True, alias='BOOKIES_API_ENABLED')
    bookies_api_base_url: str | None = Field(None, alias='BOOKIES_API_BASE_URL')
    bookies_api_login: str | None = Field(None, alias='BOOKIES_API_LOGIN')
    bookies_api_key: str | None = Field(None, alias='BOOKIES_API_KEY')
    bookies_api_token: str | None = Field(None, alias='BOOKIES_API_TOKEN')
    bookies_api_use_for_backfill_only: bool = Field(False, alias='BOOKIES_API_USE_FOR_BACKFILL_ONLY')
    bookies_api_odds_task: str = Field('odds', alias='BOOKIES_API_ODDS_TASK')
    bookies_api_odds_fetch_limit: int = Field(40, alias='BOOKIES_API_ODDS_FETCH_LIMIT')
    bookies_api_timeout_seconds: float = Field(30.0, alias='BOOKIES_API_TIMEOUT_SECONDS')
    bookies_api_max_pages_per_day: int = Field(40, alias='BOOKIES_API_MAX_PAGES_PER_DAY')
    bookies_api_sports: list[str] = Field(default_factory=lambda: ['soccer'], alias='BOOKIES_API_SPORTS')

    sheet_id: str | None = Field(None, alias='SHEET_ID')
    google_sheets_webhook_url: str | None = Field(None, alias='GOOGLE_SHEETS_WEBHOOK_URL')
    google_sheets_webhook_token: str | None = Field(None, alias='GOOGLE_SHEETS_WEBHOOK_TOKEN')

    @field_validator(
        'run_sports',
        'target_bookmakers',
        'consensus_bookmakers',
        'odds_api_io_bookmakers',
        'bookies_api_sports',
        mode='before',
    )
    @classmethod
    def _split_csv(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            text = value.strip()
            if not text:
                return []
            if text.startswith('['):
                return value
            return [item.strip() for item in text.split(',') if item.strip()]
        return value

    @field_validator('run_sports', mode='after')
    @classmethod
    def _default_run_sports(cls, value: list[str]) -> list[str]:
        return value or ['soccer']

    @field_validator('bookies_api_sports', mode='after')
    @classmethod
    def _default_bookies_sports(cls, value: list[str]) -> list[str]:
        return value or ['soccer']

    @cached_property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)

    @property
    def telegram_bot_token(self) -> str | None:
        return self.telegram_token


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
