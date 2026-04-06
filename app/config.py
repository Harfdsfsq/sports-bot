from __future__ import annotations

from functools import lru_cache
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra='ignore',
        case_sensitive=False,
        enable_decoding=False,
        populate_by_name=True,
    )

    app_name: str = Field(default='sports-value-bot')
    app_env: str = Field(default='production', validation_alias=AliasChoices('APP_ENV', 'ENV'))
    app_timezone: str = Field(default='Europe/Moscow', validation_alias=AliasChoices('APP_TIMEZONE', 'TIMEZONE', 'TZ'))

    state_path: str = Field(default='.data/state.json', validation_alias=AliasChoices('STATE_PATH'))
    debug_path: str = Field(default='.data/debug-last-run.json', validation_alias=AliasChoices('DEBUG_PATH'))
    storage_export_dir: str = Field(default='.data/exports', validation_alias=AliasChoices('STORAGE_EXPORT_DIR', 'EXPORT_DIR'))

    publish_dry_run: bool = Field(default=True, validation_alias=AliasChoices('PUBLISH_DRY_RUN'))
    run_sports: CsvList = Field(default_factory=lambda: ['soccer'], validation_alias=AliasChoices('RUN_SPORTS'))
    run_days_ahead: int = Field(default=4, validation_alias=AliasChoices('RUN_DAYS_AHEAD', 'DAYS_AHEAD'))
    publish_window_hours: int = Field(default=48, validation_alias=AliasChoices('PUBLISH_WINDOW_HOURS'))
    min_kickoff_lead_minutes: int = Field(default=30, validation_alias=AliasChoices('MIN_KICKOFF_LEAD_MINUTES'))
    max_picks_per_run: int = Field(default=5, validation_alias=AliasChoices('MAX_PICKS_PER_RUN', 'TELEGRAM_TOP_LIMIT'))

    target_bookmakers: CsvList = Field(default_factory=lambda: ['Bet365', 'Unibet'], validation_alias=AliasChoices('TARGET_BOOKMAKERS'))
    consensus_bookmakers: CsvList = Field(
        default_factory=lambda: ['Pinnacle', 'Betfair', 'Bet365', 'Unibet'],
        validation_alias=AliasChoices('CONSENSUS_BOOKMAKERS'),
    )
    odds_api_io_bookmakers: CsvList = Field(
        default_factory=lambda: ['Bet365', 'Unibet'],
        validation_alias=AliasChoices('ODDS_API_IO_BOOKMAKERS'),
    )
    bookies_api_sports: CsvList = Field(default_factory=lambda: ['soccer'], validation_alias=AliasChoices('BOOKIES_API_SPORTS'))

    allow_low_tier: bool = Field(default=False, validation_alias=AliasChoices('ALLOW_LOW_TIER', 'EXCLUDE_EXOTIC_LEAGUES'))
    match_start_tolerance_hours: float = Field(default=12.0, validation_alias=AliasChoices('MATCH_START_TOLERANCE_HOURS'))
    fallback_match_start_tolerance_hours: float = Field(default=8.0, validation_alias=AliasChoices('FALLBACK_MATCH_START_TOLERANCE_HOURS'))

    min_books_for_consensus: int = Field(default=2, validation_alias=AliasChoices('MIN_BOOKS_FOR_CONSENSUS', 'STRONG_MARKET_MIN_BOOKS'))
    min_books_publish: int = Field(default=2, validation_alias=AliasChoices('MIN_BOOKS_PUBLISH'))
    min_sources_publish: int = Field(default=1, validation_alias=AliasChoices('MIN_SOURCES_PUBLISH'))
    min_edge_pct: float = Field(default=2.0, validation_alias=AliasChoices('MIN_EDGE_PCT'))
    min_ev_pct: float = Field(default=1.5, validation_alias=AliasChoices('MIN_EV_PCT'))
    min_model_confidence: float = Field(default=0.55, validation_alias=AliasChoices('MIN_MODEL_CONFIDENCE'))
    odds_min: float = Field(default=1.50, validation_alias=AliasChoices('TARGET_ODDS_HARD_MIN', 'ODDS_MIN'))
    odds_max: float = Field(default=4.50, validation_alias=AliasChoices('TARGET_ODDS_HARD_MAX', 'ODDS_MAX'))

    bookies_bootstrap_enabled: bool = Field(default=True, validation_alias=AliasChoices('BOOKIES_BOOTSTRAP_ENABLED'))
    enable_odds_api_io: bool = Field(default=True, validation_alias=AliasChoices('ENABLE_ODDS_API_IO', 'ODDS_API_IO_ENABLED'))
    sstats_enabled: bool = Field(default=True, validation_alias=AliasChoices('SSTATS_ENABLED'))
    enable_sstats_context: bool = Field(default=True, validation_alias=AliasChoices('ENABLE_SSTATS_CONTEXT'))
    api_football_enabled: bool = Field(default=True, validation_alias=AliasChoices('API_FOOTBALL_ENABLED'))
    enable_espn_context: bool = Field(default=True, validation_alias=AliasChoices('ENABLE_ESPN_CONTEXT', 'ESPN_CONTEXT_ENABLED'))
    enable_thesportsdb_context: bool = Field(default=True, validation_alias=AliasChoices('ENABLE_THESPORTSDB_CONTEXT', 'THESPORTSDB_CONTEXT_ENABLED'))

    enable_provider_diagnostics: bool = Field(default=True, validation_alias=AliasChoices('ENABLE_PROVIDER_DIAGNOSTICS'))
    diagnostics_match_limit: int = Field(default=150, validation_alias=AliasChoices('DIAGNOSTICS_MATCH_LIMIT'))

    telegram_bot_token: str | None = Field(default=None, validation_alias=AliasChoices('TELEGRAM_TOKEN', 'TELEGRAM_BOT_TOKEN'))
    telegram_chat_id: str | None = Field(default=None, validation_alias=AliasChoices('TELEGRAM_CHAT_ID'))

    odds_api_io_key: str | None = Field(default=None, validation_alias=AliasChoices('ODDS_API_IO_KEY'))
    odds_api_io_timeout_seconds: float = Field(default=25.0, validation_alias=AliasChoices('ODDS_API_IO_TIMEOUT_SECONDS'))
    odds_api_io_page_limit: int = Field(default=60, validation_alias=AliasChoices('ODDS_API_IO_PAGE_LIMIT'))
    odds_api_io_max_pages_per_sport: int = Field(default=4, validation_alias=AliasChoices('ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT'))

    sstats_api_key: str | None = Field(default=None, validation_alias=AliasChoices('SSTATS_API_KEY'))
    sstats_timeout_seconds: float = Field(default=25.0, validation_alias=AliasChoices('SSTATS_TIMEOUT_SECONDS'))

    api_football_key: str | None = Field(default=None, validation_alias=AliasChoices('API_FOOTBALL_KEY'))
    api_football_base_url: str = Field(default='https://v3.football.api-sports.io', validation_alias=AliasChoices('API_FOOTBALL_BASE_URL'))
    api_football_predictions_limit: int | None = Field(default=None, validation_alias=AliasChoices('API_FOOTBALL_PREDICTIONS_LIMIT'))

    espn_timeout_seconds: float = Field(default=20.0, validation_alias=AliasChoices('ESPN_TIMEOUT_SECONDS'))
    espn_base_site_url: str = Field(default='https://site.api.espn.com/apis/site/v2', validation_alias=AliasChoices('ESPN_BASE_SITE_URL'))
    espn_base_core_url: str = Field(default='https://sports.core.api.espn.com/v2', validation_alias=AliasChoices('ESPN_BASE_CORE_URL'))
    espn_soccer_leagues: CsvList = Field(
        default_factory=lambda: ['eng.1', 'eng.2', 'eng.3', 'eng.4', 'esp.1', 'esp.2', 'ita.1', 'ita.2', 'ger.1', 'ger.2', 'fra.1', 'fra.2', 'ned.1', 'bel.1', 'por.1', 'sco.1', 'usa.1', 'uefa.champions', 'uefa.europa', 'uefa.europa.conf'],
        validation_alias=AliasChoices('ESPN_SOCCER_LEAGUES'),
    )
    espn_max_matches: int = Field(default=120, validation_alias=AliasChoices('ESPN_MAX_MATCHES'))

    thesportsdb_api_key: str = Field(default='123', validation_alias=AliasChoices('THESPORTSDB_API_KEY'))
    thesportsdb_base_url: str = Field(default='https://www.thesportsdb.com/api/v1/json', validation_alias=AliasChoices('THESPORTSDB_BASE_URL'))
    thesportsdb_timeout_seconds: float = Field(default=20.0, validation_alias=AliasChoices('THESPORTSDB_TIMEOUT_SECONDS'))
    thesportsdb_max_leagues: int = Field(default=24, validation_alias=AliasChoices('THESPORTSDB_MAX_LEAGUES'))

    bzzoiro_api_key: str | None = Field(default=None, validation_alias=AliasChoices('BZZOIRO_API_KEY'))

    signal_weight_explicit: float = Field(default=0.40, validation_alias=AliasChoices('SIGNAL_WEIGHT_EXPLICIT'))
    signal_weight_xg: float = Field(default=0.34, validation_alias=AliasChoices('SIGNAL_WEIGHT_XG'))
    signal_weight_strength: float = Field(default=0.16, validation_alias=AliasChoices('SIGNAL_WEIGHT_STRENGTH'))
    signal_weight_momentum: float = Field(default=0.10, validation_alias=AliasChoices('SIGNAL_WEIGHT_MOMENTUM'))
    signal_weight_injuries: float = Field(default=0.07, validation_alias=AliasChoices('SIGNAL_WEIGHT_INJURIES'))
    espn_query_all_allowed_when_unmapped: bool = Field(default=True, validation_alias=AliasChoices('ESPN_QUERY_ALL_ALLOWED_WHEN_UNMAPPED'))
    espn_enable_injuries: bool = Field(default=True, validation_alias=AliasChoices('ESPN_ENABLE_INJURIES'))

    bookies_api_enabled: bool = Field(default=False, validation_alias=AliasChoices('BOOKIES_API_ENABLED'))
    bookies_api_login: str | None = Field(default=None, validation_alias=AliasChoices('BOOKIES_API_LOGIN'))
    bookies_api_token: str | None = Field(default=None, validation_alias=AliasChoices('BOOKIES_API_TOKEN'))
    bookies_api_key: str | None = Field(default=None, validation_alias=AliasChoices('BOOKIES_API_KEY'))
    bookies_api_base_url: str = Field(default='https://bookiesapi.com/api/get.php', validation_alias=AliasChoices('BOOKIES_API_BASE_URL'))
    bookies_api_odds_task: str = Field(default='odds', validation_alias=AliasChoices('BOOKIES_API_ODDS_TASK'))
    bookies_api_odds_fetch_limit: int = Field(default=40, validation_alias=AliasChoices('BOOKIES_API_ODDS_FETCH_LIMIT'))
    bookies_api_page_limit: int = Field(default=50, validation_alias=AliasChoices('BOOKIES_API_PAGE_LIMIT'))
    bookies_api_max_pages_per_day: int = Field(default=10, validation_alias=AliasChoices('BOOKIES_API_MAX_PAGES_PER_DAY'))
    bookies_api_use_for_backfill_only: bool = Field(default=True, validation_alias=AliasChoices('BOOKIES_API_USE_FOR_BACKFILL_ONLY'))
    bookies_api_timeout_seconds: float = Field(default=25.0, validation_alias=AliasChoices('BOOKIES_API_TIMEOUT_SECONDS', 'BOOKIES_API_TIMEOUT_MS'))

    max_matches_for_odds_fetch: int = Field(default=300, validation_alias=AliasChoices('MAX_MATCHES_FOR_ODDS_FETCH', 'MAX_MATCHES_FOR_PRICING'))

    source_weight_theodds: float = Field(default=1.04, validation_alias=AliasChoices('SOURCE_WEIGHT_THEODDS'))
    source_weight_oddsapiio: float = Field(default=1.00, validation_alias=AliasChoices('SOURCE_WEIGHT_ODDSAPIIO'))
    source_weight_bookiesapi: float = Field(default=0.98, validation_alias=AliasChoices('SOURCE_WEIGHT_BOOKIESAPI'))
    source_weight_sstats: float = Field(default=0.90, validation_alias=AliasChoices('SOURCE_WEIGHT_SSTATS'))
    bookmaker_weight_pinnacle: float = Field(default=1.16, validation_alias=AliasChoices('BOOKMAKER_WEIGHT_PINNACLE'))
    bookmaker_weight_betfair: float = Field(default=1.12, validation_alias=AliasChoices('BOOKMAKER_WEIGHT_BETFAIR'))
    bookmaker_weight_bet365: float = Field(default=1.08, validation_alias=AliasChoices('BOOKMAKER_WEIGHT_BET365'))
    bookmaker_weight_unibet: float = Field(default=1.03, validation_alias=AliasChoices('BOOKMAKER_WEIGHT_UNIBET'))
    h2h_score_weight: float = Field(default=0.88, validation_alias=AliasChoices('H2H_SCORE_WEIGHT'))
    totals_score_weight: float = Field(default=1.18, validation_alias=AliasChoices('TOTALS_SCORE_WEIGHT'))
    spreads_score_weight: float = Field(default=1.15, validation_alias=AliasChoices('SPREADS_SCORE_WEIGHT'))
    dnb_score_weight: float = Field(default=1.00, validation_alias=AliasChoices('DNB_SCORE_WEIGHT'))
    double_chance_score_weight: float = Field(default=0.82, validation_alias=AliasChoices('DOUBLE_CHANCE_SCORE_WEIGHT'))
    btts_score_weight: float = Field(default=1.12, validation_alias=AliasChoices('BTTS_SCORE_WEIGHT'))
    team_totals_score_weight: float = Field(default=1.20, validation_alias=AliasChoices('TEAM_TOTALS_SCORE_WEIGHT'))

    max_picks_per_league: int = Field(default=2, validation_alias=AliasChoices('MAX_PICKS_PER_LEAGUE'))
    max_picks_per_family: int = Field(default=2, validation_alias=AliasChoices('MAX_PICKS_PER_FAMILY'))
    max_same_reason_signature: int = Field(default=2, validation_alias=AliasChoices('MAX_SAME_REASON_SIGNATURE'))
    reject_negative_expected_goals: bool = Field(default=True, validation_alias=AliasChoices('REJECT_NEGATIVE_EXPECTED_GOALS'))
    min_expected_goals_value: float = Field(default=0.15, validation_alias=AliasChoices('MIN_EXPECTED_GOALS_VALUE'))
    max_expected_goals_value: float = Field(default=4.80, validation_alias=AliasChoices('MAX_EXPECTED_GOALS_VALUE'))

    @field_validator(
        'run_sports',
        'target_bookmakers',
        'consensus_bookmakers',
        'odds_api_io_bookmakers',
        'bookies_api_sports',
        'espn_soccer_leagues',
        mode='before',
    )
    @classmethod
    def split_csv(cls, value: Any) -> list[str]:
        if value is None:
            return []
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value).strip()
        if not text:
            return []
        return [item.strip() for item in text.split(',') if item.strip()]

    @field_validator('run_sports')
    @classmethod
    def normalize_sports(cls, value: list[str]) -> list[str]:
        allowed = {'soccer', 'basketball', 'baseball', 'icehockey'}
        result = [item.strip().lower() for item in value if item and item.strip().lower() in allowed]
        return result or ['soccer']

    @field_validator('bookies_api_timeout_seconds', mode='before')
    @classmethod
    def normalize_bookies_timeout(cls, value: Any) -> Any:
        if value is None:
            return 25.0
        try:
            number = float(value)
        except Exception:
            return value
        return number / 1000.0 if number > 1000 else number

    @property
    def tzinfo(self) -> ZoneInfo:
        try:
            return ZoneInfo(self.app_timezone)
        except Exception:
            return ZoneInfo('UTC')

    @property
    def telegram_token(self) -> str | None:
        return self.telegram_bot_token

    @property
    def max_matches_for_pricing(self) -> int:
        return self.max_matches_for_odds_fetch

    def source_weight(self, source_name: str) -> float:
        key = str(source_name or '').lower().strip()
        if key == 'the_odds_api':
            return self.source_weight_theodds
        if key == 'odds_api_io':
            return self.source_weight_oddsapiio
        if key in {'bookies_api', 'bookies_bootstrap'}:
            return self.source_weight_bookiesapi
        if key == 'sstats':
            return self.source_weight_sstats
        return 1.0

    def bookmaker_weight(self, bookmaker: str) -> float:
        normalized = ''.join(ch for ch in str(bookmaker or '').lower() if ch.isalnum())
        if normalized == 'pinnacle':
            return self.bookmaker_weight_pinnacle
        if normalized in {'betfair', 'betfairexchange'}:
            return self.bookmaker_weight_betfair
        if normalized == 'bet365':
            return self.bookmaker_weight_bet365
        if normalized.startswith('unibet'):
            return self.bookmaker_weight_unibet
        if normalized in {'williamhill', 'ladbrokes', 'sbobet'}:
            return 1.08
        if normalized in {'marathonbet', 'bwin', '888sport', '188bet', 'betvictor', 'cloudbet'}:
            return 1.03
        if normalized in {'10bet', 'betsson', 'betregal'}:
            return 1.01
        if normalized in {'1xbet', 'melbet', 'cashpoint', 'betathome'}:
            return 0.96
        return 1.0

    def score_weight_for_family(self, family: str) -> float:
        return {
            'h2h': self.h2h_score_weight,
            'totals': self.totals_score_weight,
            'spreads': self.spreads_score_weight,
            'dnb': self.dnb_score_weight,
            'doubleChance': self.double_chance_score_weight,
            'btts': self.btts_score_weight,
            'teamTotals': self.team_totals_score_weight,
        }.get(family, 1.0)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
