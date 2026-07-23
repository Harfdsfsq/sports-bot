"""Reassert the Bzzoiro outage fallback after native/source-matrix installers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPORT_PATH = Path(".data/exports/latest-bzzoiro-v2-outage-reassert.json")
_INSTALLED = False


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def reassert() -> dict[str, Any]:
    from app.providers.bzzoiro_v2 import BzzoiroContextProvider
    from app.services import bzzoiro_v2_outage_fallback as fallback
    from app.services import sstats_bzzoiro_odds_merge_patch as merge_module

    result: dict[str, Any] = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "publication_contract_relaxed": False,
    }

    current_context = BzzoiroContextProvider.fetch_context
    if not getattr(current_context, "_harizon_bzzoiro_v2_outage_fallback", False):
        wrapped_context = fallback._context_factory(current_context)
        wrapped_context._harizon_batch_context_persisted = True  # type: ignore[attr-defined]
        BzzoiroContextProvider.fetch_context = wrapped_context  # type: ignore[assignment]
        result["context"] = "wrapped"
    else:
        result["context"] = "already_wrapped"

    # A healthy v2 origin may still return an empty prediction batch.  Reassert the
    # bounded v1 empty-result fallback after the outage wrapper so native installers
    # cannot silently remove it later in preflight.
    try:
        from app.services import bzzoiro_empty_prediction_fallback as empty_fallback

        result["empty_prediction_context"] = empty_fallback.reassert()
    except Exception as exc:
        result["empty_prediction_context"] = {
            "status": "error",
            "error": f"{type(exc).__name__}: {exc}",
            "publication_contract_relaxed": False,
        }

    current_merge = merge_module.fetch_bzzoiro
    if not getattr(current_merge, "_harizon_bzzoiro_v2_outage_fallback", False):
        wrapped_merge = fallback._merge_fetch_factory(current_merge, merge_module)
        wrapped_merge._harizon_bzzoiro_odds_persisted = True  # type: ignore[attr-defined]
        merge_module.fetch_bzzoiro = wrapped_merge
        result["merge_offers"] = "wrapped"
    else:
        result["merge_offers"] = "already_wrapped"

    current_direct = BzzoiroContextProvider.fetch_offers
    if not getattr(current_direct, "_harizon_bzzoiro_shared_batch_offers", False):
        BzzoiroContextProvider.fetch_offers = fallback._direct_offer_factory(merge_module)  # type: ignore[assignment]
        result["direct_offers"] = "wrapped"
    else:
        result["direct_offers"] = "already_wrapped"

    _write(result)
    return result


def install() -> dict[str, Any]:
    global _INSTALLED
    from app.services.runtime_preflight import RuntimePreflight

    if _INSTALLED:
        return {"status": "already_installed", "runtime": reassert()}

    current_apply = RuntimePreflight.apply_safe_defaults
    if not getattr(current_apply, "_harizon_bzzoiro_outage_reassert", False):

        def apply_safe_defaults(self: Any) -> int:
            changed = current_apply(self)
            self._harizon_bzzoiro_outage_reassert = reassert()
            return changed

        apply_safe_defaults._harizon_bzzoiro_outage_reassert = True  # type: ignore[attr-defined]
        RuntimePreflight.apply_safe_defaults = apply_safe_defaults  # type: ignore[assignment]

    current_run = RuntimePreflight.run_before_prediction
    if not getattr(current_run, "_harizon_bzzoiro_outage_reassert", False):

        def run_before_prediction(self: Any, stage: str = "after_discovery_before_runner"):
            report = current_run(self, stage)
            activation = reassert()
            try:
                discovery = report.discovery_first if isinstance(report.discovery_first, dict) else {}
                discovery["bzzoiro_v2_outage_reassert"] = activation
                report.discovery_first = discovery
                self.write_report(report)
            except Exception:
                pass
            return report

        run_before_prediction._harizon_bzzoiro_outage_reassert = True  # type: ignore[attr-defined]
        RuntimePreflight.run_before_prediction = run_before_prediction  # type: ignore[assignment]

    _INSTALLED = True
    result = {
        "status": "installed",
        "runtime": reassert(),
        "apply_safe_defaults_wrapped": True,
        "run_before_prediction_wrapped": True,
        "publication_contract_relaxed": False,
    }
    _write(result)
    return result


__all__ = ["install", "reassert"]
