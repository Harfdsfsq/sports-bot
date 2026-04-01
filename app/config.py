from __future__ import annotations

from functools import lru_cache
from typing import Any

from pydantic import AliasChoices, Field, field_validator
from typing import Annotated

from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

LEGACY_SOCCER_KEYS = [
    "soccer_fifa_world_cup",
    "soccer_fifa_world_cup_qualifiers_europe",
    "soccer_uefa_champs_league",
    "soccer_uefa_europa_league",
    "soccer_uefa_europa_conference_league",
    "soccer_epl",
    "soccer_spain_la_liga",
    "soccer_italy_serie_a",
    "soccer_germany_bundesliga",
    "soccer_france_ligue_one",
    "soccer_brazil_campeonato",
]

THE_ODDS_SPORTS_DEFAULT = {
    "soccer": LEGACY_SOCCER_KEYS,
    "basketball": ["basketball_nba"],
    "baseball": ["baseball_mlb"],
    "icehockey": ["icehockey_nhl"],
}


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

    telegram_bot_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_BOT_TOKEN", "TELEGRAM_TOKEN"),
    )
    telegram_chat_id: str | None = Field(
        default=None,
        validation_alias=AliasChoices("TELEGRAM_CHAT_ID"),
    )

    the_odds_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("THE_ODDS_API_KEY", "ODDS_API_KEY"),
    )
    odds_api_io_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ODDS_API_IO_KEY"),
    )
    sstats_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("SSTATS_API_KEY"),
    )
    bookies_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BOOKIES_API_KEY"),
    )
    bookies_api_login: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BOOKIES_API_LOGIN"),
    )
    bookies_api_token: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BOOKIES_API_TOKEN"),
    )
    api_football_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("API_FOOTBALL_KEY"),
    )

    run_sports: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["soccer"])
    run_days_ahead: int = Field(default=4, validation_alias=AliasChoices("RUN_DAYS_AHEAD", "DAYS_AHEAD"))
    publish_window_hours: int = Field(default=48, validation_alias=AliasChoices("PUBLISH_WINDOW_HOURS"))

    target_bookmakers: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["Bet365", "Unibet"])
    consensus_bookmakers: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["Pinnacle", "Betfair", "Bet365", "Unibet"]
    )
    the_odds_regions: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["eu", "uk", "us"])
    the_odds_sport_keys: Annotated[list[str], NoDecode] = Field(default_factory=lambda: LEGACY_SOCCER_KEYS.copy())

    allow_low_tier: bool = Field(default=False, validation_alias=AliasChoices("ALLOW_LOW_TIER", "EXCLUDE_EXOTIC_LEAGUES"))
    match_start_tolerance_hours: float = Field(
        default=12.0,
        validation_alias=AliasChoices("MATCH_START_TOLERANCE_HOURS"),
    )
    fallback_match_start_tolerance_hours: float = Field(
        default=8.0,
        validation_alias=AliasChoices("FALLBACK_MATCH_START_TOLERANCE_HOURS"),
    )

    min_books_for_consensus: int = Field(
        default=2,
        validation_alias=AliasChoices("MIN_BOOKS_FOR_CONSENSUS", "STRONG_MARKET_MIN_BOOKS"),
    )
    min_books_publish: int = Field(default=2, validation_alias=AliasChoices("MIN_BOOKS_PUBLISH"))
    min_sources_publish: int = Field(default=1, validation_alias=AliasChoices("MIN_SOURCES_PUBLISH"))
    min_edge_pct: float = Field(default=1.5, validation_alias=AliasChoices("MIN_EDGE_PCT"))
    min_ev_pct: float = Field(default=1.0, validation_alias=AliasChoices("MIN_EV_PCT"))
    min_model_confidence: float = Field(default=54.0, validation_alias=AliasChoices("MIN_MODEL_CONFIDENCE"))
    max_picks_per_run: int = Field(default=5, validation_alias=AliasChoices("MAX_PICKS_PER_RUN", "TELEGRAM_TOP_LIMIT"))
    odds_min: float = Field(default=1.60, validation_alias=AliasChoices("TARGET_ODDS_HARD_MIN", "ODDS_MIN"))
    odds_max: float = Field(default=3.20, validation_alias=AliasChoices("TARGET_ODDS_HARD_MAX", "ODDS_MAX"))

    outlier_price_tolerance_pct: float = Field(
        default=5.5,
        validation_alias=AliasChoices("OUTLIER_PRICE_TOLERANCE_PCT"),
    )
    outlier_max_penalty: float = Field(
        default=10.0,
        validation_alias=AliasChoices("OUTLIER_MAX_PENALTY"),
    )

    enable_derived_soccer_markets: bool = Field(
        default=True,
        validation_alias=AliasChoices("ENABLE_DERIVED_SOCCER_MARKETS"),
    )
    enable_team_totals: bool = Field(default=True, validation_alias=AliasChoices("ENABLE_TEAM_TOTALS"))
    enable_odds_api_io: bool = Field(default=True, validation_alias=AliasChoices("ENABLE_ODDS_API_IO"))
    enable_bookies_api: bool = Field(default=False, validation_alias=AliasChoices("BOOKIES_API_ENABLED", "ENABLE_BOOKIES_API"))
    enable_sstats_context: bool = Field(default=True, validation_alias=AliasChoices("ENABLE_SSTATS_CONTEXT"))

    publish_dry_run: bool = Field(default=True, validation_alias=AliasChoices("PUBLISH_DRY_RUN"))

    the_odds_timeout_seconds: float = Field(default=30.0, validation_alias=AliasChoices("THE_ODDS_TIMEOUT_SECONDS"))
    odds_api_io_timeout_seconds: float = Field(default=25.0, validation_alias=AliasChoices("ODDS_API_IO_TIMEOUT_SECONDS"))
    bookies_api_timeout_seconds: float = Field(default=25.0, validation_alias=AliasChoices("BOOKIES_API_TIMEOUT_MS", "BOOKIES_API_TIMEOUT_SECONDS"))
    sstats_timeout_seconds: float = Field(default=25.0, validation_alias=AliasChoices("SSTATS_TIMEOUT_SECONDS"))

    odds_api_io_page_limit: int = Field(default=60, validation_alias=AliasChoices("ODDS_API_IO_PAGE_LIMIT"))
    odds_api_io_max_pages_per_sport: int = Field(
        default=4,
        validation_alias=AliasChoices("ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT"),
    )
    bookies_api_base_url: str = Field(default="https://bookiesapi.com/api/get.php", validation_alias=AliasChoices("BOOKIES_API_BASE_URL"))
    bookies_api_sports: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["soccer"], validation_alias=AliasChoices("BOOKIES_API_SPORTS"))
    bookies_api_markets: Annotated[list[str], NoDecode] = Field(default_factory=lambda: ["h2h", "spreads", "totals", "btts", "dnb", "doubleChance", "teamTotals"], validation_alias=AliasChoices("BOOKIES_API_MARKETS"))
    bookies_api_use_for_backfill_only: bool = Field(default=True, validation_alias=AliasChoices("BOOKIES_API_USE_FOR_BACKFILL_ONLY"))
    bookies_api_page_limit: int = Field(default=50, validation_alias=AliasChoices("BOOKIES_API_PAGE_LIMIT"))
    bookies_api_max_pages_per_day: int = Field(default=10, validation_alias=AliasChoices("BOOKIES_API_MAX_PAGES_PER_DAY"))
    bookies_api_odds_task: str = Field(default="allodds", validation_alias=AliasChoices("BOOKIES_API_ODDS_TASK"))
    max_matches_for_odds_fetch: int = Field(
        default=300,
        validation_alias=AliasChoices("MAX_MATCHES_FOR_ODDS_FETCH", "MAX_MATCHES_FOR_PRICING"),
    )

    model_shrink_min: float = Field(default=0.18, validation_alias=AliasChoices("MODEL_SHRINK_MIN"))
    model_shrink_max: float = Field(default=0.50, validation_alias=AliasChoices("MODEL_SHRINK_MAX"))

    source_weight_theodds: float = Field(default=1.04, validation_alias=AliasChoices("SOURCE_WEIGHT_THEODDS"))
    source_weight_oddsapiio: float = Field(default=1.00, validation_alias=AliasChoices("SOURCE_WEIGHT_ODDSAPIIO"))
    source_weight_bookiesapi: float = Field(default=0.98, validation_alias=AliasChoices("SOURCE_WEIGHT_BOOKIESAPI"))
    source_weight_sstats: float = Field(default=0.90, validation_alias=AliasChoices("SOURCE_WEIGHT_SSTATS"))

    bookmaker_weight_pinnacle: float = Field(default=1.16, validation_alias=AliasChoices("BOOKMAKER_WEIGHT_PINNACLE"))
    bookmaker_weight_betfair: float = Field(default=1.12, validation_alias=AliasChoices("BOOKMAKER_WEIGHT_BETFAIR"))
    bookmaker_weight_bet365: float = Field(default=1.08, validation_alias=AliasChoices("BOOKMAKER_WEIGHT_BET365"))
    bookmaker_weight_unibet: float = Field(default=1.03, validation_alias=AliasChoices("BOOKMAKER_WEIGHT_UNIBET"))

    h2h_score_weight: float = Field(default=0.88, validation_alias=AliasChoices("H2H_SCORE_WEIGHT"))
    totals_score_weight: float = Field(default=1.18, validation_alias=AliasChoices("TOTALS_SCORE_WEIGHT"))
    spreads_score_weight: float = Field(default=1.15, validation_alias=AliasChoices("SPREADS_SCORE_WEIGHT"))
    dnb_score_weight: float = Field(default=1.00, validation_alias=AliasChoices("DNB_SCORE_WEIGHT"))
    double_chance_score_weight: float = Field(
        default=0.82,
        validation_alias=AliasChoices("DOUBLE_CHANCE_SCORE_WEIGHT"),
    )
    btts_score_weight: float = Field(default=1.12, validation_alias=AliasChoices("BTTS_SCORE_WEIGHT"))
    team_totals_score_weight: float = Field(
        default=1.20,
        validation_alias=AliasChoices("TEAM_TOTALS_SCORE_WEIGHT"),
    )

    @field_validator("run_sports", "target_bookmakers", "consensus_bookmakers", "the_odds_regions", "the_odds_sport_keys", "bookies_api_sports", "bookies_api_markets", mode="before")
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
        allowed = {"soccer", "basketball", "baseball", "icehockey"}
        result = [item.strip().lower() for item in value if item and item.strip().lower() in allowed]
        return result or ["soccer"]

    @field_validator("the_odds_sport_keys")
    @classmethod
    def fill_default_sport_keys(cls, value: list[str], info) -> list[str]:
        if value:
            return value
        sports = info.data.get("run_sports") or ["soccer"]
        keys: list[str] = []
        for sport in sports:
            keys.extend(THE_ODDS_SPORTS_DEFAULT.get(sport, []))
        return keys

    def source_weight(self, source_name: str) -> float:
        key = source_name.lower().strip()
        if key == "the_odds_api":
            return self.source_weight_theodds
        if key == "odds_api_io":
            return self.source_weight_oddsapiio
        if key == "bookies_api":
            return self.source_weight_bookiesapi
        if key == "sstats":
            return self.source_weight_sstats
        return 1.0

    def bookmaker_weight(self, bookmaker: str) -> float:
        normalized = "".join(ch for ch in bookmaker.lower() if ch.isalnum())
        if normalized == "pinnacle":
            return self.bookmaker_weight_pinnacle
        if normalized in {"betfair", "betfairexchange"}:
            return self.bookmaker_weight_betfair
        if normalized == "bet365":
            return self.bookmaker_weight_bet365
        if normalized == "unibet":
            return self.bookmaker_weight_unibet
        if normalized in {"williamhill", "ladbrokes", "sbobet"}:
            return 1.08
        if normalized in {"marathonbet", "bwin", "888sport", "188bet", "betvictor", "cloudbet"}:
            return 1.03
        if normalized in {"10bet", "betsson", "betregal"}:
            return 1.01
        if normalized in {"1xbet", "melbet", "cashpoint", "betathome"}:
            return 0.96
        return 1.0

    def score_weight_for_family(self, family: str) -> float:
        return {
            "h2h": self.h2h_score_weight,
            "totals": self.totals_score_weight,
            "spreads": self.spreads_score_weight,
            "dnb": self.dnb_score_weight,
            "doubleChance": self.double_chance_score_weight,
            "btts": self.btts_score_weight,
            "teamTotals": self.team_totals_score_weight,
        }.get(family, 1.0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


Settings.model_rebuild()
