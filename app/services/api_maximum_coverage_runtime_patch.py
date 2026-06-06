from __future__ import annotations

"""Runtime policy that makes the core API layer use the provider docs more fully.

This patch is intentionally conservative: it does not relax publication guards and
it does not fabricate lines or contexts. It only:
- opens documented Bzzoiro v2 resources for priority matches;
- makes SStats build a wider 30-60 day team-form index;
- retries SportLogic /games with documented/observed date parameter variants when
  the default request returns an empty list;
- writes an artifact so reports/runs can prove what was installed.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
REPORT_PATH = Path(".data/exports/latest-api-maximum-coverage-runtime-policy.json")


def _truthy(value: Any) -> bool:
    return str(value if value is not None else "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _set_true(name: str) -> None:
    os.environ[name] = "true"


def _set_if_lower(name: str, value: int) -> None:
    if _as_int(os.environ.get(name), -1) < int(value):
        os.environ[name] = str(int(value))


def _set_default(name: str, value: str) -> None:
    if os.environ.get(name) in (None, ""):
        os.environ[name] = str(value)


def _has_secret(*names: str) -> bool:
    return any(str(os.environ.get(name) or "").strip() for name in names)


def _write_report(extra: dict[str, Any] | None = None) -> None:
    payload: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "installed",
        "policy": "maximize_core_api_coverage_without_relaxing_publication_guards",
        "docs_contract": {
            "odds_api_io": "events + odds/multi; preserve raw offer snapshot and bookmaker quorum",
            "bzzoiro_v2": "events + odds + stats + metadata for priority matches",
            "sstats": "30-60 day history -> team_form_index; do not direct-match only future fixture rows",
            "sportlogic": "games first, then game odds for matched priority games; try safe date parameter variants if default /games is empty",
        },
        "publication_safety": {
            "price_integrity_guard": "unchanged",
            "line_movement_guard": "unchanged",
            "quarter_totals_block": "unchanged",
            "xg_quality_value_guards": "unchanged",
        },
        "env_sample": {
            key: os.environ.get(key)
            for key in (
                "BZZOIRO_V2_FETCH_EVENT_ODDS",
                "BZZOIRO_V2_FETCH_EVENT_STATS",
                "BZZOIRO_V2_FETCH_EVENT_METADATA",
                "BZZOIRO_CONTEXT_MATCH_LIMIT",
                "SSTATS_LOOKBACK_DAYS",
                "SSTATS_RECENT_MATCHES",
                "SSTATS_FORM_MIN_SAMPLE_PER_TEAM",
                "SPORTLOGIC_ENABLED",
                "SPORTLOGIC_PER_RUN_MAX",
                "SPORTLOGIC_ODDS_MATCH_LIMIT",
            )
        },
    }
    if extra:
        payload.update(extra)
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _apply_env_policy() -> dict[str, Any]:
    changed: dict[str, str] = {}

    def mark(name: str) -> None:
        changed[name] = str(os.environ.get(name) or "")

    # Bzzoiro v2 docs: list events first, then details/stats/metadata/odds by event id.
    if _has_secret("BZZOIRO_API_KEY"):
        for name in (
            "BZZOIRO_V2_ENRICHMENT_ENABLED",
            "BZZOIRO_V2_EVENTS_ENABLED",
            "BZZOIRO_V2_STATS_ENABLED",
            "BZZOIRO_V2_ODDS_ENABLED",
            "BZZOIRO_V2_METADATA_ENABLED",
            "BZZOIRO_V2_LINEUPS_ENABLED",
            "BZZOIRO_V2_FETCH_EVENT_ODDS",
            "BZZOIRO_V2_FETCH_EVENT_STATS",
            "BZZOIRO_V2_FETCH_EVENT_METADATA",
            "BZZOIRO_PRICE_BACKFILL_ENABLED",
            "BZZOIRO_V2_SOURCE_MATRIX_TARGETS_ENABLED",
        ):
            _set_true(name); mark(name)
        _set_if_lower("BZZOIRO_CONTEXT_MATCH_LIMIT", 300); mark("BZZOIRO_CONTEXT_MATCH_LIMIT")
        _set_if_lower("BZZOIRO_V2_MATCH_LIMIT", 300); mark("BZZOIRO_V2_MATCH_LIMIT")
        _set_if_lower("BZZOIRO_V2_MAX_EVENTS", 800); mark("BZZOIRO_V2_MAX_EVENTS")
        _set_if_lower("BZZOIRO_V2_PAGE_SIZE", 200); mark("BZZOIRO_V2_PAGE_SIZE")
        _set_if_lower("BZZOIRO_MAX_REQUESTS_PER_RUN", 200); mark("BZZOIRO_MAX_REQUESTS_PER_RUN")
        _set_if_lower("BZZOIRO_REQUEST_BUDGET_GRANTED", 200); mark("BZZOIRO_REQUEST_BUDGET_GRANTED")
        _set_default("BZZOIRO_REQUEST_RETRIES", "2"); mark("BZZOIRO_REQUEST_RETRIES")
        _set_default("BZZOIRO_TIMEOUT_SECONDS", "25"); mark("BZZOIRO_TIMEOUT_SECONDS")
        # Context limit false means use priority queue from the runner rather than a hard provider cut.
        os.environ["BZZOIRO_ENFORCE_CONTEXT_LIMIT"] = "false"; mark("BZZOIRO_ENFORCE_CONTEXT_LIMIT")

    # SStats docs/project notes: use history to build team_form_index, not only direct future fixture matching.
    if _has_secret("SSTATS_API_KEY"):
        for name in (
            "SSTATS_ENABLED",
            "ENABLE_SSTATS",
            "ENABLE_SSTATS_CONTEXT",
            "SSTATS_CONTEXT_ENABLED",
            "SSTATS_DEEP_ENRICHMENT_ENABLED",
            "SSTATS_DEEP_ENRICHMENT_AFTER_CROSSWALK",
            "SSTATS_GAME_DETAIL_ENABLED",
            "SSTATS_LAST_GAMES_STATS_ENABLED",
            "SSTATS_GLICKO_ENABLED",
            "SSTATS_ODDS_RESCUE_ENABLED",
        ):
            _set_true(name); mark(name)
        _set_if_lower("SSTATS_LOOKBACK_DAYS", 60); mark("SSTATS_LOOKBACK_DAYS")
        _set_if_lower("SSTATS_RECENT_MATCHES", 10); mark("SSTATS_RECENT_MATCHES")
        os.environ["SSTATS_FORM_MIN_SAMPLE_PER_TEAM"] = "2"; mark("SSTATS_FORM_MIN_SAMPLE_PER_TEAM")
        _set_if_lower("SSTATS_CONTEXT_MATCH_LIMIT", 300); mark("SSTATS_CONTEXT_MATCH_LIMIT")
        _set_if_lower("SSTATS_MAX_REQUESTS_PER_RUN", 150); mark("SSTATS_MAX_REQUESTS_PER_RUN")
        _set_if_lower("SSTATS_DEEP_DETAIL_LIMIT_PER_RUN", 80); mark("SSTATS_DEEP_DETAIL_LIMIT_PER_RUN")
        _set_if_lower("SSTATS_DEEP_CONTEXT_MATCH_LIMIT", 160); mark("SSTATS_DEEP_CONTEXT_MATCH_LIMIT")
        _set_default("SSTATS_REQUEST_CHUNK_DAYS", "10"); mark("SSTATS_REQUEST_CHUNK_DAYS")

    # SportLogic docs: /games first, then /games/{id}/odds only for matched priority games.
    if _has_secret("SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN") and not _truthy(os.environ.get("SPORTLOGIC_DAILY_CIRCUIT_OPEN")):
        for name in (
            "SPORTLOGIC_ENABLED",
            "ENABLE_SPORTLOGIC",
            "SPORTLOGIC_CONTROLLED_ODDS_ENABLED",
            "DAY_INVENTORY_ENABLE_SPORTLOGIC",
            "SPORTLOGIC_DOCS_DATE_FALLBACK_ENABLED",
        ):
            _set_true(name); mark(name)
        _set_if_lower("SPORTLOGIC_PER_RUN_MAX", 40); mark("SPORTLOGIC_PER_RUN_MAX")
        _set_if_lower("SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN", 40); mark("SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN")
        _set_if_lower("SPORTLOGIC_REQUEST_BUDGET_GRANTED", 40); mark("SPORTLOGIC_REQUEST_BUDGET_GRANTED")
        _set_if_lower("SPORTLOGIC_MATCH_LIMIT", 100); mark("SPORTLOGIC_MATCH_LIMIT")
        _set_if_lower("SPORTLOGIC_CONTEXT_MATCH_LIMIT", 100); mark("SPORTLOGIC_CONTEXT_MATCH_LIMIT")
        _set_if_lower("SPORTLOGIC_ODDS_MATCH_LIMIT", 40); mark("SPORTLOGIC_ODDS_MATCH_LIMIT")
        _set_if_lower("SPORTLOGIC_ACTIVE_ODDS_SMOKE_PAGES", 3); mark("SPORTLOGIC_ACTIVE_ODDS_SMOKE_PAGES")
        _set_if_lower("SPORTLOGIC_ACTIVE_ODDS_SMOKE_GAME_LIMIT", 60); mark("SPORTLOGIC_ACTIVE_ODDS_SMOKE_GAME_LIMIT")
        _set_if_lower("DAY_INVENTORY_SPORTLOGIC_MAX_REQUESTS", 40); mark("DAY_INVENTORY_SPORTLOGIC_MAX_REQUESTS")
        _set_if_lower("DAY_INVENTORY_SPORTLOGIC_MATCH_LIMIT", 100); mark("DAY_INVENTORY_SPORTLOGIC_MATCH_LIMIT")
        _set_default("SPORTLOGIC_PER_PAGE", "100"); mark("SPORTLOGIC_PER_PAGE")
        # Do not force auth header if user configured it; provider patch below can try fallbacks only after an empty/auth failed response.
        _set_default("SPORTLOGIC_HEADER_NAME", "X-API-Key"); mark("SPORTLOGIC_HEADER_NAME")

    return changed


def _empty_rows(payload: Any, extract_list: Any | None = None) -> bool:
    try:
        if extract_list is not None:
            return len(extract_list(payload)) <= 0
    except Exception:
        pass
    if payload is None:
        return True
    if isinstance(payload, list):
        return len(payload) == 0
    if isinstance(payload, dict):
        for key in ("results", "data", "events", "fixtures", "matches", "items", "response"):
            value = payload.get(key)
            if isinstance(value, list) and value:
                return False
            if isinstance(value, dict) and not _empty_rows(value):
                return False
        return True
    return False


def _patch_sportlogic() -> dict[str, Any]:
    try:
        from app.providers.sportlogic_provider import SportLogicProvider
    except Exception as exc:
        return {"sportlogic_patch": "import_failed", "error": str(exc)[:160]}
    if getattr(SportLogicProvider, "_harizon_api_maximum_patched", False):
        return {"sportlogic_patch": "already_installed"}

    original_get_json = SportLogicProvider._get_json
    original_headers = SportLogicProvider._headers

    def headers_with_forced(self: Any) -> dict[str, str]:
        forced = getattr(self, "_harizon_api_maximum_forced_headers", None)
        if isinstance(forced, dict) and forced:
            return dict(forced)
        return original_headers(self)

    async def get_json_maximum(self: Any, client: Any, path: str, params: dict[str, Any], stats: dict[str, Any], preview: dict[str, Any]) -> Any | None:
        payload = await original_get_json(self, client, path, params, stats, preview)
        if path != "/games" or not _truthy(os.environ.get("SPORTLOGIC_DOCS_DATE_FALLBACK_ENABLED", "true")):
            return payload
        if not _empty_rows(payload, getattr(self, "_extract_list", None)):
            return payload

        date_from = str((params or {}).get("date_from") or (params or {}).get("from") or (params or {}).get("date") or "").strip()
        date_to = str((params or {}).get("date_to") or (params or {}).get("to") or date_from).strip()
        if not date_from:
            return payload
        iso_from = f"{date_from}T00:00:00Z" if "T" not in date_from else date_from
        iso_to = f"{date_to}T23:59:59Z" if "T" not in date_to else date_to
        per_page = (params or {}).get("per_page") or os.getenv("SPORTLOGIC_PER_PAGE") or 100
        alternatives: list[dict[str, Any]] = [
            {"date": date_from, "per_page": per_page},
            {"date_from": iso_from, "date_to": iso_to, "per_page": per_page},
            {"from": date_from, "to": date_to, "per_page": per_page},
            {"start_date": date_from, "end_date": date_to, "per_page": per_page},
            {"date_from": date_from, "date_to": date_to, "status": "scheduled", "per_page": per_page},
            {"date_from": date_from, "date_to": date_to, "status": "notstarted", "per_page": per_page},
        ]

        key = str(getattr(self, "api_key", "") or "").strip()
        base_headers = original_headers(self)
        header_variants: list[dict[str, str] | None] = [None]
        if key:
            header_variants.extend([
                {"Accept": "application/json", "X-API-Key": key},
                {"Accept": "application/json", "Authorization": f"Bearer {key}"},
                {"Accept": "application/json", "Authorization": f"Token {key}"},
            ])
        tried = 0
        for alt in alternatives:
            for forced_headers in header_variants:
                if not self._budget_left():
                    stats["api_maximum_sportlogic_budget_exhausted"] = True
                    return payload
                tried += 1
                try:
                    if forced_headers is not None:
                        self._harizon_api_maximum_forced_headers = forced_headers
                    elif hasattr(self, "_harizon_api_maximum_forced_headers"):
                        delattr(self, "_harizon_api_maximum_forced_headers")
                    alt_payload = await original_get_json(self, client, path, alt, stats, preview)
                finally:
                    if hasattr(self, "_harizon_api_maximum_forced_headers"):
                        delattr(self, "_harizon_api_maximum_forced_headers")
                if not _empty_rows(alt_payload, getattr(self, "_extract_list", None)):
                    stats["api_maximum_sportlogic_fallback_used"] = True
                    stats["api_maximum_sportlogic_alt_params"] = alt
                    if forced_headers is not None and forced_headers != base_headers:
                        stats["api_maximum_sportlogic_alt_auth"] = sorted(k for k in forced_headers.keys() if k.lower() != "accept")
                    return alt_payload
        stats["api_maximum_sportlogic_alt_attempts"] = int(stats.get("api_maximum_sportlogic_alt_attempts") or 0) + tried
        return payload

    SportLogicProvider._headers = headers_with_forced
    SportLogicProvider._get_json = get_json_maximum
    SportLogicProvider._harizon_api_maximum_patched = True
    return {"sportlogic_patch": "installed", "fallbacks": "date/date_from_iso/from_to/start_end/status_scheduled/notstarted"}


def _patch_bzzoiro_v2() -> dict[str, Any]:
    try:
        from app.providers.bzzoiro_v2 import BzzoiroContextProvider
    except Exception as exc:
        return {"bzzoiro_v2_patch": "import_failed", "error": str(exc)[:160]}
    if getattr(BzzoiroContextProvider, "_harizon_api_maximum_patched", False):
        return {"bzzoiro_v2_patch": "already_installed"}

    original_init = BzzoiroContextProvider.__init__
    original_fetch_events = BzzoiroContextProvider._fetch_events

    def init_maximum(self: Any, settings: Any) -> None:
        original_init(self, settings)
        self.fetch_event_odds = True
        self.fetch_event_stats = True
        self.fetch_event_metadata = True
        self.enforce_context_limit = False
        try:
            self.page_size = min(200, max(50, int(float(os.getenv("BZZOIRO_V2_PAGE_SIZE", "200") or 200))))
        except Exception:
            self.page_size = 200

    async def fetch_events_maximum(self: Any, client: Any, headers: dict[str, str], date_from: str, date_to: str, stats: dict[str, Any]) -> list[dict[str, Any]]:
        rows = await original_fetch_events(self, client, headers, date_from, date_to, stats)
        if rows:
            return rows
        # Bzzoiro docs say date_from/date_to; observed deployments sometimes expose
        # date_start/date_end or ISO boundaries. Try only if the normal form returns empty.
        limit = getattr(self, "page_size", 200) or 200
        alt_params = [
            {"date_start": date_from, "date_end": date_to, "limit": limit, "offset": 0},
            {"start_date": date_from, "end_date": date_to, "limit": limit, "offset": 0},
            {"date_from": f"{date_from}T00:00:00Z", "date_to": f"{date_to}T23:59:59Z", "limit": limit, "offset": 0},
            {"date_from": date_from, "date_to": date_to, "status": "notstarted", "limit": limit, "offset": 0},
        ]
        for params in alt_params:
            payload = await self._get_json(client, "/events/", headers, params, stats)
            batch = self._rows(payload)
            if batch:
                stats["api_maximum_bzzoiro_alt_events_used"] = params
                return batch[: int(float(os.getenv("BZZOIRO_V2_MAX_EVENTS", "800") or 800))]
        return rows

    BzzoiroContextProvider.__init__ = init_maximum
    BzzoiroContextProvider._fetch_events = fetch_events_maximum
    BzzoiroContextProvider._harizon_api_maximum_patched = True
    return {"bzzoiro_v2_patch": "installed", "metadata_enabled": True, "events_param_fallbacks": True}


def install() -> None:
    changed_env = _apply_env_policy()
    patches = {}
    patches.update(_patch_bzzoiro_v2())
    patches.update(_patch_sportlogic())
    _write_report({"changed_env": changed_env, "patches": patches})
