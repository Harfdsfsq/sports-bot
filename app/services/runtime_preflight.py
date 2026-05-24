from __future__ import annotations

"""Native startup/preflight layer for production prediction runs.

The older codebase grew a long monkey-patch startup chain.  This module keeps
critical run preparation in normal, testable code and treats legacy runtime
extensions as optional compatibility hooks instead of the source of truth.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from importlib import import_module
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


SAFE_RUNTIME_DEFAULTS = {
    "STRICT_PRICE_INTEGRITY_ENABLED": "true",
    "STRICT_PRICE_INTEGRITY_MIN_PRICE_SOURCES": "2",
    "STRICT_PRICE_INTEGRITY_MIN_BOOKMAKERS": "2",
    "PUBLISH_REJECT_CONTEXT_AS_PRICE_CONFIRMATION": "false",
    "PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE": "true",
    "MIN_BOOKS_FOR_CONSENSUS": "2",
    "MIN_BOOKS_PUBLISH": "2",
    "HARIZON_UNIFIED_SCHEME_ENABLED": "true",
    "HARIZON_RUN_INTERVAL_HOURS": "2",
    "HARIZON_LINE_MOVEMENT_GUARD_ENABLED": "true",
    "PUBLISH_MIN_BOOKS": "2",
    "MIN_SOURCES_PUBLISH": "2",
    "PUBLISH_MIN_ODDS_SOURCES": "2",
    "MIN_CONTEXT_SOURCES_PUBLISH": "2",
    "PUBLISH_MIN_CONTEXT_SOURCES": "2",
    "MARKET_DERIVED_MIN_BOOKS": "2",
    "MARKET_DERIVED_MIN_SOURCES": "2",
    "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "true",
    "CONTROLLED_FALLBACK_REQUIRE_ODDS_SOURCE_DIVERSITY": "true",
    "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "2",
    "TELEGRAM_MIN_ODDS_SOURCES": "2",
    "MATCH_TOTAL_OVER15_MAX_REASONABLE_ODDS": "1.45",
    "MATCH_TOTAL_OVER15_MIN_EXACT_BOOKS": "3",
    "MATCH_TOTAL_OVER15_ABSOLUTE_PRICE_GUARD_ENABLED": "true",
    "MATCH_TOTAL_OVER15_ABSOLUTE_MAX_ODDS": "1.55",
    "ENABLE_QUARTER_TOTAL_LINES": "true",
    "QUARTER_TOTAL_MIN_BOOKS": "2",
}


DISCOVERY_FIRST_DEFAULTS = {
    "HARIZON_PROVIDER_TIER_STRATEGY_VERSION": "primary-three-v1-100-per-run",
    "HARIZON_PRIMARY_PROVIDERS": "odds_api_io,bzzoiro,sstats",
    "HARIZON_SUPPLEMENTAL_API_MODE": "top_pick_backfill_only",
    "SUPPLEMENTAL_PROVIDERS_REQUIRE_SHORTLIST": "true",
    "SUPPLEMENTAL_PROVIDERS_REQUIRE_MISSING_ROLE": "true",
    "SUPPLEMENTAL_BACKFILL_AFTER_PRIMARY_SHORTLIST": "true",
    "ODDS_API_IO_MAX_REQUESTS_PER_RUN": "200",
    "ODDS_API_IO_MAX_HTTP_REQUESTS_PER_RUN": "200",
    "ODDS_API_IO_ACCOUNT1_PER_RUN_MAX": "100",
    "ODDS_API_IO_ACCOUNT2_PER_RUN_MAX": "100",
    "BZZOIRO_MAX_HTTP_REQUESTS_PER_RUN": "100",
    "BZZOIRO_MAX_REQUESTS_PER_RUN": "100",
    "BZZOIRO_CONTEXT_MATCH_LIMIT": "300",
    "SSTATS_MAX_HTTP_REQUESTS_PER_RUN": "100",
    "SSTATS_MAX_REQUESTS_PER_RUN": "100",
    "SSTATS_CONTEXT_MATCH_LIMIT": "300",
    "SSTATS_DEEP_ENRICHMENT_ENABLED": "true",
    "SSTATS_DEEP_DETAIL_LIMIT_PER_RUN": "80",
    "SSTATS_GAME_DETAIL_LIMIT_PER_RUN": "8",
    "SSTATS_ODDS_RESCUE_LIMIT_PER_RUN": "120",
    "SSTATS_ODDS_RESCUE_ONLY_IF_ODDS_SOURCES_LT": "2",
    "PROVIDER_DAY_DISCOVERY_MAX_SECONDS": "120",
    "PROVIDER_DAY_DISCOVERY_TIMEOUT_SECONDS": "16",
    "PROVIDER_DAY_DISCOVERY_CONCURRENCY": "5",
    "PROVIDER_DAY_DISCOVERY_MIN_SCORE": "0.74",
    "SPORTLOGIC_ENABLED": "false",
    "ENABLE_SPORTLOGIC": "false",
    "SPORTLOGIC_MAX_REQUESTS_PER_RUN": "0",
    "DAY_INVENTORY_TARGET_SIZE": "300",
    "DAY_INVENTORY_MAX_MATCHES": "300",
    "HARIZON_SUPPLEMENTAL_API_MODE": "shortlist_and_missing_role_only",
}


LEGACY_DIRECT_INSTALLERS = (
    ("app.services.sstats_bzzoiro_odds_merge_patch", "install"),
    ("app.services.candidate_value_final_reinstall", "install"),
)

LEGACY_FINAL_INSTALLERS = (
    ("app.services.bzzoiro_exact_offer_bridge_patch", "install"),
    ("app.services.candidate_factory_runtime_diagnostics", "install"),
)


@dataclass
class PreflightReport:
    stage: str
    safe_defaults_applied: int = 0
    phase_policy: dict[str, Any] = field(default_factory=dict)
    discovery_first: dict[str, Any] = field(default_factory=dict)
    legacy_extensions: dict[str, Any] = field(default_factory=dict)


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def setdefault_env(values: dict[str, str]) -> int:
    applied = 0
    for key, value in values.items():
        if os.getenv(key) is None:
            os.environ[key] = value
            applied += 1
    return applied


def _set_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ[str(key)] = str(value)


def _local_hour() -> int:
    tz_name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"
    try:
        tz = ZoneInfo(str(tz_name))
    except Exception:
        tz = timezone.utc
    return datetime.now(timezone.utc).astimezone(tz).hour


def _runtime_phase() -> str:
    explicit = str(os.getenv("HARIZON_RUN_PHASE") or os.getenv("RUN_PHASE") or "").strip().lower()
    if explicit:
        return explicit
    hour = _local_hour()
    if 0 <= hour <= 2:
        return "full_inventory"
    if 3 <= hour <= 10:
        return "morning_backfill"
    return "live_refresh"


def _common_prediction_contract() -> dict[str, str]:
    return {
        "HARIZON_PHASE_POLICY_ENABLED": "true",
        "DAY_INVENTORY_USE_FOR_RUN": "true",
        "DAY_INVENTORY_NEAR_WINDOW_PRIORITY": "true",
        "DAY_INVENTORY_NEAR_WINDOW_HOURS": "12",
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "true",
        "PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE": "true",
        "MIN_BOOKS_FOR_CONSENSUS": "2",
        "MIN_BOOKS_PUBLISH": "2",
        "MIN_SOURCES_PUBLISH": "2",
        "MARKET_DERIVED_MIN_BOOKS": "2",
        "MARKET_DERIVED_MIN_SOURCES": "2",
        "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "2",
        "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "true",
        "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "true",
        "CONTROLLED_FALLBACK_VISIBLE_MIN_CANONICAL_EV_PCT": "0.0",
        "CONTROLLED_FALLBACK_VISIBLE_MIN_CANONICAL_EDGE_PP": "0.0",
        "TELEGRAM_MAIN_PICK_MIN_ODDS_SOURCES": "2",
        "TELEGRAM_MAIN_PICK_MIN_EDGE_PP": "3.0",
        "SECONDARY_ODDS_RESCUE_ENABLED": "true",
        "BZZOIRO_ODDS_REKEY_ENABLED": "true",
        "MARKET_DERIVED_SINGLE_SNAPSHOT_CONSENSUS_ENABLED": "true",
    }


def _phase_env(phase: str) -> dict[str, str]:
    env = {
        "MATCH_BOOTSTRAP_PROVIDER": "odds_api_io",
        "DAY_INVENTORY_BOOTSTRAP_PROVIDER": "odds_api_io",
        "FIXTURE_EXPANSION_ENABLED": "true",
        "RUN_DAYS_AHEAD": "1",
        "SECONDARY_ODDS_RESCUE_TRIGGER": "thin_primary_market_depth",
    }
    if phase == "full_inventory":
        env.update({
            "PUBLISH_WINDOW_HOURS": "24",
            "MAX_MATCHES_FOR_ODDS_FETCH": "900",
            "ANALYSIS_MATCH_CAP_PER_RUN": "900",
            "CONTEXT_ENRICHMENT_MATCH_LIMIT": "120",
        })
    elif phase == "morning_backfill":
        env.update({
            "PUBLISH_WINDOW_HOURS": "12",
            "MAX_MATCHES_FOR_ODDS_FETCH": "650",
            "ANALYSIS_MATCH_CAP_PER_RUN": "650",
            "CONTEXT_ENRICHMENT_MATCH_LIMIT": "260",
        })
    else:
        env.update({
            "PUBLISH_WINDOW_HOURS": "12",
            "MAX_MATCHES_FOR_ODDS_FETCH": "520",
            "ANALYSIS_MATCH_CAP_PER_RUN": "520",
            "CONTEXT_ENRICHMENT_MATCH_LIMIT": "240",
        })
    return env


class RuntimePreflight:
    def __init__(self, settings: Any | None = None, *, export_dir: str | Path = ".data/exports") -> None:
        self.settings = settings
        self.export_dir = Path(export_dir)

    def apply_safe_defaults(self) -> int:
        applied = setdefault_env(SAFE_RUNTIME_DEFAULTS)
        self._install_native_integrity_hooks()
        return applied

    def apply_phase_policy(self) -> dict[str, Any]:
        phase = _runtime_phase()
        phase_values = _phase_env(phase)
        _set_env({"HARIZON_RUN_PHASE_EFFECTIVE": phase})
        _set_env(_common_prediction_contract())
        _set_env(phase_values)

        report: dict[str, Any] = {
            "enabled": True,
            "phase": phase,
            "env": phase_values,
        }
        try:
            self.export_dir.mkdir(parents=True, exist_ok=True)
            out = self.export_dir / "latest-run-phase-policy.env"
            lines = [f"HARIZON_RUN_PHASE_EFFECTIVE={phase}"] + [f"{key}={value}" for key, value in sorted(phase_values.items())]
            out.write_text("\n".join(lines) + "\n", encoding="utf-8")
            report["path"] = str(out)
        except Exception as exc:
            report["write_error"] = f"{type(exc).__name__}: {exc}"
            logger.debug("failed to write runtime phase policy report", exc_info=True)
        return report

    def prepare_discovery_first_inventory(self) -> dict[str, Any]:
        if not _truthy(os.getenv("RUNBOT_DISCOVERY_FIRST_PREPARE_ENABLED"), True):
            return {"enabled": False, "reason": "disabled"}
        if os.getenv("RUNBOT_DISCOVERY_FIRST_PREPARE_RUNNING") == "1":
            return {"enabled": True, "status": "skipped_reentrant"}

        os.environ["RUNBOT_DISCOVERY_FIRST_PREPARE_RUNNING"] = "1"
        setdefault_env(DISCOVERY_FIRST_DEFAULTS)
        try:
            from scripts import runbot_discovery_first_prepare

            result = runbot_discovery_first_prepare.main()
            return {"enabled": True, "status": "ok", "result": result}
        except Exception as exc:
            logger.warning("discovery-first runbot preparation failed; continuing run-once: %s: %s", type(exc).__name__, exc)
            return {"enabled": True, "status": "error_ignored", "error": f"{type(exc).__name__}: {exc}"}
        finally:
            os.environ.pop("RUNBOT_DISCOVERY_FIRST_PREPARE_RUNNING", None)

    def install_legacy_runtime_extensions(self, stage: str = "pre_runner") -> dict[str, Any]:
        if not _truthy(os.getenv("LEGACY_RUNTIME_EXTENSIONS_ENABLED"), True):
            return {"enabled": False, "reason": "disabled"}

        results: dict[str, Any] = {}
        for module_path, attr in LEGACY_DIRECT_INSTALLERS:
            results[module_path] = self._run_installer(module_path, attr, stage)
        try:
            from app.services import runtime_startup_chain

            results["app.services.runtime_startup_chain"] = runtime_startup_chain.install_all()
        except Exception as exc:
            results["app.services.runtime_startup_chain"] = f"{type(exc).__name__}: {exc}"
            logger.warning("runtime startup chain install failed at %s: %s: %s", stage, type(exc).__name__, exc)
        for module_path, attr in LEGACY_FINAL_INSTALLERS:
            results[f"{module_path}:final"] = self._run_installer(module_path, attr, stage)
        return {"enabled": True, "stage": stage, "results": results}

    def run_before_prediction(self, stage: str = "after_discovery_before_runner") -> PreflightReport:
        report = PreflightReport(stage=stage)
        report.safe_defaults_applied = self.apply_safe_defaults()
        report.phase_policy = self.apply_phase_policy()
        report.discovery_first = self.prepare_discovery_first_inventory()
        report.legacy_extensions = self.install_legacy_runtime_extensions(stage=stage)
        self.write_report(report)
        return report

    def write_report(self, report: PreflightReport) -> None:
        try:
            self.export_dir.mkdir(parents=True, exist_ok=True)
            payload = {
                "stage": report.stage,
                "safe_defaults_applied": report.safe_defaults_applied,
                "phase_policy": report.phase_policy,
                "discovery_first": report.discovery_first,
                "legacy_extensions": report.legacy_extensions,
            }
            (self.export_dir / "latest-runtime-preflight.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        except Exception:
            logger.debug("failed to write runtime preflight report", exc_info=True)

    @staticmethod
    def _install_native_integrity_hooks() -> None:
        for module_path in (
            "app.services.harizon_unified_scheme_runtime",
            "app.services.api_runtime_enhancements",
            "app.services.market_integrity",
            "app.providers.odds_api_io_startup_compat",
        ):
            try:
                module = import_module(module_path)
                installer = getattr(module, "install", None)
                if callable(installer):
                    installer()
            except Exception:
                logger.debug("native integrity hook failed: %s", module_path, exc_info=True)

    @staticmethod
    def _run_installer(module_path: str, attr: str, stage: str) -> Any:
        try:
            module = import_module(module_path)
            installer = getattr(module, attr)
            return installer()
        except Exception as exc:
            logger.warning("legacy runtime extension failed at %s: %s: %s: %s", stage, module_path, type(exc).__name__, exc)
            return f"{type(exc).__name__}: {exc}"
