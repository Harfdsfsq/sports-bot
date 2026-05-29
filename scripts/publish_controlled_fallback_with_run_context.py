from __future__ import annotations

"""Run controlled fallback publisher with GitHub Actions metadata in Telegram.

The original publisher remains untouched. This wrapper patches only selected
runtime hooks and send_telegram, so publication guards remain in the original
publisher while HARIZON-specific safety patches are enforced at the wrapper edge.
"""

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from app.services.github_actions_context import append_github_run_reference, github_run_context, write_github_run_context

TARGET = Path(__file__).with_name("publish_controlled_fallback.py")
STATUS_PATH = Path(".data/exports/latest-controlled-fallback-run-context-wrapper.json")
PRE_ENRICH_PATH = Path(".data/exports/latest-controlled-fallback-prepublish-secondary-enrichment.json")


def _load_target() -> Any:
    spec = importlib.util.spec_from_file_location("publish_controlled_fallback_original", TARGET)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {TARGET}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_status(payload: dict[str, Any]) -> None:
    try:
        STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATUS_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _write_pre_enrich(payload: dict[str, Any]) -> None:
    try:
        PRE_ENRICH_PATH.parent.mkdir(parents=True, exist_ok=True)
        PRE_ENRICH_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


publisher = _load_target()
_original_send_telegram = publisher.send_telegram


# HARIZON patch: enforce honest A/B tier rules without changing the original
# publisher file.  A-tier must have 2+ independent odds sources and 2+
# confirmations.  B-tier is allowed with 1 odds source + 1 confirmation when the
# line movement guard has already approved the candidate or there is no next cron
# before kickoff.
def _tier_code(value: object) -> str:
    raw = str(value or "").strip().lower().replace("уровень", "").strip()
    if raw in {"a", "а", "tier_a", "a-tier"}:
        return "A"
    if raw in {"b", "б", "tier_b", "b-tier"}:
        return "B"
    if raw in {"c", "с", "tier_c", "c-tier"}:
        return "C"
    if "a" == raw[-1:] or "а" == raw[-1:]:
        return "A"
    if "b" == raw[-1:] or "б" == raw[-1:]:
        return "B"
    if "c" == raw[-1:] or "с" == raw[-1:]:
        return "C"
    return ""


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return default
        return int(float(str(raw)))
    except Exception:
        return default


def _env_bool(name: str, default: bool) -> bool:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return default
        return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}
    except Exception:
        return default


def _as_int(value: object, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


_original_tier_reasons = publisher.tier_reasons
_original_final_publish_guard_reasons = publisher.final_publish_guard_reasons


def tier_reasons_with_honest_ab_rules(tier: str, candidate: dict[str, Any], metrics: dict[str, Any]) -> list[str]:
    reasons = list(_original_tier_reasons(tier, candidate, metrics) or [])
    code = _tier_code(tier)
    odds_sources = _as_int(metrics.get("independent_odds_sources_count"), _as_int(metrics.get("odds_sources_count"), _as_int(metrics.get("sources_count"))))
    confirmations = _as_int(metrics.get("confirmation_sources_count"), _as_int(metrics.get("sources_count")))
    if code == "A":
        min_odds = _env_int("CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES", 2)
        min_conf = _env_int("CONTROLLED_FALLBACK_TIER_A_MIN_CONFIRMATION_SOURCES", 2)
        if odds_sources < min_odds:
            reasons.append(f"tier_a_odds_sources_below_min:{odds_sources}/{min_odds}")
        if confirmations < min_conf:
            reasons.append(f"tier_a_confirmation_sources_below_min:{confirmations}/{min_conf}")
    elif code == "B":
        # B-tier contract is intentionally 1+ independent odds source.  Do not let
        # global/A-tier env vars accidentally turn B-tier into A-tier.
        min_odds = 1
        min_conf = max(1, _env_int("CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES", 1))
        if odds_sources < min_odds:
            reasons.append(f"tier_b_odds_sources_below_min:{odds_sources}/{min_odds}")
        if confirmations < min_conf:
            reasons.append(f"tier_b_confirmation_sources_below_min:{confirmations}/{min_conf}")
    return reasons


def final_publish_guard_reasons_with_b_tier_lifecycle(candidate: dict[str, Any], metrics: dict[str, Any], tier: str) -> list[str]:
    reasons = list(_original_final_publish_guard_reasons(candidate, metrics, tier) or [])
    code = _tier_code(tier)
    if code != "B":
        return reasons

    confirmations = _as_int(metrics.get("confirmation_sources_count"), _as_int(metrics.get("sources_count")))
    min_conf = max(1, _env_int("CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES", 1))
    if confirmations >= min_conf:
        prefix = "controlled_fallback_confirmation_sources_below_min:"
        reasons = [r for r in reasons if not str(r).startswith(prefix)]

    # Remove any accidental inherited A-tier odds-source requirement from B-tier.
    reasons = [r for r in reasons if not str(r).startswith("tier_b_odds_sources_below_min:")]

    movement = metrics.get("line_movement") if isinstance(metrics.get("line_movement"), dict) else {}
    status = str(movement.get("status") or "")
    if (
        _env_bool("CONTROLLED_FALLBACK_TIER_B_ALLOW_NO_NEXT_CRON", True)
        and bool(movement.get("passed"))
        and status == "publish_now_no_next_cron"
    ):
        reasons = [r for r in reasons if str(r) != "line_movement_not_confirmed:publish_now_no_next_cron"]
    return reasons


publisher.tier_reasons = tier_reasons_with_honest_ab_rules
publisher.final_publish_guard_reasons = final_publish_guard_reasons_with_b_tier_lifecycle


def send_telegram_with_run_context(message: Any):
    write_github_run_context()
    return _original_send_telegram(append_github_run_reference(message))


publisher.send_telegram = send_telegram_with_run_context
_write_status({"status": "installed", "wrapper": "controlled-fallback-github-run-reference", "github_actions": github_run_context(), "independent_ab_tier_rules": True})


def _run_helper(script_name: str, timeout_seconds: int = 70) -> dict[str, Any]:
    path = Path(__file__).with_name(script_name)
    if not path.exists():
        return {"script": script_name, "status": "missing"}
    try:
        proc = subprocess.run([sys.executable, str(path)], text=True, capture_output=True, timeout=timeout_seconds)
        return {
            "script": script_name,
            "status": "ok" if proc.returncode == 0 else "nonzero",
            "returncode": proc.returncode,
            "stdout_tail": proc.stdout[-1200:],
            "stderr_tail": proc.stderr[-1200:],
        }
    except subprocess.TimeoutExpired as exc:
        return {"script": script_name, "status": "timeout", "timeout_seconds": timeout_seconds, "stdout_tail": (exc.stdout or "")[-1200:] if isinstance(exc.stdout, str) else "", "stderr_tail": (exc.stderr or "")[-1200:] if isinstance(exc.stderr, str) else ""}
    except Exception as exc:
        return {"script": script_name, "status": "error", "error": f"{type(exc).__name__}: {exc}"}


def run_prepublish_secondary_enrichment() -> None:
    if not _env_bool("CONTROLLED_FALLBACK_PREPUBLISH_SECONDARY_ENRICHMENT", True):
        _write_pre_enrich({"status": "disabled"})
        return
    steps = [
        _run_helper("probe_targeted_secondary_sources.py", timeout_seconds=_env_int("SECONDARY_PROVIDER_PROBE_TIMEOUT_SECONDS", 60)),
        _run_helper("merge_targeted_secondary_context.py", timeout_seconds=20),
    ]
    _write_pre_enrich({"status": "ok", "steps": steps})


if __name__ == "__main__":
    run_prepublish_secondary_enrichment()
    raise SystemExit(publisher.main())
