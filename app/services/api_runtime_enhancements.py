from __future__ import annotations

import os
from typing import Any


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _set_default(name: str, value: str) -> None:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        os.environ[name] = value


def _set_if_lower(name: str, minimum: int) -> None:
    raw = str(os.getenv(name) or "").strip()
    try:
        current = int(float(raw))
    except Exception:
        current = 0
    if current < minimum:
        os.environ[name] = str(minimum)


def _credentials_present(*names: str) -> bool:
    return all(str(os.getenv(name) or "").strip() for name in names)


def install_env_defaults() -> None:
    # odds-api.io dual-account mode. Free accounts are usually limited to two
    # bookmakers each, so split bookmakers by account instead of asking one key
    # for more books than the plan allows.
    _set_default("ODDS_API_IO_BOOKMAKERS_ACCOUNT1", "Bet365,Unibet")
    _set_default("ODDS_API_IO_BOOKMAKERS_ACCOUNT2", "Betfair Exchange,Sbobet")
    if str(os.getenv("ODDS_API_IO_KEY_2") or os.getenv("ODDS_API_IO_KEY2") or "").strip():
        _set_default("ODDS_API_IO_BOOKMAKERS", "Bet365,Unibet,Betfair Exchange,Sbobet")
        _set_default("TARGET_BOOKMAKERS", "Bet365,Unibet,Betfair Exchange,Sbobet")
        _set_default("CONSENSUS_BOOKMAKERS", "Bet365,Unibet,Betfair Exchange,Sbobet")
        _set_if_lower("ODDS_API_IO_ACCOUNT1_PER_RUN_MAX", 100)
        _set_if_lower("ODDS_API_IO_ACCOUNT2_PER_RUN_MAX", 100)
        _set_if_lower("ODDS_API_IO_PER_RUN_MAX", 160)
        _set_if_lower("ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN", 160)
        _set_if_lower("MAX_MATCHES_FOR_ODDS_FETCH", 420)

    # BookiesAPI is the best already-coded independent odds source. Enable it
    # automatically when credentials are present, but keep it backfill-aware.
    if _credentials_present("BOOKIES_API_LOGIN") and (
        str(os.getenv("BOOKIES_API_TOKEN") or "").strip() or str(os.getenv("BOOKIES_API_KEY") or "").strip()
    ):
        os.environ.setdefault("BOOKIES_API_ENABLED", "true")
        os.environ.setdefault("ENABLE_BOOKIES_API", "true")
        os.environ.setdefault("BOOKIES_API_USE_FOR_BACKFILL_ONLY", "true")
        _set_if_lower("BOOKIES_API_ODDS_FETCH_LIMIT", 36)

    # TheSportsDB public key is usable in free mode. This prevents false
    # api_key_missing diagnostics when no private key was configured.
    _set_default("THESPORTSDB_API_KEY", "123")
    _set_default("ENABLE_THESPORTSDB_CONTEXT", "true")
    _set_default("THESPORTSDB_CONTEXT_MATCH_LIMIT", "120")
    _set_if_lower("THESPORTSDB_REQUESTS_MAX_PER_RUN", 12)

    # Context and matching improvements.
    _set_default("ENABLE_EXTERNAL_SIGNALS", "true")
    _set_default("ENABLE_CLUBELO_CONTEXT", "true")
    _set_default("ENABLE_FOOTBALL_DATA_UK_CONTEXT", "true")
    _set_default("ENABLE_OPEN_METEO_CONTEXT", "true")
    _set_default("ENABLE_WIKIDATA_CONTEXT", "true")
    _set_if_lower("EXTERNAL_SIGNALS_PER_RUN_MAX", 80)
    _set_if_lower("EXTERNAL_SIGNALS_CONTEXT_MATCH_LIMIT", 120)

    # Market integrity defaults.
    _set_default("MARKET_INTEGRITY_HARD_GUARD_ENABLED", "true")
    _set_default("MARKET_INTEGRITY_CANDIDATE_PATCH_ENABLED", "true")
    _set_default("MARKET_INTEGRITY_MIN_BOOKS", "2")
    _set_default("MARKET_INTEGRITY_MIN_SOURCES", "1")
    _set_default("MARKET_INTEGRITY_SINGLE_SOURCE_MIN_BOOKS", "3")
    _set_default("MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS", "1.65")
    _set_default("MATCH_TOTAL_OVER20_MAX_REASONABLE_ODDS", "2.05")
    _set_default("MARKET_INTEGRITY_MAX_PRICE_DISPERSION_PCT", "30")
    _set_default("DISABLE_SPREADS_UNTIL_HANDICAP_PARSER_VERIFIED", "true")
    _set_default("TEAM_TOTALS_PUBLICATION_ENABLED", "false")

    # SportLogic stays conservative by default. Set SPORTLOGIC_CONTROLLED_ODDS_ENABLED=true
    # for a manual test after its inventory is confirmed fresh.
    _set_default("SPORTLOGIC_BASE_URL", "https://api.sportlogic.io/api/v1")
    _set_default("SPORTLOGIC_HEADER_NAME", "X-API-Key")
    if _truthy(os.getenv("SPORTLOGIC_CONTROLLED_ODDS_ENABLED")):
        os.environ["ENABLE_SPORTLOGIC"] = "true"
        os.environ["SPORTLOGIC_ENABLED"] = "true"
        _set_if_lower("SPORTLOGIC_PER_RUN_MAX", 30)
        _set_if_lower("SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN", 30)
        _set_if_lower("SPORTLOGIC_CONTEXT_MATCH_LIMIT", 20)
        _set_if_lower("SPORTLOGIC_ODDS_MATCH_LIMIT", 20)


def install() -> None:
    install_env_defaults()
    try:
        from app.services import market_integrity
        market_integrity.install()
    except Exception:
        pass
