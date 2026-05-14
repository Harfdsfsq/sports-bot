from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _script_name() -> str:
    return Path(str(sys.argv[0] or "")).name


def _argv_text() -> str:
    return " ".join(str(x) for x in sys.argv)


def _is_main_run_once() -> bool:
    text = _argv_text()
    return os.getenv("HARIZON_FORCE_RUNTIME_PATCH_INSTALL") == "1" or (
        "run-once" in text and ("app.cli" in text or "python -m app.cli" in text or _script_name() in {"app.cli", "python -m app.cli"})
    )


def _is_readonly_helper() -> bool:
    name = _script_name()
    return (
        name.startswith("send_harizon_telegram_run_report")
        or name in {
            "publish_controlled_fallback.py",
            "publish_controlled_fallback_guarded.py",
            "day_inventory_cumulative_coverage.py",
            "apply_publication_family_policy.py",
            "apply_provider_quota_governor.py",
            "apply_provider_request_budget.py",
            "apply_per_run_api_quota_contract.py",
        }
        or os.getenv("HARIZON_CONTROLLED_FALLBACK_REDIRECTED") == "1"
    )


def _redirect_controlled_fallback_entrypoint() -> None:
    try:
        if os.getenv("HARIZON_CONTROLLED_FALLBACK_REDIRECTED"):
            return
        if _script_name() != "publish_controlled_fallback.py":
            return
        target = SCRIPTS / "publish_controlled_fallback_guarded.py"
        if not target.exists():
            return
        os.environ["HARIZON_CONTROLLED_FALLBACK_REDIRECTED"] = "1"
        os.execv(sys.executable, [sys.executable, str(target), *sys.argv[1:]])
    except Exception as exc:
        try:
            print(f"root sitecustomize controlled fallback redirect skipped: {type(exc).__name__}: {exc}")
        except Exception:
            pass


_redirect_controlled_fallback_entrypoint()


def _set(name: str, value: str) -> None:
    os.environ[name] = str(value)


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


def _apply_common_prediction_contract() -> None:
    common = {
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
    for key, value in common.items():
        _set(key, value)


def _phase_env(phase: str) -> dict[str, str]:
    base = {
        "MATCH_BOOTSTRAP_PROVIDER": "odds_api_io",
        "DAY_INVENTORY_BOOTSTRAP_PROVIDER": "odds_api_io",
        "FIXTURE_EXPANSION_ENABLED": "true",
        "RUN_DAYS_AHEAD": "1",
        "SECONDARY_ODDS_RESCUE_TRIGGER": "thin_primary_market_depth",
    }
    if phase == "full_inventory":
        base.update({
            "PUBLISH_WINDOW_HOURS": "24",
            "MAX_MATCHES_FOR_ODDS_FETCH": "900",
            "ANALYSIS_MATCH_CAP_PER_RUN": "900",
            "CONTEXT_ENRICHMENT_MATCH_LIMIT": "120",
        })
    elif phase == "morning_backfill":
        base.update({
            "PUBLISH_WINDOW_HOURS": "12",
            "MAX_MATCHES_FOR_ODDS_FETCH": "650",
            "ANALYSIS_MATCH_CAP_PER_RUN": "650",
            "CONTEXT_ENRICHMENT_MATCH_LIMIT": "260",
        })
    else:
        base.update({
            "PUBLISH_WINDOW_HOURS": "12",
            "MAX_MATCHES_FOR_ODDS_FETCH": "520",
            "ANALYSIS_MATCH_CAP_PER_RUN": "520",
            "CONTEXT_ENRICHMENT_MATCH_LIMIT": "240",
        })
    return base


def _apply_phase_policy() -> None:
    phase = _runtime_phase()
    _set("HARIZON_RUN_PHASE_EFFECTIVE", phase)
    _apply_common_prediction_contract()
    env = _phase_env(phase)
    for key, value in env.items():
        _set(key, value)
    try:
        out = ROOT / ".data" / "exports" / "latest-run-phase-policy.env"
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"HARIZON_RUN_PHASE_EFFECTIVE={phase}"] + [f"{k}={v}" for k, v in sorted(env.items())]
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


try:
    _apply_phase_policy()
except Exception as exc:
    try:
        print(f"root sitecustomize phase policy skipped: {type(exc).__name__}: {exc}")
    except Exception:
        pass

# No model/provider runtime wrappers are installed here. usercustomize.py installs
# the gated runtime_startup_chain only for the main app.cli run-once process.
