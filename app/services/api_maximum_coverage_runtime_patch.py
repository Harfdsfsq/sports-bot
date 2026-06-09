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

import atexit
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
REPORT_PATH = Path(".data/exports/latest-api-maximum-coverage-runtime-policy.json")
AUDIT_PATH = Path(".data/exports/latest-core-api-coverage-audit.json")


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

    # odds-api.io: spend the available per-run budget on the full frozen inventory, not only near-window rows.
    if _has_secret("ODDS_API_IO_KEY", "ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2"):
        for name in (
            "ODDS_API_IO_OFFER_SNAPSHOT_ENABLED",
            "ODDS_API_IO_PRICE_BACKFILL_INCLUDE_ALL_INVENTORY",
            "PRICE_BACKFILL_ODDS_API_IO_ENABLED",
            "ODDS_API_IO_FETCH_FULL_DAY_INVENTORY",
        ):
            _set_true(name); mark(name)
        _set_if_lower("MAX_MATCHES_FOR_ODDS_FETCH", 300); mark("MAX_MATCHES_FOR_ODDS_FETCH")
        _set_if_lower("PRICE_BACKFILL_ODDS_API_IO_EVENT_LIMIT", 180); mark("PRICE_BACKFILL_ODDS_API_IO_EVENT_LIMIT")
        _set_if_lower("PRICE_BACKFILL_ODDS_API_IO_BATCHES_PER_ACCOUNT", 8); mark("PRICE_BACKFILL_ODDS_API_IO_BATCHES_PER_ACCOUNT")
        _set_if_lower("PRICE_BACKFILL_ODDS_API_IO_MAX_EVENT_IDS_PER_REQUEST", 20); mark("PRICE_BACKFILL_ODDS_API_IO_MAX_EVENT_IDS_PER_REQUEST")

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
        _set_if_lower("BZZOIRO_V2_MAX_EVENTS", 1000); mark("BZZOIRO_V2_MAX_EVENTS")
        _set_if_lower("BZZOIRO_V2_PAGE_SIZE", 200); mark("BZZOIRO_V2_PAGE_SIZE")
        _set_if_lower("BZZOIRO_MAX_REQUESTS_PER_RUN", 260); mark("BZZOIRO_MAX_REQUESTS_PER_RUN")
        _set_if_lower("BZZOIRO_REQUEST_BUDGET_GRANTED", 260); mark("BZZOIRO_REQUEST_BUDGET_GRANTED")
        _set_default("BZZOIRO_REQUEST_RETRIES", "2"); mark("BZZOIRO_REQUEST_RETRIES")
        _set_default("BZZOIRO_TIMEOUT_SECONDS", "25"); mark("BZZOIRO_TIMEOUT_SECONDS")
        # Context limit false means use priority queue from the runner rather than a hard provider cut.
        os.environ["BZZOIRO_ENFORCE_CONTEXT_LIMIT"] = "false"; mark("BZZOIRO_ENFORCE_CONTEXT_LIMIT")
        for name in ("BZZOIRO_CURRENT_ODDS_AS_SECONDARY_SOURCE", "BZZOIRO_EXACT_OFFER_BRIDGE_ENABLED", "BZZOIRO_ODDS_COMPARISON_AS_SECONDARY_OFFERS", "BZZOIRO_V2_FETCH_ODDS_COMPARISON"):
            _set_true(name); mark(name)
        _set_if_lower("BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT", 140); mark("BZZOIRO_V2_ODDS_COMPARISON_MATCH_LIMIT")
        _set_if_lower("BZZOIRO_V2_ODDS_COMPARISON_MAX_REQUESTS", 120); mark("BZZOIRO_V2_ODDS_COMPARISON_MAX_REQUESTS")
        _set_if_lower("BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT", 140); mark("BZZOIRO_PRICE_BACKFILL_TARGET_LIMIT")
        # Use already-matched Bzzoiro odds/event evidence as a light context source when
        # a second context is missing. This does not create lines or relax publication guards.
        os.environ["BZZOIRO_ODDS_MATCH_COUNTS_AS_EVENT_CONTEXT"] = "true"; mark("BZZOIRO_ODDS_MATCH_COUNTS_AS_EVENT_CONTEXT")
        _set_default("BZZOIRO_CONTEXT_GAP_RELAXED_MIN_SCORE", "50"); mark("BZZOIRO_CONTEXT_GAP_RELAXED_MIN_SCORE")
        _set_if_lower("BZZOIRO_CONTEXT_GAP_TARGET_LIMIT", 260); mark("BZZOIRO_CONTEXT_GAP_TARGET_LIMIT")
        _set_if_lower("BZZOIRO_CONTEXT_GAP_MAX_MATCHES", 300); mark("BZZOIRO_CONTEXT_GAP_MAX_MATCHES")
        _set_if_lower("BZZOIRO_CONTEXT_GAP_TIMEOUT_SECONDS", 165); mark("BZZOIRO_CONTEXT_GAP_TIMEOUT_SECONDS")
        os.environ["BZZOIRO_CONTEXT_GAP_INCLUDE_BOOKMAKER_QUALIFIED"] = "true"; mark("BZZOIRO_CONTEXT_GAP_INCLUDE_BOOKMAKER_QUALIFIED")
        os.environ["BZZOIRO_CONTEXT_GAP_INCLUDE_NEAR_WINDOW_WITH_2PLUS_BOOKS"] = "true"; mark("BZZOIRO_CONTEXT_GAP_INCLUDE_NEAR_WINDOW_WITH_2PLUS_BOOKS")
        os.environ["BZZOIRO_CONTEXT_GAP_LIGHT_CONTEXT_ONLY"] = "true"; mark("BZZOIRO_CONTEXT_GAP_LIGHT_CONTEXT_ONLY")
        os.environ["BZZOIRO_CONTEXT_GAP_NEAR_WINDOW_FIRST"] = "true"; mark("BZZOIRO_CONTEXT_GAP_NEAR_WINDOW_FIRST")

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
        for name in ("SSTATS_TEAM_FORM_RUNTIME_BRIDGE_ENABLED", "SSTATS_TEAM_FORM_JOIN_BY_ALIAS", "SSTATS_TEAM_FORM_CONTEXT_SOURCE_ENABLED"):
            _set_true(name); mark(name)
        _set_if_lower("SSTATS_TEAM_FORM_TARGET_MATCHES", 300); mark("SSTATS_TEAM_FORM_TARGET_MATCHES")
        _set_if_lower("SSTATS_DEEP_CONTEXT_MATCH_LIMIT", 220); mark("SSTATS_DEEP_CONTEXT_MATCH_LIMIT")
        os.environ["SSTATS_TEAM_FORM_RUNTIME_BRIDGE_ENABLED"] = "true"; mark("SSTATS_TEAM_FORM_RUNTIME_BRIDGE_ENABLED")
        os.environ["SSTATS_TEAM_FORM_JOIN_BY_ALIAS"] = "true"; mark("SSTATS_TEAM_FORM_JOIN_BY_ALIAS")
        _set_if_lower("SSTATS_TEAM_FORM_TARGET_MATCHES", 300); mark("SSTATS_TEAM_FORM_TARGET_MATCHES")

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
        _set_if_lower("SPORTLOGIC_PER_RUN_MAX", 70); mark("SPORTLOGIC_PER_RUN_MAX")
        _set_if_lower("SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN", 70); mark("SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN")
        _set_if_lower("SPORTLOGIC_REQUEST_BUDGET_GRANTED", 70); mark("SPORTLOGIC_REQUEST_BUDGET_GRANTED")
        os.environ["SPORTLOGIC_DIAGNOSTIC_CAPTURE"] = "true"; mark("SPORTLOGIC_DIAGNOSTIC_CAPTURE")
        os.environ["SPORTLOGIC_DOCS_PATH_PROBE_ENABLED"] = "true"; mark("SPORTLOGIC_DOCS_PATH_PROBE_ENABLED")
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
        alternatives: list[tuple[str, dict[str, Any]]] = []
        base_alts: list[dict[str, Any]] = [
            {"date": date_from, "per_page": per_page},
            {"date_from": iso_from, "date_to": iso_to, "per_page": per_page},
            {"from": date_from, "to": date_to, "per_page": per_page},
            {"start_date": date_from, "end_date": date_to, "per_page": per_page},
            {"date_from": date_from, "date_to": date_to, "status": "scheduled", "per_page": per_page},
            {"date_from": date_from, "date_to": date_to, "status": "notstarted", "per_page": per_page},
        ]
        path_variants = [path]
        if _truthy(os.environ.get("SPORTLOGIC_DOCS_PATH_PROBE_ENABLED", "true")):
            path_variants.extend(["/fixtures", "/events", "/matches", "/soccer/games", "/football/games"])
        seen_paths: set[str] = set()
        for alt_path in path_variants:
            if alt_path in seen_paths:
                continue
            seen_paths.add(alt_path)
            for alt in base_alts:
                alternatives.append((alt_path, alt))

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
        for alt_path, alt in alternatives:
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
                    alt_payload = await original_get_json(self, client, alt_path, alt, stats, preview)
                finally:
                    if hasattr(self, "_harizon_api_maximum_forced_headers"):
                        delattr(self, "_harizon_api_maximum_forced_headers")
                if not _empty_rows(alt_payload, getattr(self, "_extract_list", None)):
                    stats["api_maximum_sportlogic_fallback_used"] = True
                    stats["api_maximum_sportlogic_alt_path"] = alt_path
                    stats["api_maximum_sportlogic_alt_params"] = alt
                    if forced_headers is not None and forced_headers != base_headers:
                        stats["api_maximum_sportlogic_alt_auth"] = sorted(k for k in forced_headers.keys() if k.lower() != "accept")
                    return alt_payload
        stats["api_maximum_sportlogic_alt_attempts"] = int(stats.get("api_maximum_sportlogic_alt_attempts") or 0) + tried
        return payload

    SportLogicProvider._headers = headers_with_forced
    SportLogicProvider._get_json = get_json_maximum
    SportLogicProvider._harizon_api_maximum_patched = True
    return {"sportlogic_patch": "installed", "fallbacks": "games+fixtures+events+matches path variants with date/date_from_iso/from_to/start_end/status_scheduled/notstarted"}


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



def _load_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {} if default is None else default


def _get(data: Any, *keys: str, default: Any = 0) -> Any:
    cur = data
    for key in keys:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
    return cur if cur is not None else default


def _write_core_api_audit() -> None:
    """Write a late-run API audit from whatever artifacts exist at process exit.

    This is intentionally a read-only artifact writer. It does not change candidates,
    odds, contexts, or publication decisions. usercustomize imports this module in
    every workflow Python process, so the after-report/after-ledger processes can
    finally write the audit after all normalizer/fallback artifacts have appeared.
    """
    try:
        export = Path(".data/exports")
        report = _load_json(export / "latest-harizon-telegram-run-report.json", {})
        truth = _load_json(export / "latest-day-inventory-coverage-truth.json", {})
        normalizer = _load_json(export / "latest-bookmaker-quorum-coverage-normalizer.json", {})
        backfill = _load_json(export / "latest-odds-api-bookmaker-quorum-mapping-backfill.json", {})
        snapshot = _load_json(export / "latest-odds-api-io-offer-snapshot.json", {})
        timing = _load_json(export / "latest-controlled-fallback-publication-timing-guard.json", {})
        price_guard = _load_json(export / "latest-controlled-fallback-price-integrity-guard.json", {})
        bzz_gap = _load_json(export / "latest-bzzoiro-context-gap-finalizer.json", {})
        bzz_gap_install = _load_json(export / "latest-bzzoiro-context-gap-finalizer-install.json", {})
        max_policy = _load_json(REPORT_PATH, {})

        api = report.get("api") if isinstance(report.get("api"), dict) else {}
        coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
        counts = truth.get("counts") if isinstance(truth.get("counts"), dict) else {}
        if not counts and isinstance(normalizer.get("counts"), dict):
            counts = normalizer.get("counts")
        windows = normalizer.get("window_counts") if isinstance(normalizer.get("window_counts"), dict) else {}

        inv_total = _get(counts, "matches_total") or _get(coverage, "day_inventory_total")
        with_books = _get(counts, "matches_with_2plus_price_confirmations")
        with_context2 = _get(counts, "matches_with_2plus_context_sources")
        with_context1 = _get(counts, "matches_with_context") or _get(coverage, "day_inventory_with_context")
        ready_model = _get(counts, "matches_ready_for_model") or _get(coverage, "ready_for_model")
        odds = api.get("odds_api_io", {}) if isinstance(api.get("odds_api_io"), dict) else {}
        bzz = api.get("bzzoiro", {}) if isinstance(api.get("bzzoiro"), dict) else {}
        sstats = api.get("sstats", {}) if isinstance(api.get("sstats"), dict) else {}
        sport = api.get("sportlogic", {}) if isinstance(api.get("sportlogic"), dict) else {}
        near = windows.get("0-4") if isinstance(windows.get("0-4"), dict) else {}

        bottlenecks: list[str] = []
        if _as_int(with_context2) < max(1, _as_int(with_books) // 2):
            bottlenecks.append("2plus_context_below_bookmaker_quorum")
        if _as_int(near.get("bookmaker_2plus")) > 0 and _as_int(near.get("context_2plus")) < _as_int(near.get("bookmaker_2plus")):
            bottlenecks.append("near_window_context_gap")
        if _as_int(bzz.get("secondary_offers_added")) <= 0:
            bottlenecks.append("bzzoiro_secondary_offers_zero")
        if _as_int(sstats.get("team_form_contexts")) <= 0:
            bottlenecks.append("sstats_team_form_zero")
        if _as_int(sport.get("matched")) <= 0 and _as_int(sport.get("fixtures_fetched")) <= 0:
            bottlenecks.append("sportlogic_no_fixtures_or_matches")

        payload = {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "status": "ok",
            "audit_source": "api_maximum_coverage_runtime_patch_atexit",
            "max_policy_installed": bool(max_policy),
            "inventory": {
                "matches_total": _as_int(inv_total),
                "with_1plus_context": _as_int(with_context1),
                "with_2plus_context": _as_int(with_context2),
                "with_2plus_bookmakers": _as_int(with_books),
                "ready_for_model": _as_int(ready_model),
            },
            "near_window_0_4h": near,
            "providers": {
                "odds_api_io": odds,
                "bzzoiro": bzz,
                "sstats": sstats,
                "sportlogic": sport,
            },
            "odds_api_snapshot": {
                "rows_count": _as_int(snapshot.get("rows_count")),
                "matches_count": _as_int(snapshot.get("matches_count")),
                "same_side_2plus_books": _as_int(snapshot.get("matches_with_2plus_books_same_side_market")),
            },
            "bookmaker_backfill": {
                "mapped_matches": _as_int(backfill.get("mapped_matches")),
                "changed_inventory_rows": _as_int(backfill.get("changed_inventory_rows")),
                "changed_truth_rows": _as_int(backfill.get("changed_truth_rows")),
                "offer_rows_from_snapshot": _as_int(backfill.get("offer_rows_from_snapshot")),
            },
            "guards": {
                "timing_deferred_total": _as_int(timing.get("deferred_total")),
                "price_integrity_removed_total": _as_int(price_guard.get("removed_total")),
            },
            "bzzoiro_context_gap": {
                "runtime": bzz_gap,
                "install": bzz_gap_install,
            },
            "bottlenecks": bottlenecks,
            "next_actions": [
                "Do not relax publication guards.",
                "If near_window_context_gap persists, prioritize Bzzoiro/SStats context only for 0-4h bookmaker-qualified matches.",
                "If sportlogic_no_fixtures_or_matches persists, verify SportLogic base URL/auth/date contract outside publication pipeline.",
                "If bzzoiro_secondary_offers_zero persists, keep Bzzoiro as context/confirmation, not price source, until comparison odds parse is fixed.",
            ],
        }
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        AUDIT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def install() -> None:
    changed_env = _apply_env_policy()
    patches = {}
    patches.update(_patch_bzzoiro_v2())
    patches.update(_patch_sportlogic())
    _write_report({"changed_env": changed_env, "patches": patches, "audit": "atexit_enabled"})
    try:
        atexit.register(_write_core_api_audit)
    except Exception:
        pass
    _write_core_api_audit()
