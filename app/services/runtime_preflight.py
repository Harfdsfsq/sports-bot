from __future__ import annotations

"""Deterministic production preflight.

Deployment configuration is the source of truth. This module fills missing
values but never silently replaces workflow values. In particular, the
rules-compliant B tier remains publishable.
"""

import json
import logging
import os
from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Any

from app.services.day_inventory_preflight import repair_runtime_json_files

logger = logging.getLogger(__name__)

SAFE_RUNTIME_DEFAULTS: dict[str, str] = {
    "PUBLICATION_PROFILE": "rules_ab",
    "PUBLISH_ALLOW_B_TIER": "true",
    "PUBLISH_COVERAGE_TIER_MODE": "a_or_b",
    "HARIZON_PUBLICATION_TIER_MODE": "a_or_b",
    "PUBLISH_TIER_A_MIN_BOOKS": "2",
    "PUBLISH_TIER_A_MIN_ODDS_SOURCES": "2",
    "PUBLISH_TIER_A_MIN_CONTEXT_SOURCES": "2",
    "PUBLISH_TIER_B_MIN_BOOKS": "2",
    "PUBLISH_TIER_B_MIN_ODDS_SOURCES": "1",
    "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "1",
    "PUBLISH_MIN_BOOKS": "2",
    "MIN_BOOKS_PUBLISH": "2",
    "PUBLISH_MIN_ODDS_SOURCES": "1",
    "MIN_SOURCES_PUBLISH": "1",
    "PUBLISH_MIN_CONTEXT_SOURCES": "1",
    "MIN_CONTEXT_SOURCES_PUBLISH": "1",
    "STRICT_PRICE_INTEGRITY_ENABLED": "true",
    "STRICT_PRICE_INTEGRITY_MIN_PRICE_SOURCES": "1",
    "STRICT_PRICE_INTEGRITY_MIN_BOOKMAKERS": "2",
    "MIN_BOOKS_FOR_CONSENSUS": "2",
    "PUBLISH_REJECT_CONTEXT_AS_PRICE_CONFIRMATION": "true",
    "PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE": "true",
    "ODDS_SOURCE_INDEPENDENCE_ENABLED": "true",
    "BOOKMAKER_QUORUM_ENABLED": "true",
    "CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B": "true",
    "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "true",
    "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "false",
    "CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM": "false",
    "CONTROLLED_FALLBACK_REQUIRE_ODDS_SOURCE_DIVERSITY": "true",
    "CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES": "true",
    "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "1",
    "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": "1",
    "CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES": "1",
    "TELEGRAM_MIN_ODDS_SOURCES": "1",
    "HARIZON_TELEGRAM_PICK_SAFETY_ENABLED": "true",
    "TELEGRAM_BLOCK_C_SIGNAL_PROFILE": "true",
    "TELEGRAM_BLOCK_SINGLE_SOURCE_NON_CORE": "false",
    "PUBLISH_REQUIRE_LINE_MOVEMENT": "true",
    "LINE_MOVEMENT_MIN_SNAPSHOTS": "2",
    "LINE_MOVEMENT_MIN_MINUTES_BETWEEN_SNAPSHOTS": "8",
    "LINE_MOVEMENT_MAX_STALE_MINUTES": "360",
    "LINE_MOVEMENT_ALLOW_LAST_CHANCE_SINGLE_SNAPSHOT": "true",
    "LINE_MOVEMENT_LAST_CHANCE_MINUTES_TO_KICKOFF": "45",
    "FINAL_ENRICHMENT_ONLY_FOR_VALUE_CANDIDATES": "true",
    "FINAL_ENRICHMENT_FALLBACK_NEAREST_MATCH_LIMIT": "0",
    "DAY_INVENTORY_TARGET_SIZE": "300",
    "DAY_INVENTORY_MAX_MATCHES": "300",
    "DAY_INVENTORY_PRESERVE_CACHED_EVIDENCE": "true",
    "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
    "FALLBACK_PUBLISH_MODE_ENABLED": "false",
    "MODEL_RELAXED_FALLBACK_ENABLED": "false",
    "FORCE_PUBLISH_WHEN_EMPTY_ENABLED": "false",
    "QUALITY_EMERGENCY_PUBLISH_ENABLED": "false",
    "QUALITY_LAST_RESORT_PUBLISH_ENABLED": "false",
    "HISTORICAL_SEGMENT_RELIEF_ENABLED": "false",
    "NO_BET_QUALITY_SCORE_ENABLED": "false",
    "FOCUSED_ALPHA_RUNTIME_POLICY_ENABLED": "false",
    "HARIZON_AUTONOMOUS_ACCUMULATION_MODE": "false",
    "LEGACY_RUNTIME_EXTENSIONS_ENABLED": "false",
}

DISCOVERY_FIRST_DEFAULTS = {
    "HARIZON_PRIMARY_PROVIDERS": "odds_api_io,bzzoiro,sstats,sportlogic",
    "HARIZON_SUPPLEMENTAL_API_MODE": "top_pick_backfill_only",
    "SUPPLEMENTAL_PROVIDERS_REQUIRE_SHORTLIST": "true",
    "SUPPLEMENTAL_PROVIDERS_REQUIRE_MISSING_ROLE": "true",
    "PROVIDER_DAY_DISCOVERY_MAX_SECONDS": "120",
    "PROVIDER_DAY_DISCOVERY_TIMEOUT_SECONDS": "16",
    "PROVIDER_DAY_DISCOVERY_CONCURRENCY": "5",
}

# Compatibility export only; this policy is no longer applied authoritatively.
AUTONOMOUS_ACCUMULATION_POLICY = SAFE_RUNTIME_DEFAULTS
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
    discovery_first: dict[str, Any] = field(default_factory=dict)
    legacy_extensions: dict[str, Any] = field(default_factory=dict)

def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    return default if not raw else raw in {"1", "true", "yes", "on", "force"}

def setdefault_env(values: dict[str, str]) -> int:
    applied = 0
    for key, value in values.items():
        if os.getenv(key) is None:
            os.environ[key] = value
            applied += 1
    return applied

def apply_authoritative_env(values: dict[str, str]) -> int:
    """Backward-compatible helper; production preflight never calls it."""
    changed = 0
    for key, value in values.items():
        if os.getenv(key) != value:
            os.environ[key] = value
            changed += 1
    return changed

class RuntimePreflight:
    def __init__(self, settings: Any | None = None, *, export_dir: str | Path = ".data/exports") -> None:
        self.settings = settings
        self.export_dir = Path(export_dir)

    def apply_safe_defaults(self) -> int:
        applied = setdefault_env(SAFE_RUNTIME_DEFAULTS)
        self._install_native_integrity_hooks()
        return applied

    def apply_phase_policy(self, phase: str = "run-once") -> dict[str, Any]:
        payload: dict[str, Any] = {
            "stage": "phase_policy", "phase": str(phase or "run-once"),
            "safe_defaults_applied": 0, "runtime_json_repair": {}, "status": "ok",
        }
        try:
            payload["safe_defaults_applied"] = self.apply_safe_defaults()
        except Exception as exc:
            payload.update(status="safe_defaults_error_ignored", safe_defaults_error=f"{type(exc).__name__}: {exc}")
        try:
            repair_runtime_json_files()
            payload["runtime_json_repair"] = {"status": "ok"}
        except Exception as exc:
            payload["runtime_json_repair"] = {"status": "error_ignored", "error": f"{type(exc).__name__}: {exc}"}
        self._write_json("latest-runtime-phase-policy.json", payload)
        return payload

    def prepare_discovery_first_inventory(self) -> dict[str, Any]:
        if not _truthy(os.getenv("RUNBOT_DISCOVERY_FIRST_PREPARE_ENABLED"), True):
            return {"enabled": False, "reason": "disabled"}
        if os.getenv("RUNBOT_DISCOVERY_FIRST_PREPARE_RUNNING") == "1":
            return {"enabled": True, "status": "skipped_reentrant"}
        os.environ["RUNBOT_DISCOVERY_FIRST_PREPARE_RUNNING"] = "1"
        setdefault_env(DISCOVERY_FIRST_DEFAULTS)
        try:
            from scripts import runbot_discovery_first_prepare
            return {"enabled": True, "status": "ok", "result": runbot_discovery_first_prepare.main()}
        except Exception as exc:
            logger.warning("discovery-first preparation failed; continuing: %s", exc)
            return {"enabled": True, "status": "error_ignored", "error": f"{type(exc).__name__}: {exc}"}
        finally:
            os.environ.pop("RUNBOT_DISCOVERY_FIRST_PREPARE_RUNNING", None)

    def install_legacy_runtime_extensions(self, stage: str = "pre_runner") -> dict[str, Any]:
        if not _truthy(os.getenv("LEGACY_RUNTIME_EXTENSIONS_ENABLED"), False):
            return {"enabled": False, "reason": "disabled"}
        results: dict[str, Any] = {}
        for module_path, attr in LEGACY_DIRECT_INSTALLERS:
            results[module_path] = self._run_installer(module_path, attr, stage)
        try:
            from app.services import runtime_startup_chain
            results["app.services.runtime_startup_chain"] = runtime_startup_chain.install_all()
        except Exception as exc:
            results["app.services.runtime_startup_chain"] = f"{type(exc).__name__}: {exc}"
        for module_path, attr in LEGACY_FINAL_INSTALLERS:
            results[f"{module_path}:final"] = self._run_installer(module_path, attr, stage)
        return {"enabled": True, "stage": stage, "results": results}

    def run_before_prediction(self, stage: str = "after_discovery_before_runner") -> PreflightReport:
        report = PreflightReport(stage=stage)
        report.safe_defaults_applied = self.apply_safe_defaults()
        try:
            repair_runtime_json_files()
        except Exception:
            logger.warning("runtime JSON repair failed", exc_info=True)
        report.discovery_first = self.prepare_discovery_first_inventory()
        report.legacy_extensions = self.install_legacy_runtime_extensions(stage=stage)
        self.write_report(report)
        return report

    def write_report(self, report: PreflightReport) -> None:
        keys = ("PUBLISH_ALLOW_B_TIER", "PUBLISH_TIER_A_MIN_ODDS_SOURCES", "PUBLISH_TIER_A_MIN_CONTEXT_SOURCES", "PUBLISH_TIER_B_MIN_ODDS_SOURCES", "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES", "MIN_BOOKS_PUBLISH", "FINAL_ENRICHMENT_FALLBACK_NEAREST_MATCH_LIMIT")
        self._write_json("latest-runtime-preflight.json", {
            "stage": report.stage,
            "safe_defaults_applied": report.safe_defaults_applied,
            "publication_profile": os.getenv("PUBLICATION_PROFILE", "rules_ab"),
            "effective_contract": {key: os.getenv(key) for key in keys},
            "discovery_first": report.discovery_first,
            "legacy_extensions": report.legacy_extensions,
        })

    def _write_json(self, name: str, payload: dict[str, Any]) -> None:
        try:
            self.export_dir.mkdir(parents=True, exist_ok=True)
            (self.export_dir / name).write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except Exception:
            logger.debug("failed to write preflight report", exc_info=True)

    @staticmethod
    def _install_native_integrity_hooks() -> None:
        # Excludes autonomous/focused-alpha patches that replace the A/B contract.
        for module_path in (
            "app.services.inventory_coverage_source_runtime_patch",
            "app.services.near_window_priority_runtime_patch",
            "app.services.api_runtime_enhancements",
            "app.services.market_integrity",
            "app.services.quality_stage_gate",
            "app.providers.odds_api_io_startup_compat",
            "scripts.telegram_controlled_pick_safety",
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
            return getattr(module, attr)()
        except Exception as exc:
            logger.warning("legacy extension failed at %s: %s", stage, module_path)
            return f"{type(exc).__name__}: {exc}"
