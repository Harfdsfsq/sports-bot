from __future__ import annotations

from functools import lru_cache
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _split_csv(value: Any) -> list[str] | Any:
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return value


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "sports-value-bot"
    app_env: str = Field("development", validation_alias=AliasChoices("APP_ENV"))
    app_timezone: str = Field(
        "Europe/Moscow",
        validation_alias=AliasChoices("APP_TIMEZONE", "TZ"),
    )

    state_path: str = Field(
        ".data/state.json",
        validation_alias=AliasChoices("STATE_PATH"),
    )
    debug_path: str = Field(
        ".data/debug-last-run.json",
        validation_alias=AliasChoices("DEBUG_PATH"),
    )
    storage_export_dir: str = Field(
        ".data/exports",
        validation_alias=AliasChoices("STORAGE_EXPORT_DIR"),
    )

    telegram_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_TOKEN", "TELEGRAM_BOT_TOKEN"),
    )
    telegram_chat_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_CHAT_ID"),
    )

    odds_api_io_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ODDS_API_IO_KEY"),
    )
    sstats_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SSTATS_API_KEY"),
    )
    api_football_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("API_FOOTBALL_KEY"),
    )
    bzzoiro_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BZZOIRO_API_KEY"),
    )

    run_sports: list[str] = Field(
        default_factory=lambda: ["soccer"],
        validation_alias=AliasChoices("RUN_SPORTS"),
    )
    run_days_ahead: int = Field(4, validation_alias=AliasChoices("RUN_DAYS_AHEAD"))
    publish_window_hours: int = Field(
        48,
        validation_alias=AliasChoices("PUBLISH_WINDOW_HOURS"),
    )
    min_kickoff_lead_minutes: int = Field(
        30,
        validation_alias=AliasChoices("MIN_KICKOFF_LEAD_MINUTES"),
    )

    target_bookmakers: list[str] = Field(
        default_factory=lambda: ["Bet365", "Unibet"],
        validation_alias=AliasChoices("TARGET_BOOKMAKERS"),
    )
    consensus_bookmakers: list[str] = Field(
        default_factory=lambda: ["Bet365", "Unibet"],
        validation_alias=AliasChoices("CONSENSUS_BOOKMAKERS"),
    )

    min_books_publish: int = Field(
        3,
        validation_alias=AliasChoices("MIN_BOOKS_PUBLISH", "MIN_BOOKS"),
    )
    min_sources_publish: int = Field(
        1,
        validation_alias=AliasChoices("MIN_SOURCES_PUBLISH", "MIN_SOURCES"),
    )
    min_model_confidence: float = Field(
        58.0,
        validation_alias=AliasChoices("MIN_MODEL_CONFIDENCE"),
    )
    max_matches_for_odds_fetch: int = Field(
        300,
        validation_alias=AliasChoices("MAX_MATCHES_FOR_ODDS_FETCH", "MAX_MATCHES_FOR_PRICING"),
    )

    min_edge_pct: float = Field(2.5, validation_alias=AliasChoices("MIN_EDGE_PCT"))
    min_ev_pct: float = Field(1.75, validation_alias=AliasChoices("MIN_EV_PCT"))
    max_picks_per_run: int = Field(5, validation_alias=AliasChoices("MAX_PICKS_PER_RUN"))
    odds_min: float = Field(1.70, validation_alias=AliasChoices("ODDS_MIN"))
    odds_max: float = Field(3.00, validation_alias=AliasChoices("ODDS_MAX"))

    allow_low_tier: bool = Field(
        False,
        validation_alias=AliasChoices("ALLOW_LOW_TIER"),
    )
    publish_dry_run: bool = Field(
        True,
        validation_alias=AliasChoices("PUBLISH_DRY_RUN"),
    )

    bookies_api_enabled: bool = Field(
        True,
        validation_alias=AliasChoices("BOOKIES_API_ENABLED"),
    )
    bookies_api_login: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BOOKIES_API_LOGIN"),
    )
    bookies_api_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BOOKIES_API_TOKEN"),
    )
    bookies_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BOOKIES_API_KEY"),
    )
    bookies_api_base_url: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BOOKIES_API_BASE_URL"),
    )
    bookies_api_odds_task: str = Field(
        "odds",
        validation_alias=AliasChoices("BOOKIES_API_ODDS_TASK"),
    )
    bookies_api_odds_fetch_limit: int = Field(
        40,
        validation_alias=AliasChoices("BOOKIES_API_ODDS_FETCH_LIMIT"),
    )
    bookies_api_use_for_backfill_only: bool = Field(
        False,
        validation_alias=AliasChoices("BOOKIES_API_USE_FOR_BACKFILL_ONLY"),
    )

    api_football_predictions_limit: int = Field(
        4,
        validation_alias=AliasChoices("API_FOOTBALL_PREDICTIONS_LIMIT"),
    )

    @field_validator("run_sports", "target_bookmakers", "consensus_bookmakers", mode="before")
    @classmethod
    def split_csv(cls, value: Any) -> Any:
        return _split_csv(value)

    @field_validator("app_timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"Unknown timezone: {value}") from exc
        return value

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)

    @property
    def max_matches_for_pricing(self) -> int:
        return self.max_matches_for_odds_fetch

    @property
    def min_books(self) -> int:
        return self.min_books_publish

    @property
    def min_sources(self) -> int:
        return self.min_sources_publish


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
