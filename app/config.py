from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = Field(default="sports-value-bot")
    app_env: str = Field(default="production", validation_alias=AliasChoices("APP_ENV", "ENV"))
    app_timezone: str = Field(
        default="Europe/Moscow",
        validation_alias=AliasChoices("APP_TIMEZONE", "TIMEZONE", "TZ"),
    )

    state_path: str = Field(default=".data/state.json", validation_alias=AliasChoices("STATE_PATH"))
    debug_path: str = Field(default=".data/debug-last-run.json", validation_alias=AliasChoices("DEBUG_PATH"))
    storage_export_dir: str = Field(
        default=".data/exports",
        validation_alias=AliasChoices("STORAGE_EXPORT_DIR"),
    )

    telegram_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_TOKEN", "TELEGRAM_BOT_TOKEN"),
    )
    telegram_chat_id: str | None = Field(default=None, validation_alias=AliasChoices("TELEGRAM_CHAT_ID"))

    odds_api_io_key: str | None = Field(default=None, validation_alias=AliasChoices("ODDS_API_IO_KEY"))
    sstats_api_key: str | None = Field(default=None, validation_alias=AliasChoices("SSTATS_API_KEY"))
    api_football_key: str | None = Field(default=None, validation_alias=AliasChoices("API_FOOTBALL_KEY"))
    bzzoiro_api_key: str | None = Field(default=None, validation_alias=AliasChoices("BZZOIRO_API_KEY"))

    bookies_bootstrap_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("BOOKIES_BOOTSTRAP_ENABLED"),
    )
    bookies_api_enabled: bool = Field(
        default=True,
        validation_alias=AliasChoices("BOOKIES_API_ENABLED"),
    )
    sstats_enabled: bool = Field(default=True, validation_alias=AliasChoices("SSTATS_ENABLED"))
    enable_odds_api_io: bool = Field(
        default=True,
        validation_alias=AliasChoices("ENABLE_ODDS_API_IO"),
    )
    enable_sstats_context: bool = Field(
        default=True,
        validation_alias=AliasChoices("ENABLE_SSTATS_CONTEXT"),
    )

    bookies_api_login: str | None = Field(default=None, validation_alias=AliasChoices("BOOKIES_API_LOGIN"))
    bookies_api_token: str | None = Field(default=None, validation_alias=AliasChoices("BOOKIES_API_TOKEN"))
    bookies_api_key: str | None = Field(default=None, validation_alias=AliasChoices("BOOKIES_API_KEY"))
    bookies_api_base_url: str = Field(
        default="https://bookiesapi.com/api/get.php",
        validation_alias=AliasChoices("BOOKIES_API_BASE_URL"),
    )
    bookies_api_odds_task: str = Field(
        default="odds",
        validation_alias=AliasChoices("BOOKIES_API_ODDS_TASK"),
    )
    bookies_api_page_limit: int = Field(
        default=50,
        validation_alias=AliasChoices("BOOKIES_API_PAGE_LIMIT"),
    )
    bookies_api_max_pages_per_day: int = Field(
        default=40,
        validation_alias=AliasChoices("BOOKIES_API_MAX_PAGES_PER_DAY"),
    )
    bookies_api_odds_fetch_limit: int = Field(
        default=40,
        validation_alias=AliasChoices("BOOKIES_API_ODDS_FETCH_LIMIT"),
    )
    bookies_api_use_for_backfill_only: bool = Field(
        default=False,
        validation_alias=AliasChoices("BOOKIES_API_USE_FOR_BACKFILL_ONLY"),
    )
    bookies_api_timeout_seconds: float = Field(
        default=30.0,
        validation_alias=AliasChoices("BOOKIES_API_TIMEOUT_SECONDS", "BOOKIES_API_TIMEOUT_MS"),
    )

    run_sports: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["soccer"])
    run_days_ahead: int = Field(default=4, validation_alias=AliasChoices("RUN_DAYS_AHEAD", "DAYS_AHEAD"))
    publish_window_hours: int = Field(default=48, validation_alias=AliasChoices("PUBLISH_WINDOW_HOURS"))
    min_kickoff_lead_minutes: int = Field(
        default=30,
        validation_alias=AliasChoices("MIN_KICKOFF_LEAD_MINUTES"),
    )

    target_bookmakers: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["Bet365", "Unibet"])
    consensus_bookmakers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["Bet365", "Unibet"],
    )
    odds_api_io_bookmakers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["Bet365", "Unibet"],
    )

    min_books_publish: int = Field(default=2, validation_alias=AliasChoices("MIN_BOOKS_PUBLISH"))
    min_sources_publish: int = Field(default=1, validation_alias=AliasChoices("MIN_SOURCES_PUBLISH"))
    min_model_confidence: float = Field(default=0.55, validation_alias=AliasChoices("MIN_MODEL_CONFIDENCE"))
    max_matches_for_odds_fetch: int = Field(
        default=300,
        validation_alias=AliasChoices("MAX_MATCHES_FOR_ODDS_FETCH", "MAX_MATCHES_FOR_PRICING"),
    )

    allow_low_tier: bool = Field(default=False, validation_alias=AliasChoices("ALLOW_LOW_TIER"))
    publish_dry_run: bool = Field(default=False, validation_alias=AliasChoices("PUBLISH_DRY_RUN"))
    max_picks_per_run: int = Field(default=5, validation_alias=AliasChoices("MAX_PICKS_PER_RUN"))

    @field_validator(
        "run_sports",
        "target_bookmakers",
        "consensus_bookmakers",
        "odds_api_io_bookmakers",
        mode="before",
    )
    @classmethod
    def split_csv(cls, value: Any) -> Any:
        if value is None:
            return value
        if isinstance(value, str):
            return [item.strip() for item in value.split(",") if item.strip()]
        return value

    @field_validator("run_sports")
    @classmethod
    def normalize_sports(cls, value: list[str]) -> list[str]:
        result = [item.strip().lower() for item in value if item and item.strip()]
        return result or ["soccer"]

    @field_validator("bookies_api_timeout_seconds", mode="before")
    @classmethod
    def normalize_bookies_timeout(cls, value: Any) -> Any:
        if value is None:
            return 30.0
        try:
            number = float(value)
        except Exception:
            return value
        return number / 1000.0 if number > 1000 else number

    @property
    def tzinfo(self) -> ZoneInfo:
        return ZoneInfo(self.app_timezone)

    @property
    def telegram_bot_token(self) -> str | None:
        return self.telegram_token

    @property
    def max_matches_for_pricing(self) -> int:
        return self.max_matches_for_odds_fetch


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
