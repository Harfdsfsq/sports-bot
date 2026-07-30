from __future__ import annotations

from datetime import timezone
from functools import lru_cache
from typing import Annotated, Any
from zoneinfo import ZoneInfo

from pydantic import AliasChoices, Field, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

CsvList = Annotated[list[str], NoDecode]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
        enable_decoding=False,
        populate_by_name=True,
    )

    app_name: str = Field(default="sports-value-bot")
    app_env: str = Field(default="production", validation_alias=AliasChoices("APP_ENV", "ENV"))
    app_timezone: str = Field(default="Europe/Moscow", validation_alias=AliasChoices("APP_TIMEZONE", "TIMEZONE", "TZ"))
    state_path: str = Field(default=".data/state.json", validation_alias=AliasChoices("STATE_PATH"))
    debug_path: str = Field(default=".logs/debug-last-run.json", validation_alias=AliasChoices("DEBUG_PATH"))
    run_logs_dir: str = Field(default=".logs/runs", validation_alias=AliasChoices("RUN_LOGS_DIR"))
    storage_export_dir: str = Field(default=".data/exports", validation_alias=AliasChoices("STORAGE_EXPORT_DIR", "EXPORT_DIR"))
    publish_dry_run: bool = Field(default=True, validation_alias=AliasChoices("PUBLISH_DRY_RUN"))
    admin_run_token: str | None = Field(default=None, validation_alias=AliasChoices("ADMIN_RUN_TOKEN"))

    bankroll_enabled: bool = Field(default=True, validation_alias=AliasChoices("BANKROLL_ENABLED"))
    bankroll_starting_balance: float = Field(default=1000.0, validation_alias=AliasChoices("BANKROLL_STARTING_BALANCE", "BANKROLL_INITIAL"))
    bankroll_currency: str = Field(default="units", validation_alias=AliasChoices("BANKROLL_CURRENCY"))
    bankroll_flat_stake_pct: float = Field(default=3.0, validation_alias=AliasChoices("BANKROLL_FLAT_STAKE_PCT"))
    bankroll_kelly_enabled: bool = Field(default=True, validation_alias=AliasChoices("BANKROLL_KELLY_ENABLED"))
    bankroll_kelly_fraction: float = Field(default=0.35, validation_alias=AliasChoices("BANKROLL_KELLY_FRACTION"))
    bankroll_min_stake_pct: float = Field(default=1.0, validation_alias=AliasChoices("BANKROLL_MIN_STAKE_PCT"))
    bankroll_max_stake_pct: float = Field(default=6.0, validation_alias=AliasChoices("BANKROLL_MAX_STAKE_PCT"))
    bankroll_min_stake_amount: float = Field(default=10.0, validation_alias=AliasChoices("BANKROLL_MIN_STAKE_AMOUNT"))
    bankroll_max_open_exposure_pct: float = Field(default=18.0, validation_alias=AliasChoices("BANKROLL_MAX_OPEN_EXPOSURE_PCT"))
    bankroll_round_to: float = Field(default=1.0, validation_alias=AliasChoices("BANKROLL_ROUND_TO"))
    bankroll_force_min_stake_when_empty_enabled: bool = Field(default=True, validation_alias=AliasChoices("BANKROLL_FORCE_MIN_STAKE_WHEN_EMPTY_ENABLED"))
    bankroll_force_min_stake_max_pct: float = Field(default=1.2, validation_alias=AliasChoices("BANKROLL_FORCE_MIN_STAKE_MAX_PCT"))
    settlement_enabled: bool = Field(default=True, validation_alias=AliasChoices("SETTLEMENT_ENABLED"))
    settlement_grace_minutes: int = Field(default=180, validation_alias=AliasChoices("SETTLEMENT_GRACE_MINUTES"))
    settlement_lookback_days: int = Field(default=5, validation_alias=AliasChoices("SETTLEMENT_LOOKBACK_DAYS"))
    settlement_send_telegram_summary: bool = Field(default=True, validation_alias=AliasChoices("SETTLEMENT_SEND_TELEGRAM_SUMMARY"))

    run_sports: CsvList = Field(default_factory=lambda: ["soccer"], validation_alias=AliasChoices("RUN_SPORTS"))
    run_days_ahead: int = Field(default=4, validation_alias=AliasChoices("RUN_DAYS_AHEAD", "DAYS_AHEAD"))
    publish_window_hours: int = Field(default=48, validation_alias=AliasChoices("PUBLISH_WINDOW_HOURS"))
    min_kickoff_lead_minutes: int = Field(default=30, validation_alias=AliasChoices("MIN_KICKOFF_LEAD_MINUTES"))
    adaptive_min_kickoff_lead_enabled: bool = Field(default=False, validation_alias=AliasChoices("ADAPTIVE_MIN_KICKOFF_LEAD_ENABLED"))
    adaptive_min_kickoff_lead_minutes: int = Field(default=20, validation_alias=AliasChoices("ADAPTIVE_MIN_KICKOFF_LEAD_MINUTES"))
    emergency_min_kickoff_lead_enabled: bool = Field(default=False, validation_alias=AliasChoices("EMERGENCY_MIN_KICKOFF_LEAD_ENABLED"))
    emergency_min_kickoff_lead_minutes: int = Field(default=10, validation_alias=AliasChoices("EMERGENCY_MIN_KICKOFF_LEAD_MINUTES"))
    emergency_min_kickoff_activation_ratio: float = Field(default=0.85, validation_alias=AliasChoices("EMERGENCY_MIN_KICKOFF_ACTIVATION_RATIO"))
    force_relaxed_min_kickoff_lead_enabled: bool = Field(default=False, validation_alias=AliasChoices("FORCE_RELAXED_MIN_KICKOFF_LEAD_ENABLED"))
    force_relaxed_min_kickoff_lead_minutes: int = Field(default=10, validation_alias=AliasChoices("FORCE_RELAXED_MIN_KICKOFF_LEAD_MINUTES"))
    manual_late_mode_enabled: bool = Field(default=False, validation_alias=AliasChoices("MANUAL_LATE_MODE_ENABLED"))
    manual_late_min_kickoff_lead_minutes: int = Field(default=20, validation_alias=AliasChoices("MANUAL_LATE_MIN_KICKOFF_LEAD_MINUTES"))
    manual_late_adaptive_min_kickoff_lead_minutes: int = Field(default=10, validation_alias=AliasChoices("MANUAL_LATE_ADAPTIVE_MIN_KICKOFF_LEAD_MINUTES"))
    max_picks_per_run: int = Field(default=2, validation_alias=AliasChoices("MAX_PICKS_PER_RUN", "TELEGRAM_TOP_LIMIT"))

    target_bookmakers: CsvList = Field(
        default_factory=lambda: ["Pinnacle", "Betfair", "Bet365", "Unibet", "William Hill", "1xBet", "Bwin"],
        validation_alias=AliasChoices("TARGET_BOOKMAKERS"),
    )
    consensus_bookmakers: CsvList = Field(
        default_factory=lambda: ["Pinnacle", "Betfair", "Bet365", "Unibet", "William Hill", "1xBet", "Bwin"],
        validation_alias=AliasChoices("CONSENSUS_BOOKMAKERS"),
    )
    odds_api_io_bookmakers: CsvList = Field(
        default_factory=lambda: ["Bet365", "Unibet"],
        validation_alias=AliasChoices("ODDS_API_IO_BOOKMAKERS"),
    )
    odds_api_io_bookmakers_account1: CsvList = Field(
        default_factory=lambda: ["Bet365", "Unibet"],
        validation_alias=AliasChoices("ODDS_API_IO_BOOKMAKERS_ACCOUNT1"),
    )
    odds_api_io_bookmakers_account2: CsvList = Field(
        default_factory=lambda: ["William Hill", "Bwin"],
        validation_alias=AliasChoices("ODDS_API_IO_BOOKMAKERS_ACCOUNT2"),
    )
    bookies_api_sports: CsvList = Field(default_factory=lambda: ["soccer"], validation_alias=AliasChoices("BOOKIES_API_SPORTS"))
    allow_low_tier: bool = Field(default=False, validation_alias=AliasChoices("ALLOW_LOW_TIER", "EXCLUDE_EXOTIC_LEAGUES"))

    match_start_tolerance_hours: float = Field(default=12.0, validation_alias=AliasChoices("MATCH_START_TOLERANCE_HOURS"))
    fallback_match_start_tolerance_hours: float = Field(default=8.0, validation_alias=AliasChoices("FALLBACK_MATCH_START_TOLERANCE_HOURS"))
    min_books_for_consensus: int = Field(default=2, validation_alias=AliasChoices("MIN_BOOKS_FOR_CONSENSUS", "STRONG_MARKET_MIN_BOOKS"))
    min_books_publish: int = Field(default=2, validation_alias=AliasChoices("MIN_BOOKS_PUBLISH", "PUBLISH_MIN_BOOKS"))
    min_sources_publish: int = Field(default=2, validation_alias=AliasChoices("MIN_SOURCES_PUBLISH", "PUBLISH_MIN_ODDS_SOURCES"))
    min_context_sources_publish: int = Field(
        default=2,
        validation_alias=AliasChoices("MIN_CONTEXT_SOURCES_PUBLISH", "PUBLISH_MIN_CONTEXT_SOURCES"),
    )
    publish_allow_b_tier: bool = Field(default=True, validation_alias=AliasChoices("PUBLISH_ALLOW_B_TIER", "CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B"))
    publish_coverage_tier_mode: str = Field(default="hybrid", validation_alias=AliasChoices("PUBLISH_COVERAGE_TIER_MODE", "HARIZON_PUBLICATION_TIER_MODE"))

    min_edge_pct: float = Field(default=2.0, validation_alias=AliasChoices("MIN_EDGE_PCT"))
    min_ev_pct: float = Field(default=1.5, validation_alias=AliasChoices("MIN_EV_PCT"))
    min_model_confidence: float = Field(default=0.53, validation_alias=AliasChoices("MIN_MODEL_CONFIDENCE"))
    odds_min: float = Field(default=1.50, validation_alias=AliasChoices("TARGET_ODDS_HARD_MIN", "ODDS_MIN"))
    odds_max: float = Field(default=4.50, validation_alias=AliasChoices("TARGET_ODDS_HARD_MAX", "ODDS_MAX"))

    bookies_bootstrap_enabled: bool = Field(default=True, validation_alias=AliasChoices("BOOKIES_BOOTSTRAP_ENABLED"))
    enable_odds_api_io: bool = Field(default=True, validation_alias=AliasChoices("ENABLE_ODDS_API_IO", "ODDS_API_IO_ENABLED"))
    sstats_enabled: bool = Field(default=False, validation_alias=AliasChoices("SSTATS_ENABLED"))
    enable_sstats_context: bool = Field(default=False, validation_alias=AliasChoices("ENABLE_SSTATS_CONTEXT"))
    api_football_enabled: bool = Field(default=False, validation_alias=AliasChoices("API_FOOTBALL_ENABLED", "ENABLE_API_FOOTBALL"))
    enable_espn_context: bool = Field(default=True, validation_alias=AliasChoices("ENABLE_ESPN_CONTEXT", "ESPN_CONTEXT_ENABLED"))
    enable_thesportsdb_context: bool = Field(default=False, validation_alias=AliasChoices("ENABLE_THESPORTSDB_CONTEXT", "THESPORTSDB_CONTEXT_ENABLED"))
    enable_football_data_context: bool = Field(default=False, validation_alias=AliasChoices("ENABLE_FOOTBALL_DATA_CONTEXT", "FOOTBALL_DATA_ENABLED"))
    enable_openfootball_context: bool = Field(default=True, validation_alias=AliasChoices("ENABLE_OPENFOOTBALL_CONTEXT", "OPENFOOTBALL_ENABLED"))
    enable_newsapi_context: bool = Field(default=False, validation_alias=AliasChoices("ENABLE_NEWSAPI_CONTEXT", "NEWSAPI_ENABLED"))
    enable_gnews_context: bool = Field(default=False, validation_alias=AliasChoices("ENABLE_GNEWS_CONTEXT", "GNEWS_ENABLED"))
    enable_bzzoiro_context: bool = Field(default=True, validation_alias=AliasChoices("ENABLE_BZZOIRO_CONTEXT", "BZZOIRO_ENABLED"))
    enable_provider_diagnostics: bool = Field(default=True, validation_alias=AliasChoices("ENABLE_PROVIDER_DIAGNOSTICS"))

    diagnostics_match_limit: int = Field(default=240, validation_alias=AliasChoices("DIAGNOSTICS_MATCH_LIMIT"))
    context_enrichment_match_limit: int = Field(default=420, validation_alias=AliasChoices("CONTEXT_ENRICHMENT_MATCH_LIMIT"))
    context_enrichment_requires_offers: bool = Field(default=False, validation_alias=AliasChoices("CONTEXT_ENRICHMENT_REQUIRES_OFFERS"))
    seen_candidate_lookback_hours: float = Field(default=36.0, validation_alias=AliasChoices("SEEN_CANDIDATE_LOOKBACK_HOURS"))
    republish_seen_candidates_when_empty: bool = Field(
        default=False,
        validation_alias=AliasChoices("REPUBLISH_SEEN_CANDIDATES_WHEN_EMPTY"),
    )
    republish_seen_candidates_limit: int = Field(
        default=1,
        validation_alias=AliasChoices("REPUBLISH_SEEN_CANDIDATES_LIMIT"),
    )

    simple_market_fallback_enabled: bool = Field(default=True, validation_alias=AliasChoices("SIMPLE_MARKET_FALLBACK_ENABLED"))
    partial_context_market_fallback_enabled: bool = Field(default=True, validation_alias=AliasChoices("PARTIAL_CONTEXT_MARKET_FALLBACK_ENABLED"))
    simple_market_totals_min_ev_pct: float = Field(default=1.0, validation_alias=AliasChoices("SIMPLE_MARKET_TOTALS_MIN_EV_PCT"))
    simple_market_totals_min_edge_pct: float = Field(default=1.4, validation_alias=AliasChoices("SIMPLE_MARKET_TOTALS_MIN_EDGE_PCT"))
    simple_market_totals_min_confidence: float = Field(default=50.0, validation_alias=AliasChoices("SIMPLE_MARKET_TOTALS_MIN_CONFIDENCE"))
    simple_market_h2h_min_ev_pct: float = Field(default=0.8, validation_alias=AliasChoices("SIMPLE_MARKET_H2H_MIN_EV_PCT"))
    simple_market_h2h_min_edge_pct: float = Field(default=1.2, validation_alias=AliasChoices("SIMPLE_MARKET_H2H_MIN_EDGE_PCT"))
    simple_market_h2h_min_confidence: float = Field(default=52.0, validation_alias=AliasChoices("SIMPLE_MARKET_H2H_MIN_CONFIDENCE"))
    simple_market_spreads_min_ev_pct: float = Field(default=0.9, validation_alias=AliasChoices("SIMPLE_MARKET_SPREADS_MIN_EV_PCT"))
    simple_market_spreads_min_edge_pct: float = Field(default=1.4, validation_alias=AliasChoices("SIMPLE_MARKET_SPREADS_MIN_EDGE_PCT"))
    simple_market_spreads_min_confidence: float = Field(default=51.0, validation_alias=AliasChoices("SIMPLE_MARKET_SPREADS_MIN_CONFIDENCE"))
    simple_market_min_signal_boost_pct: float = Field(default=0.65, validation_alias=AliasChoices("SIMPLE_MARKET_MIN_SIGNAL_BOOST_PCT"))
    market_derived_candidates_enabled: bool = Field(default=True, validation_alias=AliasChoices("MARKET_DERIVED_CANDIDATES_ENABLED"))
    market_derived_min_books: int = Field(default=2, validation_alias=AliasChoices("MARKET_DERIVED_MIN_BOOKS"))
    market_derived_min_sources: int = Field(default=2, validation_alias=AliasChoices("MARKET_DERIVED_MIN_SOURCES"))
    market_derived_min_observations: int = Field(default=2, validation_alias=AliasChoices("MARKET_DERIVED_MIN_OBSERVATIONS"))
    market_derived_min_edge_pct: float = Field(default=1.2, validation_alias=AliasChoices("MARKET_DERIVED_MIN_EDGE_PCT"))
    market_derived_min_delta_prob_pp: float = Field(default=0.0, validation_alias=AliasChoices("MARKET_DERIVED_MIN_DELTA_PROB_PP"))
    market_derived_max_dispersion_pct: float = Field(default=7.0, validation_alias=AliasChoices("MARKET_DERIVED_MAX_DISPERSION_PCT"))
    market_derived_max_h2h_odds: float = Field(default=4.4, validation_alias=AliasChoices("MARKET_DERIVED_MAX_H2H_ODDS"))
    market_derived_context_confidence_cap: float = Field(default=60.0, validation_alias=AliasChoices("MARKET_DERIVED_CONTEXT_CONFIDENCE_CAP"))
    market_derived_consensus_relief_enabled: bool = Field(default=True, validation_alias=AliasChoices("MARKET_DERIVED_CONSENSUS_RELIEF_ENABLED"))
    market_derived_consensus_relief_min_books: int = Field(default=2, validation_alias=AliasChoices("MARKET_DERIVED_CONSENSUS_RELIEF_MIN_BOOKS"))
    market_derived_consensus_relief_min_sources: int = Field(default=1, validation_alias=AliasChoices("MARKET_DERIVED_CONSENSUS_RELIEF_MIN_SOURCES"))
    market_derived_consensus_relief_min_edge_pct: float = Field(default=2.1, validation_alias=AliasChoices("MARKET_DERIVED_CONSENSUS_RELIEF_MIN_EDGE_PCT"))
    market_derived_consensus_relief_max_dispersion_pct: float = Field(default=4.8, validation_alias=AliasChoices("MARKET_DERIVED_CONSENSUS_RELIEF_MAX_DISPERSION_PCT"))
    market_derived_consensus_relief_min_observations: int = Field(default=1, validation_alias=AliasChoices("MARKET_DERIVED_CONSENSUS_RELIEF_MIN_OBSERVATIONS"))
    market_derived_consensus_relief_probability_gap_max: float = Field(default=0.03, validation_alias=AliasChoices("MARKET_DERIVED_CONSENSUS_RELIEF_PROBABILITY_GAP_MAX"))
    market_derived_consensus_relief_probability_boost_pct: float = Field(default=0.85, validation_alias=AliasChoices("MARKET_DERIVED_CONSENSUS_RELIEF_PROBABILITY_BOOST_PCT"))
    probability_gate_relief_max_gap: float = Field(default=0.05, validation_alias=AliasChoices("PROBABILITY_GATE_RELIEF_MAX_GAP"))
    probability_gate_relief_min_confidence: float = Field(default=55.0, validation_alias=AliasChoices("PROBABILITY_GATE_RELIEF_MIN_CONFIDENCE"))
    probability_gate_relief_min_edge_pct: float = Field(default=2.8, validation_alias=AliasChoices("PROBABILITY_GATE_RELIEF_MIN_EDGE_PCT"))
    probability_gate_relief_min_ev_pct: float = Field(default=1.2, validation_alias=AliasChoices("PROBABILITY_GATE_RELIEF_MIN_EV_PCT"))
    max_candidates_per_match_pre_filter: int = Field(default=3, validation_alias=AliasChoices("MAX_CANDIDATES_PER_MATCH_PRE_FILTER"))
    max_internal_candidates_per_run: int = Field(default=8, validation_alias=AliasChoices("MAX_INTERNAL_CANDIDATES_PER_RUN"))
    shadow_tracking_enabled: bool = Field(default=True, validation_alias=AliasChoices("SHADOW_TRACKING_ENABLED"))
    shadow_tracking_max_per_run: int = Field(default=6, validation_alias=AliasChoices("SHADOW_TRACKING_MAX_PER_RUN"))
    shadow_tracking_min_publication_score: float = Field(default=12.0, validation_alias=AliasChoices("SHADOW_TRACKING_MIN_PUBLICATION_SCORE"))
    shadow_tracking_min_ev_pct: float = Field(default=0.8, validation_alias=AliasChoices("SHADOW_TRACKING_MIN_EV_PCT"))
    shadow_tracking_min_edge_pct: float = Field(default=1.2, validation_alias=AliasChoices("SHADOW_TRACKING_MIN_EDGE_PCT"))
    shadow_tracking_min_confidence: float = Field(default=50.0, validation_alias=AliasChoices("SHADOW_TRACKING_MIN_CONFIDENCE"))
    shadow_tracking_store_quality_rejections: bool = Field(default=True, validation_alias=AliasChoices("SHADOW_TRACKING_STORE_QUALITY_REJECTIONS"))
    self_history_context_enabled: bool = Field(default=True, validation_alias=AliasChoices("SELF_HISTORY_CONTEXT_ENABLED"))
    self_history_context_max_runs: int = Field(default=48, validation_alias=AliasChoices("SELF_HISTORY_CONTEXT_MAX_RUNS"))
    self_history_context_max_age_days: int = Field(default=45, validation_alias=AliasChoices("SELF_HISTORY_CONTEXT_MAX_AGE_DAYS"))
    self_history_context_min_team_samples: int = Field(default=2, validation_alias=AliasChoices("SELF_HISTORY_CONTEXT_MIN_TEAM_SAMPLES"))
    self_history_context_confidence_base: float = Field(default=50.0, validation_alias=AliasChoices("SELF_HISTORY_CONTEXT_CONFIDENCE_BASE"))
    self_history_context_confidence_step: float = Field(default=1.8, validation_alias=AliasChoices("SELF_HISTORY_CONTEXT_CONFIDENCE_STEP"))
    self_history_context_confidence_cap: float = Field(default=60.0, validation_alias=AliasChoices("SELF_HISTORY_CONTEXT_CONFIDENCE_CAP"))

    analysis_match_cap_per_run: int = Field(default=260, validation_alias=AliasChoices("ANALYSIS_MATCH_CAP_PER_RUN", "DAILY_ANALYSIS_MATCH_LIMIT"))

    telegram_bot_token: str | None = Field(default=None, validation_alias=AliasChoices("TELEGRAM_TOKEN", "TELEGRAM_BOT_TOKEN"))
    telegram_chat_id: str | None = Field(default=None, validation_alias=AliasChoices("TELEGRAM_CHAT_ID"))
    prediction_publication_enabled: bool = Field(default=True, validation_alias=AliasChoices("PREDICTION_PUBLICATION_ENABLED"))
    run_report_enabled: bool = Field(default=True, validation_alias=AliasChoices("RUN_REPORT_ENABLED"))
    run_report_only_when_no_predictions: bool = Field(default=True, validation_alias=AliasChoices("RUN_REPORT_ONLY_WHEN_NO_PREDICTIONS"))
    run_report_top_reasons: int = Field(default=4, validation_alias=AliasChoices("RUN_REPORT_TOP_REASONS"))
    daily_report_enabled: bool = Field(default=True, validation_alias=AliasChoices("DAILY_REPORT_ENABLED"))
    daily_report_send_telegram: bool = Field(default=True, validation_alias=AliasChoices("DAILY_REPORT_SEND_TELEGRAM"))
    daily_report_hour_local: int = Field(default=22, validation_alias=AliasChoices("DAILY_REPORT_HOUR_LOCAL"))
    daily_report_target_offset_days: int = Field(default=0, validation_alias=AliasChoices("DAILY_REPORT_TARGET_OFFSET_DAYS"))
    daily_report_min_bets: int = Field(default=1, validation_alias=AliasChoices("DAILY_REPORT_MIN_BETS"))
    daily_report_resend_on_change: bool = Field(default=True, validation_alias=AliasChoices("DAILY_REPORT_RESEND_ON_CHANGE"))
    nightly_review_report_only_enabled: bool = Field(default=True, validation_alias=AliasChoices("NIGHTLY_REVIEW_REPORT_ONLY_ENABLED"))
    nightly_review_store_adjustments_enabled: bool = Field(default=True, validation_alias=AliasChoices("NIGHTLY_REVIEW_STORE_ADJUSTMENTS_ENABLED"))
    sheet_id: str | None = Field(default=None, validation_alias=AliasChoices("SHEET_ID"))

    odds_api_io_key: str | None = Field(default=None, validation_alias=AliasChoices("ODDS_API_IO_KEY"))
    odds_api_io_key_2: str | None = Field(default=None, validation_alias=AliasChoices("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2"))
    odds_api_io_timeout_seconds: float = Field(default=25.0, validation_alias=AliasChoices("ODDS_API_IO_TIMEOUT_SECONDS"))
    odds_api_io_page_limit: int = Field(default=100, validation_alias=AliasChoices("ODDS_API_IO_PAGE_LIMIT"))
    odds_api_io_max_pages_per_sport: int = Field(default=8, validation_alias=AliasChoices("ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT"))
    odds_api_io_per_run_max: int = Field(default=8, validation_alias=AliasChoices("ODDS_API_IO_PER_RUN_MAX", "ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN"))

    sstats_api_key: str | None = Field(default=None, validation_alias=AliasChoices("SSTATS_API_KEY"))
    sstats_timeout_seconds: float = Field(default=25.0, validation_alias=AliasChoices("SSTATS_TIMEOUT_SECONDS"))
    sstats_lookback_days: int = Field(default=30, validation_alias=AliasChoices("SSTATS_LOOKBACK_DAYS"))
    sstats_recent_matches: int = Field(default=8, validation_alias=AliasChoices("SSTATS_RECENT_MATCHES"))
    sstats_requests_max_per_run: int = Field(default=4, validation_alias=AliasChoices("SSTATS_REQUESTS_MAX_PER_RUN", "SSTATS_PER_RUN_MAX", "SSTATS_MAX_HTTP_REQUESTS_PER_RUN"))

    api_football_key: str | None = Field(default=None, validation_alias=AliasChoices("API_FOOTBALL_KEY"))
    api_football_base_url: str = Field(default="https://v3.football.api-sports.io", validation_alias=AliasChoices("API_FOOTBALL_BASE_URL"))
    api_football_per_run_max: int = Field(default=0, validation_alias=AliasChoices("API_FOOTBALL_PER_RUN_MAX", "API_FOOTBALL_MAX_HTTP_REQUESTS_PER_RUN"))
    api_football_rate_limit_cooldown_minutes: int = Field(default=180, validation_alias=AliasChoices("API_FOOTBALL_RATE_LIMIT_COOLDOWN_MINUTES"))
    api_football_auth_error_cooldown_minutes: int = Field(default=1440, validation_alias=AliasChoices("API_FOOTBALL_AUTH_ERROR_COOLDOWN_MINUTES"))

    football_data_api_key: str | None = Field(default=None, validation_alias=AliasChoices("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY"))
    football_data_base_url: str = Field(default="https://api.football-data.org/v4", validation_alias=AliasChoices("FOOTBALL_DATA_BASE_URL"))
    football_data_timeout_seconds: float = Field(default=20.0, validation_alias=AliasChoices("FOOTBALL_DATA_TIMEOUT_SECONDS"))
    football_data_requests_max_per_run: int = Field(default=4, validation_alias=AliasChoices("FOOTBALL_DATA_REQUESTS_MAX_PER_RUN", "FOOTBALL_DATA_PER_RUN_MAX", "FOOTBALL_DATA_MAX_HTTP_REQUESTS_PER_RUN"))
    football_data_match_limit: int = Field(default=80, validation_alias=AliasChoices("FOOTBALL_DATA_MATCH_LIMIT"))
    football_data_standings_limit: int = Field(default=8, validation_alias=AliasChoices("FOOTBALL_DATA_STANDINGS_LIMIT"))
    football_data_days_ahead: int = Field(default=2, validation_alias=AliasChoices("FOOTBALL_DATA_DAYS_AHEAD"))
    football_data_history_competitions_limit: int = Field(default=8, validation_alias=AliasChoices("FOOTBALL_DATA_HISTORY_COMPETITIONS_LIMIT"))
    football_data_competition_match_limit: int = Field(default=220, validation_alias=AliasChoices("FOOTBALL_DATA_COMPETITION_MATCH_LIMIT"))
    football_data_min_team_history_matches: int = Field(default=1, validation_alias=AliasChoices("FOOTBALL_DATA_MIN_TEAM_HISTORY_MATCHES"))
    football_data_allow_cup_standings: bool = Field(default=True, validation_alias=AliasChoices("FOOTBALL_DATA_ALLOW_CUP_STANDINGS"))
    football_data_match_score_threshold: float = Field(default=42.0, validation_alias=AliasChoices("FOOTBALL_DATA_MATCH_SCORE_THRESHOLD"))
    football_data_team_match_threshold: float = Field(default=0.68, validation_alias=AliasChoices("FOOTBALL_DATA_TEAM_MATCH_THRESHOLD"))

    api_football_predictions_limit: int | None = Field(default=None, validation_alias=AliasChoices("API_FOOTBALL_PREDICTIONS_LIMIT"))
    api_football_context_match_limit: int = Field(default=28, validation_alias=AliasChoices("API_FOOTBALL_CONTEXT_MATCH_LIMIT"))
    espn_context_match_limit: int = Field(default=36, validation_alias=AliasChoices("ESPN_CONTEXT_MATCH_LIMIT"))
    thesportsdb_context_match_limit: int = Field(default=80, validation_alias=AliasChoices("THESPORTSDB_CONTEXT_MATCH_LIMIT"))
    thesportsdb_requests_max_per_run: int = Field(default=6, validation_alias=AliasChoices("THESPORTSDB_REQUESTS_MAX_PER_RUN", "THESPORTSDB_PER_RUN_MAX", "THESPORTSDB_MAX_HTTP_REQUESTS_PER_RUN"))
    football_data_context_match_limit: int = Field(default=120, validation_alias=AliasChoices("FOOTBALL_DATA_CONTEXT_MATCH_LIMIT"))
    openfootball_context_match_limit: int = Field(default=180, validation_alias=AliasChoices("OPENFOOTBALL_CONTEXT_MATCH_LIMIT"))
    newsapi_context_match_limit: int = Field(default=12, validation_alias=AliasChoices("NEWSAPI_CONTEXT_MATCH_LIMIT"))
    gnews_context_match_limit: int = Field(default=8, validation_alias=AliasChoices("GNEWS_CONTEXT_MATCH_LIMIT"))
    futrixmetrics_context_match_limit: int = Field(default=6, validation_alias=AliasChoices("FUTRIXMETRICS_CONTEXT_MATCH_LIMIT"))
    enable_context_staging: bool = Field(default=True, validation_alias=AliasChoices("ENABLE_CONTEXT_STAGING"))
    premium_context_shortlist_limit: int = Field(default=18, validation_alias=AliasChoices("PREMIUM_CONTEXT_SHORTLIST_LIMIT"))
    premium_news_shortlist_limit: int = Field(default=3, validation_alias=AliasChoices("PREMIUM_NEWS_SHORTLIST_LIMIT"))
    value_hint_min_edge_pct: float = Field(default=1.0, validation_alias=AliasChoices("VALUE_HINT_MIN_EDGE_PCT"))
    detailed_telegram_writeup: bool = Field(default=True, validation_alias=AliasChoices("DETAILED_TELEGRAM_WRITEUP"))
    telegram_writeup_max_points: int = Field(default=5, validation_alias=AliasChoices("TELEGRAM_WRITEUP_MAX_POINTS"))
    telegram_writeup_min_recent_sample: int = Field(default=3, validation_alias=AliasChoices("TELEGRAM_WRITEUP_MIN_RECENT_SAMPLE"))
    telegram_writeup_min_split_sample: int = Field(default=3, validation_alias=AliasChoices("TELEGRAM_WRITEUP_MIN_SPLIT_SAMPLE"))
    telegram_writeup_min_form_diff_pct: float = Field(default=6.0, validation_alias=AliasChoices("TELEGRAM_WRITEUP_MIN_FORM_DIFF_PCT"))
    telegram_writeup_min_table_gap: float = Field(default=0.22, validation_alias=AliasChoices("TELEGRAM_WRITEUP_MIN_TABLE_GAP"))
    telegram_writeup_show_used_marker: bool = Field(default=False, validation_alias=AliasChoices("TELEGRAM_WRITEUP_SHOW_USED_MARKER"))

    espn_timeout_seconds: float = Field(default=20.0, validation_alias=AliasChoices("ESPN_TIMEOUT_SECONDS"))
    espn_base_site_url: str = Field(default="https://site.api.espn.com/apis/site/v2", validation_alias=AliasChoices("ESPN_BASE_SITE_URL"))
    espn_base_core_url: str = Field(default="https://sports.core.api.espn.com/v2", validation_alias=AliasChoices("ESPN_BASE_CORE_URL"))
    espn_soccer_leagues: CsvList = Field(
        default_factory=lambda: [
            "eng.1", "eng.2", "eng.3", "eng.4", "eng.5", "esp.1", "esp.2", "ita.1", "ita.2",
            "ger.1", "ger.2", "fra.1", "fra.2", "ned.1", "ned.2", "sco.1", "sco.2", "sco.3",
            "sco.4", "bel.1", "por.1", "den.1", "nor.1", "swe.1", "tur.1", "aut.1", "usa.1",
            "uefa.champions", "uefa.europa", "uefa.europa.conf",
        ],
        validation_alias=AliasChoices("ESPN_SOCCER_LEAGUES"),
    )
    espn_max_matches: int = Field(default=100, validation_alias=AliasChoices("ESPN_MAX_MATCHES"))
    espn_event_match_threshold: float = Field(default=44.0, validation_alias=AliasChoices("ESPN_EVENT_MATCH_THRESHOLD"))
    espn_slugs_per_run_limit: int = Field(default=10, validation_alias=AliasChoices("ESPN_SLUGS_PER_RUN_LIMIT"))
    espn_form_only_context_confidence: float = Field(default=53.0, validation_alias=AliasChoices("ESPN_FORM_ONLY_CONTEXT_CONFIDENCE"))

    thesportsdb_api_key: str = Field(default="123", validation_alias=AliasChoices("THESPORTSDB_API_KEY"))
    thesportsdb_base_url: str = Field(default="https://www.thesportsdb.com/api/v1/json", validation_alias=AliasChoices("THESPORTSDB_BASE_URL"))
    thesportsdb_timeout_seconds: float = Field(default=20.0, validation_alias=AliasChoices("THESPORTSDB_TIMEOUT_SECONDS"))
    thesportsdb_max_leagues: int = Field(default=24, validation_alias=AliasChoices("THESPORTSDB_MAX_LEAGUES"))

    newsapi_key: str | None = Field(default=None, validation_alias=AliasChoices("NEWSAPI_KEY"))
    currents_key: str | None = Field(default=None, validation_alias=AliasChoices("CURRENTS_API_KEY", "CURRENTS_KEY", "CURRENTS_NEWS_KEY"))
    currents_base_url: str = Field(default="https://api.currentsapi.services/v1", validation_alias=AliasChoices("CURRENTS_BASE_URL", "CURRENTS_NEWS_BASE_URL"))
    newsapi_base_url: str = Field(default="https://newsapi.org/v2", validation_alias=AliasChoices("NEWSAPI_BASE_URL"))
    newsapi_timeout_seconds: float = Field(default=15.0, validation_alias=AliasChoices("NEWSAPI_TIMEOUT_SECONDS"))
    newsapi_match_limit: int = Field(default=12, validation_alias=AliasChoices("NEWSAPI_MATCH_LIMIT"))
    newsapi_articles_per_match: int = Field(default=6, validation_alias=AliasChoices("NEWSAPI_ARTICLES_PER_MATCH"))
    newsapi_lookback_hours: int = Field(default=72, validation_alias=AliasChoices("NEWSAPI_LOOKBACK_HOURS"))
    newsapi_per_run_max: int = Field(default=1, validation_alias=AliasChoices("NEWSAPI_PER_RUN_MAX", "NEWSAPI_MAX_HTTP_REQUESTS_PER_RUN"))
    currents_news_per_run_max: int = Field(default=3, validation_alias=AliasChoices("CURRENTS_NEWS_PER_RUN_MAX", "CURRENTS_NEWS_MAX_HTTP_REQUESTS_PER_RUN", "CURRENTS_PER_RUN_MAX"))
    news_context_cache_ttl_minutes: int = Field(default=180, validation_alias=AliasChoices("NEWS_CONTEXT_CACHE_TTL_MINUTES"))

    gnews_key: str | None = Field(default=None, validation_alias=AliasChoices("GNEWS_KEY"))
    gnews_base_url: str = Field(default="https://gnews.io/api/v4", validation_alias=AliasChoices("GNEWS_BASE_URL"))
    gnews_timeout_seconds: float = Field(default=15.0, validation_alias=AliasChoices("GNEWS_TIMEOUT_SECONDS"))
    gnews_match_limit: int = Field(default=12, validation_alias=AliasChoices("GNEWS_MATCH_LIMIT"))
    gnews_articles_per_match: int = Field(default=6, validation_alias=AliasChoices("GNEWS_ARTICLES_PER_MATCH"))
    gnews_lookback_hours: int = Field(default=72, validation_alias=AliasChoices("GNEWS_LOOKBACK_HOURS"))
    gnews_per_run_max: int = Field(default=1, validation_alias=AliasChoices("GNEWS_PER_RUN_MAX", "GNEWS_MAX_HTTP_REQUESTS_PER_RUN"))
    gnews_rate_limit_cooldown_minutes: int = Field(default=180, validation_alias=AliasChoices("GNEWS_RATE_LIMIT_COOLDOWN_MINUTES"))

    enable_oddspapi: bool = Field(default=False, validation_alias=AliasChoices("ENABLE_ODDSPAPI", "ODDSPAPI_ENABLED"))
    oddspapi_api_key: str | None = Field(default=None, validation_alias=AliasChoices("ODDSPAPI_API_KEY"))
    oddspapi_base_url: str = Field(default="https://api.oddspapi.io/v4", validation_alias=AliasChoices("ODDSPAPI_BASE_URL"))
    oddspapi_timeout_seconds: float = Field(default=12.0, validation_alias=AliasChoices("ODDSPAPI_TIMEOUT_SECONDS"))
    oddspapi_min_fetch_interval_minutes: int = Field(default=360, validation_alias=AliasChoices("ODDSPAPI_MIN_FETCH_INTERVAL_MINUTES"))
    oddspapi_match_limit: int = Field(default=16, validation_alias=AliasChoices("ODDSPAPI_MATCH_LIMIT"))
    oddspapi_tournament_limit: int = Field(default=4, validation_alias=AliasChoices("ODDSPAPI_TOURNAMENT_LIMIT"))
    oddspapi_bookmakers: CsvList = Field(
        default_factory=lambda: ["bet365", "unibet", "pinnacle", "betfair", "williamhill", "1xbet", "bwin"],
        validation_alias=AliasChoices("ODDSPAPI_BOOKMAKERS"),
    )

    enable_allsportsapi: bool = Field(default=False, validation_alias=AliasChoices("ENABLE_ALLSPORTSAPI", "ALLSPORTSAPI_ENABLED"))
    allsportsapi_api_key: str | None = Field(default=None, validation_alias=AliasChoices("ALLSPORTSAPI_API_KEY"))
    allsportsapi_base_url: str = Field(default="https://apiv2.allsportsapi.com/football/", validation_alias=AliasChoices("ALLSPORTSAPI_BASE_URL"))
    allsportsapi_timeout_seconds: float = Field(default=12.0, validation_alias=AliasChoices("ALLSPORTSAPI_TIMEOUT_SECONDS"))
    allsportsapi_min_fetch_interval_minutes: int = Field(default=120, validation_alias=AliasChoices("ALLSPORTSAPI_MIN_FETCH_INTERVAL_MINUTES"))
    allsportsapi_match_limit: int = Field(default=12, validation_alias=AliasChoices("ALLSPORTSAPI_MATCH_LIMIT"))
    allsportsapi_bookmakers: CsvList = Field(
        default_factory=lambda: ["Bet365", "Unibet", "Pinnacle", "Betfair", "William Hill", "1xBet", "Bwin"],
        validation_alias=AliasChoices("ALLSPORTSAPI_BOOKMAKERS"),
    )

    enable_futrixmetrics_context: bool = Field(default=False, validation_alias=AliasChoices("ENABLE_FUTRIXMETRICS_CONTEXT", "FUTRIXMETRICS_ENABLED"))
    futrixmetrics_api_key: str | None = Field(default=None, validation_alias=AliasChoices("FUTRIXMETRICS_API_KEY"))
    futrixmetrics_base_url: str = Field(default="https://footballperformanceapi.site", validation_alias=AliasChoices("FUTRIXMETRICS_BASE_URL"))
    futrixmetrics_timeout_seconds: float = Field(default=12.0, validation_alias=AliasChoices("FUTRIXMETRICS_TIMEOUT_SECONDS"))
    futrixmetrics_context_match_limit: int = Field(default=6, validation_alias=AliasChoices("FUTRIXMETRICS_CONTEXT_MATCH_LIMIT"))
    futrixmetrics_team_cache_ttl_hours: int = Field(default=168, validation_alias=AliasChoices("FUTRIXMETRICS_TEAM_CACHE_TTL_HOURS"))
    futrixmetrics_limit_per_team: int = Field(default=80, validation_alias=AliasChoices("FUTRIXMETRICS_LIMIT_PER_TEAM"))

    openfootball_base_url: str = Field(default="https://raw.githubusercontent.com/openfootball/football.json/master", validation_alias=AliasChoices("OPENFOOTBALL_BASE_URL"))
    openfootball_timeout_seconds: float = Field(default=15.0, validation_alias=AliasChoices("OPENFOOTBALL_TIMEOUT_SECONDS"))
    openfootball_dataset_limit: int = Field(default=12, validation_alias=AliasChoices("OPENFOOTBALL_DATASET_LIMIT"))
    openfootball_match_limit: int = Field(default=24, validation_alias=AliasChoices("OPENFOOTBALL_MATCH_LIMIT"))
    openfootball_competition_map: CsvList = Field(
        default_factory=lambda: [
            "english premier league=en.1",
            "english league championship=en.2",
            "english league one=en.3",
            "english league two=en.4",
            "german bundesliga=de.1",
            "german bundesliga 2=de.2",
            "3 liga=de.3",
            "spanish la liga=es.1",
            "spanish la liga 2=es.2",
            "italian serie a=it.1",
            "italian serie b=it.2",
            "italian serie c=it.3",
            "french ligue 1=fr.1",
            "french ligue 2=fr.2",
            "dutch eredivisie=nl.1",
            "portuguese primeira liga=pt.1",
            "belgian pro league=be.1",
            "scottish premiership=sco.1",
            "danish superliga=dk.1",
            "norway 1st division=no.2",
            "norwegian eliteserien=no.1",
            "swedish allsvenskan=se.1",
            "turkish super lig=tr.1",
            "greece super league=gr.1",
            "greece - super league=gr.1",
            "greek super league=gr.1",
            "switzerland super league=ch.1",
            "switzerland - super league=ch.1",
            "switzerland challenge league=ch.2",
            "switzerland - challenge league=ch.2",
            "italy - serie c=it.3",
            "italy - serie c, group a=it.3",
            "italy - serie c, group b=it.3",
            "italy - serie c, group c=it.3",
            "england - championship=en.2",
            "england - premier league=en.1",
            "norway - eliteserien=no.1",
            "norway - 1st division=no.2",
        ],
        validation_alias=AliasChoices("OPENFOOTBALL_COMPETITION_MAP"),
    )

    openligadb_base_url: str = Field(default="https://api.openligadb.de", validation_alias=AliasChoices("OPENLIGADB_BASE_URL"))
    openligadb_timeout_seconds: float = Field(default=12.0, validation_alias=AliasChoices("OPENLIGADB_TIMEOUT_SECONDS"))
    openligadb_dataset_limit: int = Field(default=8, validation_alias=AliasChoices("OPENLIGADB_DATASET_LIMIT"))
    openligadb_match_limit: int = Field(default=24, validation_alias=AliasChoices("OPENLIGADB_MATCH_LIMIT"))
    openligadb_competition_map: CsvList = Field(
        default_factory=lambda: [
            "german bundesliga=bl1",
            "germany bundesliga=bl1",
            "1. bundesliga=bl1",
            "german bundesliga 2=bl2",
            "germany bundesliga 2=bl2",
            "2. bundesliga=bl2",
            "german 3 liga=bl3",
            "germany 3 liga=bl3",
            "3. liga=bl3",
            "uefa champions league=ucl",
            "champions league=ucl",
            "dfb pokal=dfb",
        ],
        validation_alias=AliasChoices("OPENLIGADB_COMPETITION_MAP"),
    )

    bzzoiro_api_key: str | None = Field(default=None, validation_alias=AliasChoices("BZZOIRO_API_KEY"))
    bzzoiro_timeout_seconds: float = Field(default=20.0, validation_alias=AliasChoices("BZZOIRO_TIMEOUT_SECONDS"))
    bzzoiro_max_pages: int = Field(default=8, validation_alias=AliasChoices("BZZOIRO_MAX_PAGES"))
    bzzoiro_per_run_max: int = Field(default=70, validation_alias=AliasChoices("BZZOIRO_PER_RUN_MAX", "BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN"))
    bzzoiro_context_match_limit: int = Field(default=80, validation_alias=AliasChoices("BZZOIRO_CONTEXT_MATCH_LIMIT"))

    weatherapi_key: str | None = Field(default=None, validation_alias=AliasChoices("WEATHERAPI_KEY"))
    openweathermap_api_key: str | None = Field(default=None, validation_alias=AliasChoices("OPENWEATHERMAP_API_KEY", "OPENWEATHER_API_KEY", "OPENWEATHERMAP_KEY"))
    weather_context_enabled: bool = Field(default=True, validation_alias=AliasChoices("WEATHER_CONTEXT_ENABLED"))
    weather_context_match_limit: int = Field(default=8, validation_alias=AliasChoices("WEATHER_CONTEXT_MATCH_LIMIT"))
    weather_timeout_seconds: float = Field(default=8.0, validation_alias=AliasChoices("WEATHER_TIMEOUT_SECONDS"))
    weather_cache_ttl_minutes: int = Field(default=360, validation_alias=AliasChoices("WEATHER_CACHE_TTL_MINUTES"))
    weatherapi_per_run_max: int = Field(default=4, validation_alias=AliasChoices("WEATHERAPI_PER_RUN_MAX", "WEATHERAPI_MAX_HTTP_REQUESTS_PER_RUN"))
    openweathermap_per_run_max: int = Field(default=2, validation_alias=AliasChoices("OPENWEATHERMAP_PER_RUN_MAX", "OPENWEATHERMAP_MAX_HTTP_REQUESTS_PER_RUN"))

    signal_weight_explicit: float = Field(default=0.40, validation_alias=AliasChoices("SIGNAL_WEIGHT_EXPLICIT"))
    signal_weight_xg: float = Field(default=0.34, validation_alias=AliasChoices("SIGNAL_WEIGHT_XG"))
    signal_weight_strength: float = Field(default=0.16, validation_alias=AliasChoices("SIGNAL_WEIGHT_STRENGTH"))
    signal_weight_momentum: float = Field(default=0.10, validation_alias=AliasChoices("SIGNAL_WEIGHT_MOMENTUM"))
    signal_weight_injuries: float = Field(default=0.07, validation_alias=AliasChoices("SIGNAL_WEIGHT_INJURIES"))

    espn_query_all_allowed_when_unmapped: bool = Field(default=True, validation_alias=AliasChoices("ESPN_QUERY_ALL_ALLOWED_WHEN_UNMAPPED"))
    espn_enable_injuries: bool = Field(default=True, validation_alias=AliasChoices("ESPN_ENABLE_INJURIES"))
    espn_enable_news_context: bool = Field(default=True, validation_alias=AliasChoices("ESPN_ENABLE_NEWS_CONTEXT"))
    espn_injury_out_weight: float = Field(default=1.00, validation_alias=AliasChoices("ESPN_INJURY_OUT_WEIGHT"))
    espn_injury_doubtful_weight: float = Field(default=0.45, validation_alias=AliasChoices("ESPN_INJURY_DOUBTFUL_WEIGHT"))
    espn_injury_suspension_weight: float = Field(default=0.85, validation_alias=AliasChoices("ESPN_INJURY_SUSPENSION_WEIGHT"))
    espn_injury_questionable_weight: float = Field(default=0.35, validation_alias=AliasChoices("ESPN_INJURY_QUESTIONABLE_WEIGHT"))
    espn_news_absence_penalty_per_point: float = Field(default=0.05, validation_alias=AliasChoices("ESPN_NEWS_ABSENCE_PENALTY_PER_POINT"))

    thesportsdb_team_alias_relaxed: bool = Field(default=True, validation_alias=AliasChoices("THESPORTSDB_TEAM_ALIAS_RELAXED"))

    bookies_api_enabled: bool = Field(default=False, validation_alias=AliasChoices("BOOKIES_API_ENABLED"))
    bookies_api_login: str | None = Field(default=None, validation_alias=AliasChoices("BOOKIES_API_LOGIN"))
    bookies_api_token: str | None = Field(default=None, validation_alias=AliasChoices("BOOKIES_API_TOKEN"))
    bookies_api_key: str | None = Field(default=None, validation_alias=AliasChoices("BOOKIES_API_KEY"))
    bookies_api_base_url: str = Field(default="https://bookiesapi.com/api/get.php", validation_alias=AliasChoices("BOOKIES_API_BASE_URL"))
    bookies_api_odds_task: str = Field(default="odds", validation_alias=AliasChoices("BOOKIES_API_ODDS_TASK"))
    bookies_api_odds_fetch_limit: int = Field(default=40, validation_alias=AliasChoices("BOOKIES_API_ODDS_FETCH_LIMIT"))
    bookies_api_page_limit: int = Field(default=50, validation_alias=AliasChoices("BOOKIES_API_PAGE_LIMIT"))
    bookies_api_max_pages_per_day: int = Field(default=10, validation_alias=AliasChoices("BOOKIES_API_MAX_PAGES_PER_DAY"))
    bookies_api_use_for_backfill_only: bool = Field(default=True, validation_alias=AliasChoices("BOOKIES_API_USE_FOR_BACKFILL_ONLY"))
    bookies_api_timeout_seconds: float = Field(default=25.0, validation_alias=AliasChoices("BOOKIES_API_TIMEOUT_SECONDS", "BOOKIES_API_TIMEOUT_MS"))

    max_matches_for_odds_fetch: int = Field(default=260, validation_alias=AliasChoices("MAX_MATCHES_FOR_ODDS_FETCH", "MAX_MATCHES_FOR_PRICING"))
    match_bootstrap_provider: str = Field(default="odds_api_io", validation_alias=AliasChoices("MATCH_BOOTSTRAP_PROVIDER"))
    bootstrap_fallback_to_bookies: bool = Field(default=True, validation_alias=AliasChoices("BOOTSTRAP_FALLBACK_TO_BOOKIES"))
    bootstrap_fallback_to_context: bool = Field(default=True, validation_alias=AliasChoices("BOOTSTRAP_FALLBACK_TO_CONTEXT"))

    source_weight_theodds: float = Field(default=1.04, validation_alias=AliasChoices("SOURCE_WEIGHT_THEODDS"))
    source_weight_oddsapiio: float = Field(default=1.00, validation_alias=AliasChoices("SOURCE_WEIGHT_ODDSAPIIO"))
    source_weight_bookiesapi: float = Field(default=0.98, validation_alias=AliasChoices("SOURCE_WEIGHT_BOOKIESAPI"))
    source_weight_sstats: float = Field(default=0.90, validation_alias=AliasChoices("SOURCE_WEIGHT_SSTATS"))
    source_weight_oddspapi: float = Field(default=1.00, validation_alias=AliasChoices("SOURCE_WEIGHT_ODDSPAPI"))
    source_weight_allsportsapi: float = Field(default=0.99, validation_alias=AliasChoices("SOURCE_WEIGHT_ALLSPORTSAPI"))
    source_weight_futrixmetrics: float = Field(default=0.90, validation_alias=AliasChoices("SOURCE_WEIGHT_FUTRIXMETRICS"))

    bookmaker_weight_pinnacle: float = Field(default=1.16, validation_alias=AliasChoices("BOOKMAKER_WEIGHT_PINNACLE"))
    bookmaker_weight_betfair: float = Field(default=1.12, validation_alias=AliasChoices("BOOKMAKER_WEIGHT_BETFAIR"))
    bookmaker_weight_bet365: float = Field(default=1.08, validation_alias=AliasChoices("BOOKMAKER_WEIGHT_BET365"))
    bookmaker_weight_unibet: float = Field(default=1.03, validation_alias=AliasChoices("BOOKMAKER_WEIGHT_UNIBET"))

    h2h_score_weight: float = Field(default=1.00, validation_alias=AliasChoices("H2H_SCORE_WEIGHT"))
    totals_score_weight: float = Field(default=0.96, validation_alias=AliasChoices("TOTALS_SCORE_WEIGHT"))
    spreads_score_weight: float = Field(default=1.15, validation_alias=AliasChoices("SPREADS_SCORE_WEIGHT"))
    dnb_score_weight: float = Field(default=1.00, validation_alias=AliasChoices("DNB_SCORE_WEIGHT"))
    double_chance_score_weight: float = Field(default=0.82, validation_alias=AliasChoices("DOUBLE_CHANCE_SCORE_WEIGHT"))
    btts_score_weight: float = Field(default=1.12, validation_alias=AliasChoices("BTTS_SCORE_WEIGHT"))
    team_totals_score_weight: float = Field(default=1.20, validation_alias=AliasChoices("TEAM_TOTALS_SCORE_WEIGHT"))

    max_picks_per_league: int = Field(default=3, validation_alias=AliasChoices("MAX_PICKS_PER_LEAGUE"))
    max_picks_per_family: int = Field(default=3, validation_alias=AliasChoices("MAX_PICKS_PER_FAMILY"))
    max_same_reason_signature: int = Field(default=2, validation_alias=AliasChoices("MAX_SAME_REASON_SIGNATURE"))

    reject_negative_expected_goals: bool = Field(default=True, validation_alias=AliasChoices("REJECT_NEGATIVE_EXPECTED_GOALS"))
    min_expected_goals_value: float = Field(default=0.15, validation_alias=AliasChoices("MIN_EXPECTED_GOALS_VALUE"))
    max_expected_goals_value: float = Field(default=4.80, validation_alias=AliasChoices("MAX_EXPECTED_GOALS_VALUE"))

    market_monitor_enabled: bool = Field(default=True, validation_alias=AliasChoices("MARKET_MONITOR_ENABLED"))
    clv_tracking_enabled: bool = Field(default=True, validation_alias=AliasChoices("CLV_TRACKING_ENABLED"))
    closing_line_monitor_enabled: bool = Field(default=True, validation_alias=AliasChoices("CLOSING_LINE_MONITOR_ENABLED"))
    line_movement_signal_enabled: bool = Field(default=True, validation_alias=AliasChoices("LINE_MOVEMENT_SIGNAL_ENABLED"))
    line_movement_min_delta_pct: float = Field(default=1.75, validation_alias=AliasChoices("LINE_MOVEMENT_MIN_DELTA_PCT"))
    line_movement_confidence_bonus: float = Field(default=4.0, validation_alias=AliasChoices("LINE_MOVEMENT_CONFIDENCE_BONUS"))
    line_movement_confidence_penalty: float = Field(default=3.0, validation_alias=AliasChoices("LINE_MOVEMENT_CONFIDENCE_PENALTY"))
    max_consensus_dispersion_pct: float = Field(default=6.5, validation_alias=AliasChoices("MAX_CONSENSUS_DISPERSION_PCT"))
    consensus_tight_confidence_bonus: float = Field(default=2.0, validation_alias=AliasChoices("CONSENSUS_TIGHT_CONFIDENCE_BONUS"))
    confidence_gap_bonus_weight: float = Field(default=0.10, validation_alias=AliasChoices("CONFIDENCE_GAP_BONUS_WEIGHT"))
    confidence_books_bonus: float = Field(default=0.90, validation_alias=AliasChoices("CONFIDENCE_BOOKS_BONUS"))
    confidence_sources_bonus: float = Field(default=1.10, validation_alias=AliasChoices("CONFIDENCE_SOURCES_BONUS"))
    confidence_price_premium_bonus: float = Field(default=0.08, validation_alias=AliasChoices("CONFIDENCE_PRICE_PREMIUM_BONUS"))
    confidence_dispersion_penalty_weight: float = Field(default=0.18, validation_alias=AliasChoices("CONFIDENCE_DISPERSION_PENALTY_WEIGHT"))
    market_snapshot_history_limit: int = Field(default=96, validation_alias=AliasChoices("MARKET_SNAPSHOT_HISTORY_LIMIT"))
    clv_resolve_grace_minutes: int = Field(default=45, validation_alias=AliasChoices("CLV_RESOLVE_GRACE_MINUTES"))
    market_monitor_subdir: str = Field(default="market-monitor", validation_alias=AliasChoices("MARKET_MONITOR_SUBDIR"))
    line_movement_requires_history: bool = Field(default=True, validation_alias=AliasChoices("LINE_MOVEMENT_REQUIRES_HISTORY"))
    line_movement_min_books: int = Field(default=2, validation_alias=AliasChoices("LINE_MOVEMENT_MIN_BOOKS"))
    line_movement_min_sources: int = Field(default=1, validation_alias=AliasChoices("LINE_MOVEMENT_MIN_SOURCES"))
    line_movement_negative_penalty_factor: float = Field(default=0.5, validation_alias=AliasChoices("LINE_MOVEMENT_NEGATIVE_PENALTY_FACTOR"))

    espn_allow_partial_context: bool = Field(default=True, validation_alias=AliasChoices("ESPN_ALLOW_PARTIAL_CONTEXT"))
    espn_soft_fail_statuses: CsvList = Field(default_factory=lambda: ["400", "404"], validation_alias=AliasChoices("ESPN_SOFT_FAIL_STATUSES"))
    thesportsdb_allow_partial_context: bool = Field(default=True, validation_alias=AliasChoices("THESPORTSDB_ALLOW_PARTIAL_CONTEXT"))
    thesportsdb_partial_match_threshold: float = Field(default=0.52, validation_alias=AliasChoices("THESPORTSDB_PARTIAL_MATCH_THRESHOLD"))
    thesportsdb_league_match_threshold: float = Field(default=0.60, validation_alias=AliasChoices("THESPORTSDB_LEAGUE_MATCH_THRESHOLD"))

    fallback_publish_mode_enabled: bool = Field(default=False, validation_alias=AliasChoices("FALLBACK_PUBLISH_MODE_ENABLED"))
    fallback_publish_min_ev_pct: float = Field(default=2.0, validation_alias=AliasChoices("FALLBACK_PUBLISH_MIN_EV_PCT"))
    fallback_publish_min_edge_pct: float = Field(default=2.5, validation_alias=AliasChoices("FALLBACK_PUBLISH_MIN_EDGE_PCT"))
    fallback_publish_min_confidence: float = Field(default=54.0, validation_alias=AliasChoices("FALLBACK_PUBLISH_MIN_CONFIDENCE"))
    fallback_publish_min_books: int = Field(default=2, validation_alias=AliasChoices("FALLBACK_PUBLISH_MIN_BOOKS"))
    model_relaxed_fallback_enabled: bool = Field(default=False, validation_alias=AliasChoices("MODEL_RELAXED_FALLBACK_ENABLED"))
    model_relaxed_fallback_min_ev_pct: float = Field(default=0.6, validation_alias=AliasChoices("MODEL_RELAXED_FALLBACK_MIN_EV_PCT"))
    model_relaxed_fallback_min_edge_pct: float = Field(default=0.8, validation_alias=AliasChoices("MODEL_RELAXED_FALLBACK_MIN_EDGE_PCT"))
    model_relaxed_fallback_min_confidence: float = Field(default=52.0, validation_alias=AliasChoices("MODEL_RELAXED_FALLBACK_MIN_CONFIDENCE"))
    model_relaxed_fallback_min_books: int = Field(default=2, validation_alias=AliasChoices("MODEL_RELAXED_FALLBACK_MIN_BOOKS"))
    force_publish_when_empty_enabled: bool = Field(default=False, validation_alias=AliasChoices("FORCE_PUBLISH_WHEN_EMPTY_ENABLED"))
    force_publish_when_empty_min_confidence: float = Field(default=49.0, validation_alias=AliasChoices("FORCE_PUBLISH_WHEN_EMPTY_MIN_CONFIDENCE"))
    force_publish_when_empty_min_ev_pct: float = Field(default=0.0, validation_alias=AliasChoices("FORCE_PUBLISH_WHEN_EMPTY_MIN_EV_PCT"))
    force_publish_when_empty_min_edge_pct: float = Field(default=0.0, validation_alias=AliasChoices("FORCE_PUBLISH_WHEN_EMPTY_MIN_EDGE_PCT"))
    force_publish_when_empty_min_books: int = Field(default=2, validation_alias=AliasChoices("FORCE_PUBLISH_WHEN_EMPTY_MIN_BOOKS"))
    quality_emergency_publish_enabled: bool = Field(default=False, validation_alias=AliasChoices("QUALITY_EMERGENCY_PUBLISH_ENABLED"))
    quality_emergency_min_confidence: float = Field(default=50.0, validation_alias=AliasChoices("QUALITY_EMERGENCY_MIN_CONFIDENCE"))
    quality_emergency_min_ev_pct: float = Field(default=0.4, validation_alias=AliasChoices("QUALITY_EMERGENCY_MIN_EV_PCT"))
    quality_emergency_min_edge_pct: float = Field(default=0.6, validation_alias=AliasChoices("QUALITY_EMERGENCY_MIN_EDGE_PCT"))
    quality_emergency_min_books: int = Field(default=2, validation_alias=AliasChoices("QUALITY_EMERGENCY_MIN_BOOKS"))
    quality_last_resort_publish_enabled: bool = Field(default=False, validation_alias=AliasChoices("QUALITY_LAST_RESORT_PUBLISH_ENABLED"))
    historical_segment_relief_enabled: bool = Field(default=False, validation_alias=AliasChoices("HISTORICAL_SEGMENT_RELIEF_ENABLED"))
    historical_segment_relief_min_confidence: float = Field(default=62.0, validation_alias=AliasChoices("HISTORICAL_SEGMENT_RELIEF_MIN_CONFIDENCE"))
    historical_segment_relief_min_ev_pct: float = Field(default=2.0, validation_alias=AliasChoices("HISTORICAL_SEGMENT_RELIEF_MIN_EV_PCT"))
    historical_segment_relief_min_edge_pct: float = Field(default=3.0, validation_alias=AliasChoices("HISTORICAL_SEGMENT_RELIEF_MIN_EDGE_PCT"))
    historical_segment_relief_edge_tolerance_pp: float = Field(default=0.35, validation_alias=AliasChoices("HISTORICAL_SEGMENT_RELIEF_EDGE_TOLERANCE_PP"))
    historical_segment_relief_min_books: int = Field(default=2, validation_alias=AliasChoices("HISTORICAL_SEGMENT_RELIEF_MIN_BOOKS"))
    historical_segment_relief_min_sources: int = Field(default=2, validation_alias=AliasChoices("HISTORICAL_SEGMENT_RELIEF_MIN_SOURCES"))
    historical_segment_relief_min_context_sources: int = Field(default=2, validation_alias=AliasChoices("HISTORICAL_SEGMENT_RELIEF_MIN_CONTEXT_SOURCES"))
    historical_segment_relief_min_publication_score: float = Field(default=24.0, validation_alias=AliasChoices("HISTORICAL_SEGMENT_RELIEF_MIN_PUBLICATION_SCORE"))
    min_quality_score_publish: float = Field(default=57.0, validation_alias=AliasChoices("MIN_QUALITY_SCORE_PUBLISH"))
    quality_fallback_min_books_strict: int = Field(default=2, validation_alias=AliasChoices("QUALITY_FALLBACK_MIN_BOOKS_STRICT"))
    quality_totals_fallback_enabled: bool = Field(default=True, validation_alias=AliasChoices("QUALITY_TOTALS_FALLBACK_ENABLED"))
    quality_totals_fallback_min_confidence: float = Field(default=58.0, validation_alias=AliasChoices("QUALITY_TOTALS_FALLBACK_MIN_CONFIDENCE"))
    quality_totals_fallback_min_ev_pct: float = Field(default=1.0, validation_alias=AliasChoices("QUALITY_TOTALS_FALLBACK_MIN_EV_PCT"))
    quality_totals_fallback_min_edge_pct: float = Field(default=1.5, validation_alias=AliasChoices("QUALITY_TOTALS_FALLBACK_MIN_EDGE_PCT"))
    quality_totals_fallback_min_books: int = Field(default=2, validation_alias=AliasChoices("QUALITY_TOTALS_FALLBACK_MIN_BOOKS"))
    quality_totals_fallback_under_min_xg_headroom: float = Field(default=0.06, validation_alias=AliasChoices("QUALITY_TOTALS_FALLBACK_UNDER_MIN_XG_HEADROOM"))
    quality_totals_fallback_over_min_xg_headroom: float = Field(default=0.16, validation_alias=AliasChoices("QUALITY_TOTALS_FALLBACK_OVER_MIN_XG_HEADROOM"))
    post_calibration_probability_relief_max_gap: float = Field(default=0.09, validation_alias=AliasChoices("POST_CALIBRATION_PROBABILITY_RELIEF_MAX_GAP"))
    post_calibration_probability_relief_min_confidence: float = Field(default=58.0, validation_alias=AliasChoices("POST_CALIBRATION_PROBABILITY_RELIEF_MIN_CONFIDENCE"))
    post_calibration_probability_relief_min_edge_pct: float = Field(default=5.0, validation_alias=AliasChoices("POST_CALIBRATION_PROBABILITY_RELIEF_MIN_EDGE_PCT"))
    post_calibration_probability_relief_min_ev_pct: float = Field(default=2.0, validation_alias=AliasChoices("POST_CALIBRATION_PROBABILITY_RELIEF_MIN_EV_PCT"))
    post_calibration_probability_relief_min_books: int = Field(default=1, validation_alias=AliasChoices("POST_CALIBRATION_PROBABILITY_RELIEF_MIN_BOOKS"))
    post_calibration_core_relief_enabled: bool = Field(default=True, validation_alias=AliasChoices("POST_CALIBRATION_CORE_RELIEF_ENABLED"))
    post_calibration_core_relief_max_gap: float = Field(default=0.12, validation_alias=AliasChoices("POST_CALIBRATION_CORE_RELIEF_MAX_GAP"))
    post_calibration_core_relief_min_confidence: float = Field(default=58.0, validation_alias=AliasChoices("POST_CALIBRATION_CORE_RELIEF_MIN_CONFIDENCE"))
    post_calibration_core_relief_min_edge_pct: float = Field(default=2.8, validation_alias=AliasChoices("POST_CALIBRATION_CORE_RELIEF_MIN_EDGE_PCT"))
    post_calibration_core_relief_min_ev_pct: float = Field(default=1.1, validation_alias=AliasChoices("POST_CALIBRATION_CORE_RELIEF_MIN_EV_PCT"))
    post_calibration_core_relief_min_books: int = Field(default=2, validation_alias=AliasChoices("POST_CALIBRATION_CORE_RELIEF_MIN_BOOKS"))
    post_calibration_core_relief_totals_under_min_xg_headroom: float = Field(default=0.06, validation_alias=AliasChoices("POST_CALIBRATION_CORE_RELIEF_TOTALS_UNDER_MIN_XG_HEADROOM"))
    post_calibration_core_relief_totals_over_min_xg_headroom: float = Field(default=0.16, validation_alias=AliasChoices("POST_CALIBRATION_CORE_RELIEF_TOTALS_OVER_MIN_XG_HEADROOM"))

    min_publication_score: float = Field(default=12.0, validation_alias=AliasChoices("MIN_PUBLICATION_SCORE"))
    min_publication_score_secondary_league: float = Field(default=14.5, validation_alias=AliasChoices("MIN_PUBLICATION_SCORE_SECONDARY_LEAGUE"))
    min_publication_score_other_league: float = Field(default=18.0, validation_alias=AliasChoices("MIN_PUBLICATION_SCORE_OTHER_LEAGUE"))
    min_publication_score_low_tier: float = Field(default=22.0, validation_alias=AliasChoices("MIN_PUBLICATION_SCORE_LOW_TIER"))
    non_core_league_min_books: int = Field(default=1, validation_alias=AliasChoices("NON_CORE_LEAGUE_MIN_BOOKS"))
    non_core_league_min_confidence: float = Field(default=68.0, validation_alias=AliasChoices("NON_CORE_LEAGUE_MIN_CONFIDENCE"))
    non_core_league_min_edge_pct: float = Field(default=7.5, validation_alias=AliasChoices("NON_CORE_LEAGUE_MIN_EDGE_PCT"))
    non_core_league_min_ev_pct: float = Field(default=4.5, validation_alias=AliasChoices("NON_CORE_LEAGUE_MIN_EV_PCT"))
    non_core_league_require_core_context: bool = Field(default=True, validation_alias=AliasChoices("NON_CORE_LEAGUE_REQUIRE_CORE_CONTEXT"))
    prefer_core_leagues_in_shortlist: bool = Field(default=True, validation_alias=AliasChoices("PREFER_CORE_LEAGUES_IN_SHORTLIST"))
    max_non_core_picks_per_run: int = Field(default=1, validation_alias=AliasChoices("MAX_NON_CORE_PICKS_PER_RUN"))
    preferred_league_terms: CsvList = Field(
        default_factory=lambda: ["champions league", "europa league", "conference league", "premier league", "la liga", "laliga", "serie a", "bundesliga", "ligue 1", "eredivisie", "primeira liga", "championship", "world cup", "euro", "nations league"],
        validation_alias=AliasChoices("PREFERRED_LEAGUE_TERMS"),
    )
    secondary_league_terms: CsvList = Field(
        default_factory=lambda: ["libertadores", "sudamericana", "brazil serie a", "argentina primera", "liga mx", "mls", "turkey super lig", "belgium pro league", "scotland premiership", "swiss super league", "austria bundesliga", "croatia hnl", "greece super league", "czech liga", "denmark superliga", "norway eliteserien", "sweden allsvenskan"],
        validation_alias=AliasChoices("SECONDARY_LEAGUE_TERMS"),
    )
    totals_over25_min_weaker_side_xg: float = Field(default=0.68, validation_alias=AliasChoices("TOTALS_OVER25_MIN_WEAKER_SIDE_XG"))
    totals_over25_max_team_xg_share: float = Field(default=0.79, validation_alias=AliasChoices("TOTALS_OVER25_MAX_TEAM_XG_SHARE"))
    totals_over25_dual_threat_guard_enabled: bool = Field(default=True, validation_alias=AliasChoices("TOTALS_OVER25_DUAL_THREAT_GUARD_ENABLED"))

    api_football_fetch_match_dates_only: bool = Field(default=True, validation_alias=AliasChoices("API_FOOTBALL_FETCH_MATCH_DATES_ONLY"))
    api_football_free_days_ahead: int = Field(default=1, validation_alias=AliasChoices("API_FOOTBALL_FREE_DAYS_AHEAD"))

    supported_total_lines: CsvList = Field(
        default_factory=lambda: ["1.5", "2.0", "2.5", "3.0", "3.5", "4.0", "4.5"],
        validation_alias=AliasChoices("SUPPORTED_TOTAL_LINES"),
    )
    supported_team_total_lines: CsvList = Field(
        default_factory=lambda: ["0.5", "1.0", "1.5", "2.0", "2.5", "3.0", "3.5"],
        validation_alias=AliasChoices("SUPPORTED_TEAM_TOTAL_LINES"),
    )
    line_support_tolerance: float = Field(default=0.06, validation_alias=AliasChoices("LINE_SUPPORT_TOLERANCE"))

    totals_min_edge_pct: float = Field(default=2.0, validation_alias=AliasChoices("TOTALS_MIN_EDGE_PCT"))
    totals_min_ev_pct: float = Field(default=1.5, validation_alias=AliasChoices("TOTALS_MIN_EV_PCT"))
    totals_min_model_confidence: float = Field(default=0.54, validation_alias=AliasChoices("TOTALS_MIN_MODEL_CONFIDENCE"))
    totals_min_books: int = Field(default=1, validation_alias=AliasChoices("TOTALS_MIN_BOOKS"))
    totals_over25_min_edge_pct: float = Field(default=6.0, validation_alias=AliasChoices("TOTALS_OVER25_MIN_EDGE_PCT"))
    totals_over25_min_ev_pct: float = Field(default=4.5, validation_alias=AliasChoices("TOTALS_OVER25_MIN_EV_PCT"))
    totals_over25_min_confidence: float = Field(default=66.0, validation_alias=AliasChoices("TOTALS_OVER25_MIN_CONFIDENCE"))
    totals_over25_min_sum_xg: float = Field(default=3.15, validation_alias=AliasChoices("TOTALS_OVER25_MIN_SUM_XG"))
    totals_over25_min_adjusted_probability: float = Field(default=0.51, validation_alias=AliasChoices("TOTALS_OVER25_MIN_ADJUSTED_PROBABILITY"))

    risky_totals_min_edge_pct: float = Field(default=8.0, validation_alias=AliasChoices("RISKY_TOTALS_MIN_EDGE_PCT"))
    risky_totals_min_ev_pct: float = Field(default=6.0, validation_alias=AliasChoices("RISKY_TOTALS_MIN_EV_PCT"))
    risky_totals_min_confidence: float = Field(default=70.0, validation_alias=AliasChoices("RISKY_TOTALS_MIN_CONFIDENCE"))
    risky_totals_min_sum_xg: float = Field(default=3.30, validation_alias=AliasChoices("RISKY_TOTALS_MIN_SUM_XG"))
    risky_totals_min_adjusted_probability: float = Field(default=0.53, validation_alias=AliasChoices("RISKY_TOTALS_MIN_ADJUSTED_PROBABILITY"))
    risky_totals_league_terms: CsvList = Field(
        default_factory=lambda: ["serie c", "primera nacional", "liga de expansion", "u17", "u19", "u20", "u21", "u23", "youth", "academy", "reserve", "reserves", "primavera"],
        validation_alias=AliasChoices("RISKY_TOTALS_LEAGUE_TERMS"),
    )
    risky_totals_team_terms: CsvList = Field(
        default_factory=lambda: ["u17", "u19", "u20", "u21", "u23", "academy", "reserve", "reserves", "primavera"],
        validation_alias=AliasChoices("RISKY_TOTALS_TEAM_TERMS"),
    )

    h2h_min_edge_pct: float = Field(default=2.0, validation_alias=AliasChoices("H2H_MIN_EDGE_PCT"))
    h2h_min_ev_pct: float = Field(default=1.1, validation_alias=AliasChoices("H2H_MIN_EV_PCT"))
    h2h_min_model_confidence: float = Field(default=0.54, validation_alias=AliasChoices("H2H_MIN_MODEL_CONFIDENCE"))
    h2h_min_books: int = Field(default=1, validation_alias=AliasChoices("H2H_MIN_BOOKS"))
    h2h_draw_min_edge_pct: float = Field(default=3.2, validation_alias=AliasChoices("H2H_DRAW_MIN_EDGE_PCT"))
    h2h_draw_min_ev_pct: float = Field(default=2.4, validation_alias=AliasChoices("H2H_DRAW_MIN_EV_PCT"))
    h2h_draw_min_confidence: float = Field(default=61.0, validation_alias=AliasChoices("H2H_DRAW_MIN_CONFIDENCE"))
    h2h_side_min_edge_pct: float = Field(default=2.0, validation_alias=AliasChoices("H2H_SIDE_MIN_EDGE_PCT"))
    h2h_side_min_ev_pct: float = Field(default=1.2, validation_alias=AliasChoices("H2H_SIDE_MIN_EV_PCT"))
    h2h_side_min_confidence: float = Field(default=51.0, validation_alias=AliasChoices("H2H_SIDE_MIN_CONFIDENCE"))
    h2h_single_source_min_edge_pct: float = Field(default=3.0, validation_alias=AliasChoices("H2H_SINGLE_SOURCE_MIN_EDGE_PCT"))
    h2h_single_source_min_ev_pct: float = Field(default=1.8, validation_alias=AliasChoices("H2H_SINGLE_SOURCE_MIN_EV_PCT"))
    h2h_single_source_min_confidence: float = Field(default=58.0, validation_alias=AliasChoices("H2H_SINGLE_SOURCE_MIN_CONFIDENCE"))
    quality_high_odds_h2h_single_source_guard_enabled: bool = Field(default=True, validation_alias=AliasChoices("QUALITY_HIGH_ODDS_H2H_SINGLE_SOURCE_GUARD_ENABLED"))
    quality_high_odds_h2h_single_source_min_odds: float = Field(default=2.8, validation_alias=AliasChoices("QUALITY_HIGH_ODDS_H2H_SINGLE_SOURCE_MIN_ODDS"))
    quality_high_odds_h2h_single_source_max_sources: int = Field(default=1, validation_alias=AliasChoices("QUALITY_HIGH_ODDS_H2H_SINGLE_SOURCE_MAX_SOURCES"))
    quality_high_odds_h2h_single_source_max_books: int = Field(default=2, validation_alias=AliasChoices("QUALITY_HIGH_ODDS_H2H_SINGLE_SOURCE_MAX_BOOKS"))
    quality_high_odds_h2h_single_source_min_shrink_pp: float = Field(default=9.0, validation_alias=AliasChoices("QUALITY_HIGH_ODDS_H2H_SINGLE_SOURCE_MIN_SHRINK_PP"))
    h2h_xg_dislocation_guard_enabled: bool = Field(default=True, validation_alias=AliasChoices("H2H_XG_DISLOCATION_GUARD_ENABLED"))
    h2h_xg_dislocation_min_diff: float = Field(default=1.60, validation_alias=AliasChoices("H2H_XG_DISLOCATION_MIN_DIFF"))
    h2h_xg_dislocation_market_max_prob: float = Field(default=0.37, validation_alias=AliasChoices("H2H_XG_DISLOCATION_MARKET_MAX_PROB"))
    h2h_xg_dislocation_min_confidence: float = Field(default=66.0, validation_alias=AliasChoices("H2H_XG_DISLOCATION_MIN_CONFIDENCE"))
    h2h_xg_dislocation_min_sources: int = Field(default=2, validation_alias=AliasChoices("H2H_XG_DISLOCATION_MIN_SOURCES"))
    h2h_market_prior_blend_side: float = Field(default=0.16, validation_alias=AliasChoices("H2H_MARKET_PRIOR_BLEND_SIDE"))
    h2h_market_prior_blend_draw: float = Field(default=0.08, validation_alias=AliasChoices("H2H_MARKET_PRIOR_BLEND_DRAW"))
    h2h_draw_probability_cap: float = Field(default=0.34, validation_alias=AliasChoices("H2H_DRAW_PROBABILITY_CAP"))

    spreads_min_edge_pct: float = Field(default=2.2, validation_alias=AliasChoices("SPREADS_MIN_EDGE_PCT"))
    spreads_min_ev_pct: float = Field(default=1.2, validation_alias=AliasChoices("SPREADS_MIN_EV_PCT"))
    spreads_min_model_confidence: float = Field(default=0.55, validation_alias=AliasChoices("SPREADS_MIN_MODEL_CONFIDENCE"))
    spreads_min_books: int = Field(default=1, validation_alias=AliasChoices("SPREADS_MIN_BOOKS"))

    dnb_min_edge_pct: float = Field(default=1.8, validation_alias=AliasChoices("DNB_MIN_EDGE_PCT"))
    dnb_min_ev_pct: float = Field(default=1.3, validation_alias=AliasChoices("DNB_MIN_EV_PCT"))
    dnb_min_model_confidence: float = Field(default=0.54, validation_alias=AliasChoices("DNB_MIN_MODEL_CONFIDENCE"))
    dnb_min_books: int = Field(default=1, validation_alias=AliasChoices("DNB_MIN_BOOKS"))

    double_chance_min_edge_pct: float = Field(default=1.2, validation_alias=AliasChoices("DOUBLE_CHANCE_MIN_EDGE_PCT"))
    double_chance_min_ev_pct: float = Field(default=0.9, validation_alias=AliasChoices("DOUBLE_CHANCE_MIN_EV_PCT"))
    double_chance_min_model_confidence: float = Field(default=0.56, validation_alias=AliasChoices("DOUBLE_CHANCE_MIN_MODEL_CONFIDENCE"))
    double_chance_min_books: int = Field(default=1, validation_alias=AliasChoices("DOUBLE_CHANCE_MIN_BOOKS"))

    btts_min_edge_pct: float = Field(default=1.7, validation_alias=AliasChoices("BTTS_MIN_EDGE_PCT"))
    btts_min_ev_pct: float = Field(default=1.2, validation_alias=AliasChoices("BTTS_MIN_EV_PCT"))
    btts_min_model_confidence: float = Field(default=0.54, validation_alias=AliasChoices("BTTS_MIN_MODEL_CONFIDENCE"))
    btts_min_books: int = Field(default=1, validation_alias=AliasChoices("BTTS_MIN_BOOKS"))
    btts_yes_min_edge_pct: float = Field(default=1.8, validation_alias=AliasChoices("BTTS_YES_MIN_EDGE_PCT"))
    btts_yes_min_ev_pct: float = Field(default=1.3, validation_alias=AliasChoices("BTTS_YES_MIN_EV_PCT"))
    btts_yes_min_confidence: float = Field(default=56.0, validation_alias=AliasChoices("BTTS_YES_MIN_CONFIDENCE"))
    btts_no_min_edge_pct: float = Field(default=1.6, validation_alias=AliasChoices("BTTS_NO_MIN_EDGE_PCT"))
    btts_no_min_ev_pct: float = Field(default=1.1, validation_alias=AliasChoices("BTTS_NO_MIN_EV_PCT"))
    btts_no_min_confidence: float = Field(default=55.0, validation_alias=AliasChoices("BTTS_NO_MIN_CONFIDENCE"))
    btts_market_prior_blend: float = Field(default=0.12, validation_alias=AliasChoices("BTTS_MARKET_PRIOR_BLEND"))
    btts_over25_synergy_weight: float = Field(default=0.08, validation_alias=AliasChoices("BTTS_OVER25_SYNERGY_WEIGHT"))

    team_totals_min_edge_pct: float = Field(default=2.1, validation_alias=AliasChoices("TEAM_TOTALS_MIN_EDGE_PCT"))
    team_totals_min_ev_pct: float = Field(default=1.6, validation_alias=AliasChoices("TEAM_TOTALS_MIN_EV_PCT"))
    team_totals_min_model_confidence: float = Field(default=0.55, validation_alias=AliasChoices("TEAM_TOTALS_MIN_MODEL_CONFIDENCE"))
    team_totals_min_books: int = Field(default=1, validation_alias=AliasChoices("TEAM_TOTALS_MIN_BOOKS"))

    bookmaker_alias_relaxed: bool = Field(default=True, validation_alias=AliasChoices("BOOKMAKER_ALIAS_RELAXED"))
    allow_single_sharp_book: bool = Field(default=True, validation_alias=AliasChoices("ALLOW_SINGLE_SHARP_BOOK"))
    allow_single_target_book: bool = Field(default=True, validation_alias=AliasChoices("ALLOW_SINGLE_TARGET_BOOK"))
    single_sharp_min_confidence: float = Field(default=64.0, validation_alias=AliasChoices("SINGLE_SHARP_MIN_CONFIDENCE"))
    single_sharp_min_edge_pct: float = Field(default=4.0, validation_alias=AliasChoices("SINGLE_SHARP_MIN_EDGE_PCT"))
    preferred_single_book_min_confidence: float = Field(default=60.0, validation_alias=AliasChoices("PREFERRED_SINGLE_BOOK_MIN_CONFIDENCE"))
    preferred_single_book_min_edge_pct: float = Field(default=3.2, validation_alias=AliasChoices("PREFERRED_SINGLE_BOOK_MIN_EDGE_PCT"))
    preferred_single_book_min_ev_pct: float = Field(default=1.4, validation_alias=AliasChoices("PREFERRED_SINGLE_BOOK_MIN_EV_PCT"))
    preferred_single_book_min_publication_score: float = Field(default=9.5, validation_alias=AliasChoices("PREFERRED_SINGLE_BOOK_MIN_PUBLICATION_SCORE"))
    secondary_single_book_min_confidence: float = Field(default=62.0, validation_alias=AliasChoices("SECONDARY_SINGLE_BOOK_MIN_CONFIDENCE"))
    secondary_single_book_min_edge_pct: float = Field(default=3.4, validation_alias=AliasChoices("SECONDARY_SINGLE_BOOK_MIN_EDGE_PCT"))
    secondary_single_book_min_ev_pct: float = Field(default=1.6, validation_alias=AliasChoices("SECONDARY_SINGLE_BOOK_MIN_EV_PCT"))
    secondary_single_book_min_publication_score: float = Field(default=10.5, validation_alias=AliasChoices("SECONDARY_SINGLE_BOOK_MIN_PUBLICATION_SCORE"))
    min_weighted_books_for_consensus: float = Field(default=1.45, validation_alias=AliasChoices("MIN_WEIGHTED_BOOKS_FOR_CONSENSUS"))
    sharp_bookmakers: CsvList = Field(
        default_factory=lambda: ["Pinnacle", "Betfair", "SBOBET", "Marathonbet", "Bet365", "Unibet"],
        validation_alias=AliasChoices("SHARP_BOOKMAKERS"),
    )
    consensus_alias_groups: CsvList = Field(
        default_factory=lambda: [
            "bet365|bet365_4|bet 365",
            "unibet|uni bet|uni-bet",
            "pinnaclesports|pinnacle|pinny",
            "betfair|bet fair|betfair exchange",
            "sbobet|sbo bet",
            "williamhill|william hill",
            "marathonbet|marathon bet",
            "1xbet|1xbetcom|1x bet",
        ],
        validation_alias=AliasChoices("CONSENSUS_ALIAS_GROUPS"),
    )

    @field_validator(
        "run_sports",
        "target_bookmakers",
        "consensus_bookmakers",
        "odds_api_io_bookmakers",
        "odds_api_io_bookmakers_account1",
        "odds_api_io_bookmakers_account2",
        "bookies_api_sports",
        "espn_soccer_leagues",
        "espn_soft_fail_statuses",
        "supported_total_lines",
        "supported_team_total_lines",
        "openfootball_competition_map",
        "openligadb_competition_map",
        "sharp_bookmakers",
        "consensus_alias_groups",
        "risky_totals_league_terms",
        "risky_totals_team_terms",
        "oddspapi_bookmakers",
        "allsportsapi_bookmakers",
        "preferred_league_terms",
        "secondary_league_terms",
        mode="before",
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
        return [item.strip() for item in text.split(",") if item.strip()]

    @field_validator("run_sports")
    @classmethod
    def normalize_sports(cls, value: list[str]) -> list[str]:
        allowed = {"soccer", "basketball", "baseball", "icehockey"}
        result = [item.strip().lower() for item in value if item and item.strip().lower() in allowed]
        return result or ["soccer"]

    @field_validator("match_bootstrap_provider", mode="before")
    @classmethod
    def normalize_match_bootstrap_provider(cls, value: Any) -> str:
        text = str(value or "odds_api_io").strip().lower()
        aliases = {
            "bookies": "bookies_bootstrap",
            "bookies_api": "bookies_bootstrap",
            "bookiesbootstrap": "bookies_bootstrap",
            "bootstrap": "bookies_bootstrap",
            "oddsapiio": "odds_api_io",
            "odds_apiio": "odds_api_io",
            "odds-api-io": "odds_api_io",
        }
        text = aliases.get(text, text)
        return text if text in {"odds_api_io", "bookies_bootstrap", "auto"} else "odds_api_io"

    @field_validator("bookies_api_timeout_seconds", mode="before")
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
            return timezone.utc

    @property
    def telegram_token(self) -> str | None:
        return self.telegram_bot_token

    @property
    def max_matches_for_pricing(self) -> int:
        return self.max_matches_for_odds_fetch

    def source_weight(self, source_name: str) -> float:
        key = str(source_name or "").lower().strip()
        if key == "the_odds_api":
            return self.source_weight_theodds
        if key == "odds_api_io":
            return self.source_weight_oddsapiio
        if key in {"bookies_api", "bookies_bootstrap"}:
            return self.source_weight_bookiesapi
        if key == "sstats":
            return self.source_weight_sstats
        if key == "oddspapi":
            return self.source_weight_oddspapi
        if key == "allsportsapi":
            return self.source_weight_allsportsapi
        if key == "futrixmetrics":
            return self.source_weight_futrixmetrics
        return 1.0

    def bookmaker_weight(self, bookmaker: str) -> float:
        normalized = "".join(ch for ch in str(bookmaker or "").lower() if ch.isalnum())
        if normalized == "pinnacle":
            return self.bookmaker_weight_pinnacle
        if normalized in {"betfair", "betfairexchange"}:
            return self.bookmaker_weight_betfair
        if normalized == "bet365":
            return self.bookmaker_weight_bet365
        if normalized.startswith("unibet"):
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

    def supported_lines_for_family(self, family: str) -> set[float]:
        raw = self.supported_team_total_lines if family == "teamTotals" else self.supported_total_lines
        values: set[float] = set()
        for item in raw or []:
            try:
                values.add(round(float(item), 2))
            except Exception:
                continue
        if values:
            return values
        return {1.5, 2.5, 3.5, 4.5} if family != "teamTotals" else {0.5, 1.5, 2.5}

    def min_edge_pct_for_family(self, family: str) -> float:
        return {
            "totals": self.totals_min_edge_pct,
            "h2h": self.h2h_min_edge_pct,
            "spreads": self.spreads_min_edge_pct,
            "dnb": self.dnb_min_edge_pct,
            "doubleChance": self.double_chance_min_edge_pct,
            "btts": self.btts_min_edge_pct,
            "teamTotals": self.team_totals_min_edge_pct,
        }.get(family, self.min_edge_pct)

    def min_ev_pct_for_family(self, family: str) -> float:
        return {
            "totals": self.totals_min_ev_pct,
            "h2h": self.h2h_min_ev_pct,
            "spreads": self.spreads_min_ev_pct,
            "dnb": self.dnb_min_ev_pct,
            "doubleChance": self.double_chance_min_ev_pct,
            "btts": self.btts_min_ev_pct,
            "teamTotals": self.team_totals_min_ev_pct,
        }.get(family, self.min_ev_pct)

    def min_model_confidence_for_family(self, family: str) -> float:
        return {
            "totals": self.totals_min_model_confidence,
            "h2h": self.h2h_min_model_confidence,
            "spreads": self.spreads_min_model_confidence,
            "dnb": self.dnb_min_model_confidence,
            "doubleChance": self.double_chance_min_model_confidence,
            "btts": self.btts_min_model_confidence,
            "teamTotals": self.team_totals_min_model_confidence,
        }.get(family, self.min_model_confidence)



    def _normalize_league_key(self, league_name: str | None) -> str:
        text = str(league_name or "").strip().lower()
        if not text:
            return ""
        normalized = []
        prev_space = False
        for ch in text:
            if ch.isalnum():
                normalized.append(ch)
                prev_space = False
            else:
                if not prev_space:
                    normalized.append(" ")
                    prev_space = True
        return " ".join("".join(normalized).split())

    def is_preferred_league(self, league_name: str | None) -> bool:
        key = self._normalize_league_key(league_name)
        if not key:
            return False
        return any(self._normalize_league_key(term) in key for term in (self.preferred_league_terms or []))

    def is_secondary_league(self, league_name: str | None) -> bool:
        key = self._normalize_league_key(league_name)
        if not key or self.is_preferred_league(key):
            return False
        return any(self._normalize_league_key(term) in key for term in (self.secondary_league_terms or []))

    def is_low_tier_league(self, league_name: str | None) -> bool:
        key = self._normalize_league_key(league_name)
        if not key:
            return False
        low_tier_terms = (
            "u19", "u20", "u21", "u23", "reserves", "reserve", "women", "youth",
            "amateur", "regional", "division 2", "division 3", "division a", "division b",
            "state league", "county", "cup qualification", "qualification", "qualifying"
        )
        if any(term in key for term in low_tier_terms):
            return True
        return False

    def league_priority_score(self, league_name: str | None) -> float:
        if self.is_preferred_league(league_name):
            return 3.0
        if self.is_secondary_league(league_name):
            return 2.0
        if self.is_low_tier_league(league_name):
            return 0.5
        return 1.0

    def min_books_for_family(self, family: str) -> int:
        return max(
            1,
            int(
                {
                    "totals": self.totals_min_books,
                    "h2h": self.h2h_min_books,
                    "spreads": self.spreads_min_books,
                    "dnb": self.dnb_min_books,
                    "doubleChance": self.double_chance_min_books,
                    "btts": self.btts_min_books,
                    "teamTotals": self.team_totals_min_books,
                }.get(family, self.min_books_publish)
                or 1
            ),
        )


Settings.model_rebuild()


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
