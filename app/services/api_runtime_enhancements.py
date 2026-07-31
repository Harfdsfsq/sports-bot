from __future__ import annotations

import os
from typing import Any, Callable

REMOVED_PROVIDERS: tuple[str, ...] = tuple(
    item.strip().lower()
    for item in str(os.getenv("HARIZON_FORCE_DISABLED_PROVIDERS") or "").split(",")
    if item.strip()
)

PROVIDER_KEY_ENVS: dict[str, tuple[str, ...]] = {
    "odds_api_io": ("ODDS_API_IO_KEY", "ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2"),
    "bookies_api": ("BOOKIES_API_LOGIN", "BOOKIES_API_TOKEN", "BOOKIES_API_KEY"),
    "oddspapi": ("ODDSPAPI_API_KEY", "ODDSPAPI_KEY", "ODDS_PAPI_API_KEY"),
    "allsportsapi": ("ALLSPORTSAPI_API_KEY", "ALLSPORTSAPI_KEY"),
    "sportlogic": ("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN"),
    "api_football": ("API_FOOTBALL_KEY", "API_FOOTBALL_API_KEY"),
    "football_data": ("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY"),
    "thesportsdb": ("THESPORTSDB_API_KEY",),
    "sstats": ("SSTATS_API_KEY",),
    "bzzoiro": ("BZZOIRO_API_KEY",),
    "futrixmetrics": ("FUTRIXMETRICS_API_KEY",),
    "newsapi": ("NEWSAPI_KEY", "CURRENTS_API_KEY", "CURRENTS_KEY"),
    "gnews": ("GNEWS_KEY",),
}

PROVIDER_ENABLE_ENVS: dict[str, tuple[str, ...]] = {
    "odds_api_io": ("ENABLE_ODDS_API_IO", "ODDS_API_IO_ENABLED"),
    "bookies_api": ("BOOKIES_API_ENABLED", "ENABLE_BOOKIES_API"),
    "oddspapi": ("ENABLE_ODDSPAPI", "ODDSPAPI_ENABLED"),
    "allsportsapi": ("ENABLE_ALLSPORTSAPI", "ALLSPORTSAPI_ENABLED"),
    "sportlogic": ("ENABLE_SPORTLOGIC", "SPORTLOGIC_ENABLED"),
    "api_football": ("ENABLE_API_FOOTBALL", "API_FOOTBALL_ENABLED"),
    "football_data": ("ENABLE_FOOTBALL_DATA_CONTEXT", "FOOTBALL_DATA_ENABLED"),
    "thesportsdb": ("ENABLE_THESPORTSDB_CONTEXT", "THESPORTSDB_CONTEXT_ENABLED"),
    "sstats": ("SSTATS_ENABLED", "ENABLE_SSTATS_CONTEXT"),
    "bzzoiro": ("ENABLE_BZZOIRO_CONTEXT", "BZZOIRO_ENABLED"),
    "futrixmetrics": ("ENABLE_FUTRIXMETRICS_CONTEXT", "FUTRIXMETRICS_ENABLED"),
    "newsapi": ("ENABLE_NEWSAPI_CONTEXT", "NEWSAPI_ENABLED"),
    "gnews": ("ENABLE_GNEWS_CONTEXT", "GNEWS_ENABLED"),
}


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _falsey(value: Any) -> bool:
    return str(value if value is not None else "").strip().lower() in {"0", "false", "no", "off", "disabled"}


def _env_present(*names: str) -> bool:
    return any(str(os.getenv(name) or "").strip() for name in names)


def _set_default(name: str, value: str) -> None:
    if not str(os.getenv(name) or "").strip():
        os.environ[name] = value


def _set_if_lower(name: str, minimum: int) -> None:
    try:
        current = int(float(str(os.getenv(name) or 0)))
    except Exception:
        current = 0
    if current < minimum:
        os.environ[name] = str(minimum)


def _set_if_higher(name: str, maximum: int) -> None:
    try:
        current = int(float(str(os.getenv(name) or maximum)))
    except Exception:
        current = maximum
    if current > maximum:
        os.environ[name] = str(maximum)


def _set_float_if_higher(name: str, maximum: float) -> None:
    try:
        current = float(str(os.getenv(name) or maximum))
    except Exception:
        current = maximum
    if current > maximum:
        os.environ[name] = str(maximum)


def _provider_auth_available(provider_key: str) -> bool:
    key = str(provider_key or "").strip().lower()
    if key == "thesportsdb":
        return True
    return _env_present(*(PROVIDER_KEY_ENVS.get(key) or tuple()))


def _provider_explicitly_disabled(provider_key: str) -> bool:
    key = str(provider_key or "").strip().lower()
    if key in REMOVED_PROVIDERS:
        return True
    return any(_falsey(os.getenv(name)) for name in PROVIDER_ENABLE_ENVS.get(key, tuple()) if os.getenv(name) is not None)


def _should_enable_provider(provider_key: str, original_result: bool) -> bool:
    key = str(provider_key or "").strip().lower()
    if _provider_explicitly_disabled(key):
        return False
    if original_result:
        return True
    if key == "sportlogic" and _provider_auth_available(key):
        return True
    return _provider_auth_available(key) or any(_truthy(os.getenv(name)) for name in PROVIDER_ENABLE_ENVS.get(key, tuple()) if os.getenv(name) is not None)


def patch_runner_provider_policy() -> None:
    try:
        from app.services import runner as runner_module
    except Exception:
        return
    cls = getattr(runner_module, "PredictionRunner", None)
    if cls is None or getattr(cls, "_harizon_provider_activation_patch", False):
        return
    original_instance_by_key = getattr(cls, "_provider_instance_by_key", None)
    original_has_auth = getattr(cls, "_provider_has_required_auth", None)
    original_provider_enabled = getattr(cls, "_provider_enabled", None)

    if callable(original_instance_by_key):
        def provider_instance_by_key_patched(self, provider_key: str):
            if str(provider_key or "").strip().lower() in REMOVED_PROVIDERS:
                return None
            return original_instance_by_key(self, provider_key)
        cls._provider_instance_by_key = provider_instance_by_key_patched

    if callable(original_has_auth):
        def provider_has_required_auth_patched(self, provider_key: str) -> bool:
            key = str(provider_key or "").strip().lower()
            if _provider_explicitly_disabled(key):
                return False
            if _provider_auth_available(key):
                return True
            return bool(original_has_auth(self, provider_key))
        cls._provider_has_required_auth = provider_has_required_auth_patched

    if callable(original_provider_enabled):
        def provider_enabled_patched(self, provider_name: str, default: bool = True) -> bool:
            return _should_enable_provider(str(provider_name or ""), bool(original_provider_enabled(self, provider_name, default)))
        cls._provider_enabled = provider_enabled_patched

    cls._harizon_provider_activation_patch = True


def patch_market_integrity_policy() -> None:
    try:
        from app.services import market_integrity
    except Exception:
        return
    if getattr(market_integrity, "_harizon_low_total_absolute_guard_patch", False):
        return
    original_validate: Callable[[Any], Any] | None = getattr(market_integrity, "validate_candidate", None)
    if not callable(original_validate):
        return

    def _to_float(value: Any, default: float | None = None) -> float | None:
        try:
            if value in (None, ""):
                return default
            return float(str(value).replace(",", "."))
        except Exception:
            return default

    def _candidate_selection(candidate: Any) -> str:
        text = " ".join(str(getattr(candidate, attr, "") or "") for attr in ("selection", "selection_key", "market", "label")).lower()
        if "over" in text or "больше" in text or "тб" in text:
            return "over"
        if "under" in text or "меньше" in text or "тм" in text:
            return "under"
        return text.strip()

    def validate_candidate_patched(candidate: Any):
        decision = original_validate(candidate)
        if not _truthy(os.getenv("MATCH_TOTAL_OVER15_ABSOLUTE_PRICE_GUARD_ENABLED"), True):
            return decision
        family = str(getattr(candidate, "family", "") or "").strip().lower()
        point = _to_float(getattr(candidate, "point", None))
        price = 0.0
        try:
            best_price = getattr(market_integrity, "_best_price", None)
            price = float(best_price(candidate)) if callable(best_price) else 0.0
        except Exception:
            price = 0.0
        if price <= 0.0:
            price = _to_float(getattr(candidate, "odds", None), 0.0) or 0.0
        absolute_max = _to_float(os.getenv("MATCH_TOTAL_OVER15_ABSOLUTE_MAX_ODDS"), 1.85) or 1.85
        if family == "totals" and point is not None and point <= 1.5 and _candidate_selection(candidate) == "over" and price > absolute_max:
            reason = f"suspicious_low_total_absolute_price:point={point:g},odds={price:.2f},max={absolute_max:.2f}"
            if reason not in decision.reasons:
                decision.reasons.append(reason)
            decision.passed = False
        return decision

    market_integrity.validate_candidate = validate_candidate_patched
    market_integrity._harizon_low_total_absolute_guard_patch = True


def install_env_defaults() -> None:
    for provider in REMOVED_PROVIDERS:
        for name in PROVIDER_ENABLE_ENVS.get(provider, tuple()):
            os.environ[name] = "false"

    _set_default("HARIZON_RUNTIME_POLICY_VERSION", "harizon-runtime-policy-v6-max-lines-context")
    _set_default("HARIZON_EFFECTIVE_RUNTIME_POLICY_VERSION", "harizon-runtime-policy-v6-max-lines-context")
    _set_default("MATCH_BOOTSTRAP_PROVIDER", "odds_api_io")
    _set_default("DAY_INVENTORY_BOOTSTRAP_PROVIDER", "odds_api_io")
    _set_default("DAY_INVENTORY_USE_FOR_RUN", "true")
    _set_default("DAY_INVENTORY_FORCE_PROVIDER_MERGE", "false")
    _set_default("DAY_INVENTORY_NEAR_WINDOW_PRIORITY", "true")
    _set_default("DAY_INVENTORY_NEAR_WINDOW_HOURS", "12")
    _set_default("DAY_INVENTORY_ACCUMULATION_ENABLED", "true")
    _set_default("FIXTURE_EXPANSION_ENABLED", "true")
    _set_default("RUNTIME_PRE_PREDICTION_INVENTORY_HOOKS_ENABLED", "true")
    _set_default("RUNTIME_POST_PREDICTION_INVENTORY_HOOKS_ENABLED", "true")
    _set_default("LINE_MOVEMENT_GUARD_ENABLED", "true")
    _set_default("LINE_MOVEMENT_DROP_BAD_CANDIDATES", "true")
    _set_default("CRON_EXPECTED_INTERVAL_MINUTES", "120")
    _set_default("FINAL_PRE_KICKOFF_REFRESH_WINDOW_MINUTES", "150")
    _set_default("FINAL_PRE_KICKOFF_MAX_LINE_AGE_MINUTES", "18")
    _set_default("URGENT_KICKOFF_WINDOW_MINUTES", "180")
    _set_default("URGENT_ODDS_MAX_AGE_MINUTES", "35")
    _set_default("LINE_MOVEMENT_MAX_NEGATIVE_PRICE_MOVE_PCT", "8.0")
    _set_default("LINE_MOVEMENT_MIN_CURRENT_EV_PCT", "3.0")
    _set_default("LINE_MOVEMENT_MIN_CURRENT_EDGE_PP", "1.5")

    _set_default("VOLUME_POLICY_MODE", "target_5")
    _set_default("DAILY_TARGET_PICKS", "5")
    _set_default("DAILY_HARD_CAP_PICKS", "7")
    _set_default("MAX_PICKS_PER_RUN", "2")
    _set_default("CONTROLLED_FALLBACK_MAX_PICKS_PER_RUN", "2")
    _set_default("CONTEXT_ENRICHMENT_REQUIRES_OFFERS", "false")
    _set_default("DAY_INVENTORY_CONTEXT_BACKFILL_ENABLED", "true")
    _set_default("DAY_INVENTORY_CONTEXT_BACKFILL_LIMIT", "160")
    _set_if_lower("ANALYSIS_MATCH_CAP_PER_RUN", 420)
    _set_if_lower("DIAGNOSTICS_MATCH_LIMIT", 420)
    _set_if_lower("CONTEXT_ENRICHMENT_MATCH_LIMIT", 260)

    _set_default("CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT", "true")
    _set_float_if_higher("CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE", 68.0)
    _set_float_if_higher("CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT", 7.0)
    _set_float_if_higher("CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP", 3.0)
    _set_default("CONTROLLED_FALLBACK_ALLOWED_FAMILIES", "totals,dnb,btts")
    _set_default("CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM", "true")
    _set_default("CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES", "1")
    _set_default("CONTROLLED_FALLBACK_REJECT_SINGLE_SOURCE_UNLESS_3_BOOKS", "true")
    _set_default("CONTROLLED_FALLBACK_SINGLE_SOURCE_MIN_BOOKS", "3")
    _set_default("CONTROLLED_CONSENSUS_CANDIDATE_RESCUE_ENABLED", "true")
    _set_default("CONTROLLED_RESCUE_ALLOWED_FAMILIES", "totals,dnb,btts")
    _set_default("CONTROLLED_RESCUE_MIN_PAIRED_BOOKS", "2")
    _set_default("CONTROLLED_RESCUE_MIN_ODDS", "1.45")
    _set_default("CONTROLLED_RESCUE_MAX_ODDS", "2.75")
    _set_default("CONTROLLED_RESCUE_MIN_EDGE_PP", "1.2")
    _set_default("CONTROLLED_RESCUE_MIN_EV_PCT", "2.5")
    _set_default("CONTROLLED_RESCUE_RETURN_LIMIT", "24")
    _set_default("CONTROLLED_RESCUE_MAX_TOTAL", "40")
    _set_default("CONTROLLED_RESCUE_MAX_PER_MATCH", "2")
    _set_default("POST_INTEGRITY_CANDIDATE_RESCUE_ENABLED", "true")
    _set_default("POST_INTEGRITY_RESCUE_APPLY_MARKET_GUARD", "true")
    _set_default("POST_INTEGRITY_RESCUE_RETURN_LIMIT", "24")

    _set_default("ODDS_API_IO_BOOKMAKERS_ACCOUNT1", "Bet365,Unibet")
    _set_default("ODDS_API_IO_BOOKMAKERS_ACCOUNT2", "Betfair Exchange,Sbobet")
    _set_default("ODDS_API_IO_BOOKMAKERS", "Bet365,Unibet,Betfair Exchange,Sbobet")
    _set_default(
        "TARGET_BOOKMAKERS",
        "Bet365,Unibet,Betfair Exchange,Sbobet",
    )
    _set_default(
        "CONSENSUS_BOOKMAKERS",
        "Bet365,Unibet,Betfair Exchange,Sbobet",
    )
    os.environ["ODDS_API_IO_UNFILTERED_EMPTY_RETRY_ENABLED"] = "false"
    os.environ["ODDS_API_IO_BOOKMAKER_ALIAS_EMPTY_RETRY_ENABLED"] = "false"
    if _env_present("ODDS_API_IO_KEY"):
        os.environ.setdefault("ENABLE_ODDS_API_IO", "true")
        _set_if_lower("ODDS_API_IO_ACCOUNT1_PER_RUN_MAX", 100)
        _set_if_lower("MAX_MATCHES_FOR_ODDS_FETCH", 520)
    if _env_present("ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2"):
        _set_if_lower("ODDS_API_IO_ACCOUNT2_PER_RUN_MAX", 100)
        _set_if_lower("ODDS_API_IO_PER_RUN_MAX", 200)
        _set_if_lower("ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN", 200)
        _set_if_lower("MAX_MATCHES_FOR_ODDS_FETCH", 520)

    _set_default("SECONDARY_ODDS_RESCUE_ENABLED", "true")
    _set_default("SECONDARY_ODDS_RESCUE_TRIGGER", "single_source_candidate_or_primary_thin")
    if _env_present("ODDSPAPI_API_KEY", "ODDSPAPI_KEY", "ODDS_PAPI_API_KEY"):
        os.environ.setdefault("ENABLE_ODDSPAPI", "true")
        os.environ.setdefault("ODDSPAPI_ENABLED", "true")
        _set_if_lower("ODDSPAPI_MAX_REQUESTS_PER_RUN", 3)
        _set_if_lower("ODDSPAPI_MATCH_LIMIT", 24)
    if _env_present("ALLSPORTSAPI_API_KEY", "ALLSPORTSAPI_KEY"):
        os.environ.setdefault("ENABLE_ALLSPORTSAPI", "true")
        os.environ.setdefault("ALLSPORTSAPI_ENABLED", "true")
        _set_if_lower("ALLSPORTSAPI_MAX_REQUESTS_PER_RUN", 20)
        _set_if_lower("ALLSPORTSAPI_MATCH_LIMIT", 48)
    if _env_present("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN"):
        os.environ.setdefault("ENABLE_SPORTLOGIC", "true")
        os.environ.setdefault("SPORTLOGIC_ENABLED", "true")
        os.environ.setdefault("SPORTLOGIC_CONTROLLED_ODDS_ENABLED", "true")
        _set_if_lower("SPORTLOGIC_PER_RUN_MAX", 30)
        _set_if_lower("SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN", 30)
        _set_if_lower("SPORTLOGIC_CONTEXT_MATCH_LIMIT", 60)
        _set_if_lower("SPORTLOGIC_ODDS_MATCH_LIMIT", 24)

    if _env_present("FOOTBALL_DATA_API_KEY", "FOOTBALL_DATA_KEY"):
        os.environ.setdefault("ENABLE_FOOTBALL_DATA_CONTEXT", "true")
        _set_if_lower("FOOTBALL_DATA_MAX_REQUESTS_PER_RUN", 8)
        _set_if_lower("FOOTBALL_DATA_CONTEXT_MATCH_LIMIT", 72)

    _set_default("THESPORTSDB_API_KEY", "123")
    os.environ.setdefault("ENABLE_THESPORTSDB_CONTEXT", "true")
    _set_if_lower("THESPORTSDB_REQUESTS_MAX_PER_RUN", 12)
    _set_if_lower("THESPORTSDB_CONTEXT_MATCH_LIMIT", 96)

    _set_default("ENABLE_EXTERNAL_SIGNALS", "true")
    _set_default("ENABLE_CLUBELO_CONTEXT", "true")
    _set_default("ENABLE_FOOTBALL_DATA_UK_CONTEXT", "true")
    _set_default("ENABLE_OPEN_METEO_CONTEXT", "true")
    _set_default("ENABLE_WIKIDATA_CONTEXT", "true")
    _set_if_lower("EXTERNAL_SIGNALS_PER_RUN_MAX", 80)
    _set_if_lower("EXTERNAL_SIGNALS_CONTEXT_MATCH_LIMIT", 120)
    _set_if_higher("FUTRIXMETRICS_CONTEXT_MATCH_LIMIT", 8)
    _set_if_higher("FUTRIXMETRICS_MAX_HTTP_REQUESTS_PER_RUN", 16)
    _set_if_higher("FUTRIXMETRICS_PER_RUN_MAX", 16)

    _set_default("MARKET_INTEGRITY_HARD_GUARD_ENABLED", "true")
    _set_default("MARKET_INTEGRITY_CANDIDATE_PATCH_ENABLED", "true")
    _set_default("MARKET_INTEGRITY_MIN_BOOKS", "2")
    _set_default("MARKET_INTEGRITY_MIN_SOURCES", "1")
    _set_default("MARKET_INTEGRITY_SINGLE_SOURCE_MIN_BOOKS", "3")
    _set_default("MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS", "1.65")
    _set_default("MATCH_TOTAL_OVER15_ABSOLUTE_PRICE_GUARD_ENABLED", "true")
    _set_default("MATCH_TOTAL_OVER15_ABSOLUTE_MAX_ODDS", "1.85")
    _set_default("MATCH_TOTAL_OVER20_MAX_REASONABLE_ODDS", "2.05")
    _set_default("MARKET_INTEGRITY_MAX_PRICE_DISPERSION_PCT", "30")
    _set_default("MARKET_INTEGRITY_MAX_EXACT_PRICE_DISPERSION_PCT", "22")
    _set_default("MARKET_INTEGRITY_MAX_EXACT_LINE_DELTA_PCT", "18")
    _set_default("MARKET_INTEGRITY_USE_EXACT_PRICE_SOURCES", "true")
    _set_default("PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE", "true")
    _set_default("DISABLE_SPREADS_UNTIL_HANDICAP_PARSER_VERIFIED", "true")
    _set_default("SPREADS_PUBLICATION_ENABLED", "false")
    _set_default("TEAM_TOTALS_PUBLICATION_ENABLED", "false")

    _set_default("RUN_REPORT_ONLY_WHEN_NO_PREDICTIONS", "false")
    _set_default("ENHANCED_RUN_REPORT_SEND_TELEGRAM", "true")
    _set_default("RUNTIME_PROVIDER_DIAGNOSTICS_ENABLED", "true")
    _set_default("ENABLE_PROVIDER_DIAGNOSTICS", "true")


def install() -> None:
    install_env_defaults()
    try:
        from app.services import runtime_inventory_hooks
        runtime_inventory_hooks.install()
    except Exception:
        pass
    patch_runner_provider_policy()
    try:
        from app.providers import odds_api_io_startup_compat
        odds_api_io_startup_compat.install()
    except Exception:
        pass
    try:
        from app.services import controlled_candidate_rescue
        controlled_candidate_rescue.install()
    except Exception:
        pass
    try:
        from app.services import market_integrity
        market_integrity.install()
    except Exception:
        pass
    patch_market_integrity_policy()
    try:
        from app.services import post_integrity_candidate_rescue
        post_integrity_candidate_rescue.install()
    except Exception:
        pass
