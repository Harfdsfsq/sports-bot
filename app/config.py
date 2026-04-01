from functools import lru_cache
from typing import List

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'sports-value-bot'
    app_env: str = 'development'
    app_timezone: str = 'Europe/Moscow'
    state_path: str = Field('.data/state.json', alias='STATE_PATH')

    telegram_bot_token: str | None = None
    telegram_chat_id: str | None = None

    the_odds_api_key: str | None = None
    odds_api_io_key: str | None = None
    sstats_api_key: str | None = None

    run_sports: List[str] = ['soccer', 'basketball', 'icehockey', 'baseball']
    run_days_ahead: int = 3
    max_matches_for_pricing: int = 200

    min_books: int = 3
    min_sources: int = 2
    min_edge_pct: float = 2.5
    min_ev_pct: float = 1.75
    max_picks_per_run: int = 5
    odds_min: float = 1.70
    odds_max: float = 3.00
    allow_low_tier: bool = False
    publish_dry_run: bool = True

    @field_validator('run_sports', mode='before')
    @classmethod
    def split_csv(cls, value):
        if isinstance(value, str):
            return [item.strip() for item in value.split(',') if item.strip()]
        return value


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
