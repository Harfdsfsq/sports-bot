from __future__ import annotations

import json
from functools import lru_cache
from typing import Annotated, Any

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    app_env: str = "production"
    app_timezone: str = "Europe/Moscow"

    state_path: str = ".data/state.json"
    debug_path: str = ".data/debug-last-run.json"
    storage_export_dir: str = ".data/exports"

    publish_dry_run: bool = False

    run_sports: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["soccer"])
    run_days_ahead: int = 4
    publish_window_hours: int = 48
    min_kickoff_lead_minutes: int = 30

    target_bookmakers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["Bet365", "Unibet"]
    )
    consensus_bookmakers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["Bet365", "Unibet"]
    )
    odds_api_io_bookmakers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["Bet365", "Unibet"]
    )

    min_books_publish: int = 2
    min_sources_publish: int = 1
    min_model_confidence: float = 0.55

    max_matches_for_odds_fetch: int = 300
    max_matches_for_pricing: int = 300

    allow_low_tier: bool = False

    telegram_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_TOKEN", "TELEGRAM_BOT_TOKEN"),
    )
    telegram_chat_id: str | None = None

    odds_api_io_key: str | None = None
    sstats_api_key: str | None = None
    bzzoiro_api_key: str | None = None
    api_football_key: str | None = None
    api_football_predictions_limit: int = 4

    bookies_api_enabled: bool = True
    bookies_api_base_url: str | None = None
    bookies_api_login: str | None = None
    bookies_api_key: str | None = None
    bookies_api_token: str | None = None
    bookies_api_use_for_backfill_only: bool = False
    bookies_api_odds_task: str = "odds"
    bookies_api_odds_fetch_limit: int = 40

    # Оставлено для совместимости, но не использовать в workflow
    the_odds_api_key: str | None = None

    # Необязательные старые поля, чтобы код не падал, если где-то ещё остались ссылки
    sheet_id: str | None = None
    google_sheets_webhook_url: str | None = None
    google_sheets_webhook_token: str | None = None

    @field_validator(
        "run_sports",
        "target_bookmakers",
        "consensus_bookmakers",
        "odds_api_io_bookmakers",
        mode="before",
    )
    @classmethod
    def parse_listish(cls, value: Any) -> list[str]:
        if value is None:
            return []

        if isinstance(value, list):
            return [str(x).strip() for x in value if str(x).strip()]

        if isinstance(value, str):
            raw = value.strip()
            if not raw:
                return []

            if raw.startswith("[") and raw.endswith("]"):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, list):
                        return [str(x).strip() for x in parsed if str(x).strip()]
                except json.JSONDecodeError:
                    pass

            return [part.strip() for part in raw.split(",") if part.strip()]

        return [str(value).strip()]

    @field_validator(
        "telegram_token",
        "telegram_chat_id",
        "odds_api_io_key",
        "sstats_api_key",
        "bzzoiro_api_key",
        "api_football_key",
        "bookies_api_base_url",
        "bookies_api_login",
        "bookies_api_key",
        "bookies_api_token",
        "the_odds_api_key",
        "sheet_id",
        "google_sheets_webhook_url",
        "google_sheets_webhook_token",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, value: Any) -> Any:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @property
    def telegram_bot_token(self) -> str | None:
        return self.telegram_token


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
