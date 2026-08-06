from __future__ import annotations

"""Apply provider request-budget policy.

This layer is intentionally conservative and no longer hard-disables
API-Football or OddsPapi.  Daily/monthly-limited providers are controlled by the
final per-run contract in scripts/apply_per_run_api_quota_contract.py.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(".").resolve()
POLICY_PATH = ROOT / "config" / "provider_runtime_policy.json"
STATE_PATH = ROOT / ".data" / "provider_request_budget_state.json"
EXPORT_PATH = ROOT / ".data" / "exports" / "latest-provider-request-budget.json"
EFFECTIVE_RUNTIME_PATH = ROOT / ".data" / "exports" / "latest-harizon-runtime-policy.json"
MARKET_INTEGRITY_CHECK_PATH = ROOT / ".data" / "exports" / "latest-market-integrity-runtime-check.json"
GITHUB_ENV = os.getenv("GITHUB_ENV")
UTC = timezone.utc
MSK = ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")

# Only the broken Bookies API integration stays forcibly removed.  API-Football
# and OddsPapi are valid limited providers and may be enabled by the per-run
# contract when secrets are present.
REMOVED_PROVIDERS = {"bookies_api"}
LIMITED_PROVIDER_DISABLE_PREFIXES = ("API_FOOTBALL", "ODDSPAPI")


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def append_github_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ[str(key)] = str(value)
    if not GITHUB_ENV:
        for key in sorted(values):
            print(f"{key}={values[key]}")
        return
    with open(GITHUB_ENV, "a", encoding="utf-8") as fh:
        for key in sorted(values):
            fh.write(f"{key}={values[key]}\n")


def truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def env_present(keys: list[str]) -> bool:
    return any(str(os.getenv(key) or "").strip() for key in keys)


def prefix(provider: str) -> str:
    return provider.upper().replace("-", "_")


def disabled_env(provider: str) -> dict[str, str]:
    p = prefix(provider)
    return {
        f"{p}_PER_RUN_MAX": "0",
        f"{p}_MAX_HTTP_REQUESTS_PER_RUN": "0",
        f"{p}_REQUEST_BUDGET_GRANTED": "0",
    }


def decide_provider(provider: str, cfg: dict[str, Any], state_row: dict[str, Any], now_utc: datetime, reason_prefix: str = "") -> dict[str, Any]:
    """Compatibility decision helper used by provider budget tests.

    The production path now reads provider_runtime_policy.json through
    compute(), but tests and diagnostic scripts still import this smaller
    decision primitive.
    """

    provider_key = str(provider or "").strip().lower()
    event_name = str(os.getenv("GITHUB_EVENT_NAME") or "").strip()
    per_run_max = max(0, as_int(cfg.get("per_run_max"), as_int(cfg.get("grant"), 0)))
    if not truthy(cfg.get("enabled", True)):
        return {"provider": provider_key, "grant": 0, "reason": f"{reason_prefix}disabled_by_policy".strip(":")}
    if event_name == "workflow_dispatch" and cfg.get("manual_enabled") is False:
        return {"provider": provider_key, "grant": 0, "reason": f"{reason_prefix}manual_disabled_by_policy".strip(":")}

    allowed_hours = cfg.get("allowed_msk_hours")
    if isinstance(allowed_hours, list) and allowed_hours:
        local_hour = int(now_utc.astimezone(MSK).hour)
        allowed = {as_int(item, -1) for item in allowed_hours}
        if local_hour not in allowed:
            return {"provider": provider_key, "grant": 0, "reason": f"{reason_prefix}outside_allowed_hour:{local_hour}".strip(":")}

    daily_budget = cfg.get("safe_daily_budget")
    if daily_budget is not None:
        day_key = now_utc.astimezone(MSK).date().isoformat()
        daily = state_row.setdefault("daily", {})
        used = as_int((daily or {}).get(day_key), 0)
        limit = max(0, as_int(daily_budget, 0))
        if used >= limit:
            return {"provider": provider_key, "grant": 0, "reason": f"{reason_prefix}daily_budget_exhausted:{used}/{limit}".strip(":")}
        per_run_max = min(per_run_max, max(0, limit - used))

    monthly_budget = cfg.get("safe_monthly_budget")
    if monthly_budget is not None:
        month_key = now_utc.astimezone(MSK).strftime("%Y-%m")
        monthly = state_row.setdefault("monthly", {})
        used = as_int((monthly or {}).get(month_key), 0)
        limit = max(0, as_int(monthly_budget, 0))
        if used >= limit:
            return {"provider": provider_key, "grant": 0, "reason": f"{reason_prefix}monthly_budget_exhausted:{used}/{limit}".strip(":")}
        per_run_max = min(per_run_max, max(0, limit - used))

    grant = max(0, per_run_max)
    state_row["last_grant"] = grant
    state_row["last_decided_at"] = now_utc.isoformat()
    return {"provider": provider_key, "grant": grant, "reason": f"{reason_prefix}granted".strip(":") if grant > 0 else f"{reason_prefix}disabled_by_policy".strip(":")}


def build_env_for_decision(cfg: dict[str, Any], decision: dict[str, Any]) -> dict[str, str]:
    env = {str(k): str(v) for k, v in dict(cfg.get("env") or {}).items()}
    if as_int(decision.get("grant"), 0) <= 0:
        provider = str(decision.get("provider") or "").strip()
        if provider:
            env.update(disabled_env(provider))
        env.update({str(k): str(v) for k, v in dict(cfg.get("disable_env") or {}).items()})
    return env


def removed_provider_env() -> dict[str, str]:
    return {
        "ENABLE_BOOKIES_API": "false",
        "BOOKIES_API_ENABLED": "false",
        "BOOKIES_API_ODDS_FETCH_LIMIT": "0",
        "BOOKIES_API_REQUEST_BUDGET_GRANTED": "0",
        "BOOKIES_API_REQUEST_BUDGET_REASON": "removed_from_project",
    }


def _drop_legacy_limited_provider_disables(env: dict[str, str]) -> dict[str, str]:
    """Ignore stale JSON fields that disabled API-Football/OddsPapi globally."""
    out: dict[str, str] = {}
    for key, value in env.items():
        upper = str(key).upper()
        if any(upper.startswith(prefix) for prefix in LIMITED_PROVIDER_DISABLE_PREFIXES):
            continue
        out[str(key)] = str(value)
    return out


def manual_probe_without_force_publish() -> bool:
    if truthy(os.getenv("HARIZON_MANUAL_DRY_RUN")) or str(os.getenv("RUN_MODE") or "").strip().lower() == "dry_run":
        return True
    if str(os.getenv("GITHUB_EVENT_NAME") or "").strip() != "workflow_dispatch":
        return False
    return truthy(os.getenv("PUBLISH_DRY_RUN"))


def final_market_integrity_env() -> dict[str, str]:
    manual_dry_run = manual_probe_without_force_publish()
    fast_inventory = truthy(os.getenv("HARIZON_FAST_INVENTORY_LOCK") or os.getenv("DAY_INVENTORY_FAST_MODE") or "false")
    inventory_merge = "false" if fast_inventory else str(os.getenv("DAY_INVENTORY_FORCE_PROVIDER_MERGE") or "true").lower()
    runtime_version = "harizon-runtime-policy-v6-strict-full-inventory"
    env = {
        "HARIZON_RUNTIME_POLICY_VERSION": runtime_version,
        "PUBLISH_ALLOW_B_TIER": "true",
        "PUBLISH_COVERAGE_TIER_MODE": "hybrid",
        "CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B": "true",
        "HARIZON_EFFECTIVE_RUNTIME_POLICY_VERSION": runtime_version,
        "HARIZON_FAST_INVENTORY_LOCK": "true" if fast_inventory else "false",
        "HARIZON_MANUAL_DRY_RUN": str(manual_dry_run).lower(),
        "MANUAL_CONTROLLED_PUBLISH_ENABLED": str(os.getenv("MANUAL_CONTROLLED_PUBLISH_ENABLED") or "false").lower(),
        "PUBLISH_DRY_RUN": "true" if manual_dry_run or truthy(os.getenv("PUBLISH_DRY_RUN")) else "false",
        "CONTROLLED_FALLBACK_DRY_RUN": "true" if manual_dry_run else "false",
        "CONTROLLED_FALLBACK_SEND_TELEGRAM": "false" if manual_dry_run else "true",
        "CONTROLLED_FALLBACK_TELEGRAM_ENABLED": "false" if manual_dry_run else "true",
        "MATCH_BOOTSTRAP_PROVIDER": os.getenv("MATCH_BOOTSTRAP_PROVIDER") or "odds_api_io",
        "DAY_INVENTORY_BOOTSTRAP_PROVIDER": os.getenv("DAY_INVENTORY_BOOTSTRAP_PROVIDER") or "odds_api_io",
        "DAY_INVENTORY_FORCE_PROVIDER_MERGE": inventory_merge,
        "DAY_INVENTORY_USE_FOR_RUN": "true",
        "DAY_INVENTORY_COVERAGE_MAX_REBUILD": "false" if fast_inventory else str(os.getenv("DAY_INVENTORY_COVERAGE_MAX_REBUILD") or "true").lower(),
        "DAY_INVENTORY_NEAR_WINDOW_PRIORITY": "true",
        "DAY_INVENTORY_NEAR_WINDOW_HOURS": os.getenv("DAY_INVENTORY_NEAR_WINDOW_HOURS") or "12",
        "MARKET_DERIVED_MIN_BOOKS": "2",
        "MARKET_DERIVED_MIN_SOURCES": "2",
        "MARKET_DERIVED_CONSENSUS_RELIEF_MIN_BOOKS": "2",
        "MARKET_DERIVED_CONSENSUS_RELIEF_MIN_SOURCES": "2",
        "CONTROLLED_FALLBACK_MIN_INDEPENDENT_SOURCES": "1",
        "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "1",
        "CONTROLLED_FALLBACK_MIN_CONTEXT_SOURCES": "2",
        "CONTROLLED_FALLBACK_MIN_CONFIRMATION_SOURCES": "2",
        "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "true",
        "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "false",
        "CONTROLLED_FALLBACK_REQUIRE_2_CONTEXT_SOURCES_FOR_TELEGRAM": "false",
        "CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES": "false",
        "CONTROLLED_FALLBACK_REJECT_SINGLE_SOURCE_UNLESS_3_BOOKS": "true",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_STRICT": "true",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE": "78.0",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP": "8.0",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT": "15.0",
        "CONTROLLED_FALLBACK_ALLOWED_FAMILIES": "totals,spreads",
        "CONTROLLED_FALLBACK_TIER_A_ALLOWED_FAMILIES": "totals,spreads",
        "CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES": "totals,spreads",
        "CONTROLLED_FALLBACK_TIER_C_ALLOWED_FAMILIES": "",
        "CONTROLLED_FALLBACK_TIER_C_PUBLISH_ENABLED": "false",
        "CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS": "2",
        "CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES": "2",
        "CONTROLLED_FALLBACK_TIER_A_MIN_CONTEXT_SOURCES": "2",
        "CONTROLLED_FALLBACK_TIER_B_REQUIRE_ODDS_SOURCES": "true",
        "CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES": "false",
        "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_SINGLE_LINE_ENABLED": "true",
        "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_MIN_CONTEXT_SOURCES": "2",
        "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_MIN_CONFIDENCE": "76.0",
        "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_MIN_QUALITY": "78.0",
        "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_MIN_EDGE_PP": "4.0",
        "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_MIN_EV_PCT": "7.0",
        "CONTROLLED_FALLBACK_TIER_B_WEIGHTED_REQUIRE_XG_HARD_CONFIRMATION": "true",
        "CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS": "2",
        "CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES": "1",
        "CONTROLLED_FALLBACK_TIER_B_MIN_CONTEXT_SOURCES": "2",
        "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES": "2",
        "PUBLISH_TIER_B_MIN_CONTEXT_SOURCES": "2",
        "CONTROLLED_FALLBACK_TIER_B_MIN_CONFIRMATION_SOURCES": "2",
        "CONTROLLED_FALLBACK_FINAL_MIN_EV_PCT": "6.0",
        "CONTROLLED_FALLBACK_FINAL_MIN_EDGE_PP": "3.0",
        "DISABLE_SPREADS_UNTIL_HANDICAP_PARSER_VERIFIED": "false",
        "HANDICAP_PAIR_INTEGRITY_REQUIRED": "true",
        "SPREADS_PUBLICATION_ENABLED": "true",
        "TEAM_TOTALS_PUBLICATION_ENABLED": "false",
    }
    env.update(removed_provider_env())
    if manual_dry_run:
        env.update({
            "MIN_KICKOFF_LEAD_MINUTES": "0",
            "ADAPTIVE_MIN_KICKOFF_LEAD_ENABLED": "false",
            "ADAPTIVE_MIN_KICKOFF_LEAD_MINUTES": "0",
            "EMERGENCY_MIN_KICKOFF_LEAD_ENABLED": "true",
            "EMERGENCY_MIN_KICKOFF_LEAD_MINUTES": "0",
            "FORCE_RELAXED_MIN_KICKOFF_LEAD_ENABLED": "true",
            "FORCE_RELAXED_MIN_KICKOFF_LEAD_MINUTES": "0",
            "MANUAL_LATE_MODE_ENABLED": "true",
            "MANUAL_LATE_MIN_KICKOFF_LEAD_MINUTES": "0",
            "MANUAL_LATE_ADAPTIVE_MIN_KICKOFF_LEAD_MINUTES": "0",
        })
    return env


def market_integrity_check(env: dict[str, str], policy_version: str | None) -> dict[str, Any]:
    def families(name: str) -> set[str]:
        return {item.strip().lower() for item in str(env.get(name) or "").split(",") if item.strip()}
    failures: list[str] = []
    warnings: list[str] = []
    forbidden = {"teamtotals"}
    scopes = {
        "allowed": families("CONTROLLED_FALLBACK_ALLOWED_FAMILIES"),
        "tier_a": families("CONTROLLED_FALLBACK_TIER_A_ALLOWED_FAMILIES"),
        "tier_b": families("CONTROLLED_FALLBACK_TIER_B_ALLOWED_FAMILIES"),
        "tier_c": families("CONTROLLED_FALLBACK_TIER_C_ALLOWED_FAMILIES"),
    }
    for scope, values in scopes.items():
        leaked = sorted(values & forbidden)
        if leaked:
            failures.append(f"{scope}_contains_forbidden_families:{'/'.join(leaked)}")
    if not truthy(env.get("SPREADS_PUBLICATION_ENABLED")):
        warnings.append("SPREADS_PUBLICATION_ENABLED=false")
    if truthy(env.get("TEAM_TOTALS_PUBLICATION_ENABLED")):
        failures.append("TEAM_TOTALS_PUBLICATION_ENABLED=true")
    if as_int(env.get("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES")) < 1:
        failures.append("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES_below_1")
    if as_int(env.get("CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS")) < 2:
        failures.append("CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS_below_2")
    if not truthy(env.get("DAY_INVENTORY_FORCE_PROVIDER_MERGE")) and not truthy(env.get("HARIZON_FAST_INVENTORY_LOCK")):
        failures.append("DAY_INVENTORY_FORCE_PROVIDER_MERGE_false")
    return {
        "status": "failed" if failures else "ok",
        "failures": failures,
        "warnings": warnings,
        "runtime_policy_version": env.get("HARIZON_EFFECTIVE_RUNTIME_POLICY_VERSION"),
        "provider_policy_version": policy_version,
        "checked": {
            "allowed_families": sorted(scopes["allowed"]),
            "tier_a": sorted(scopes["tier_a"]),
            "tier_b": sorted(scopes["tier_b"]),
            "tier_c": sorted(scopes["tier_c"]),
            "min_odds_sources": env.get("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES"),
            "inventory_bootstrap": env.get("DAY_INVENTORY_BOOTSTRAP_PROVIDER"),
            "inventory_provider_merge": env.get("DAY_INVENTORY_FORCE_PROVIDER_MERGE"),
        },
    }


def load_policy() -> dict[str, Any]:
    policy = load_json(POLICY_PATH, {})
    if isinstance(policy, dict) and isinstance(policy.get("providers"), dict):
        return policy
    return {
        "version": "v25-per-run-contract-fallback",
        "mode": "per_run_only",
        "deleted_providers": sorted(REMOVED_PROVIDERS),
        "base_env": {
            "PROVIDER_REQUEST_BUDGET_MODE": "per_run_with_daily_guards",
            "PROVIDER_REQUEST_BUDGET_DISABLE_DAILY_MONTHLY": "false",
            "ALL_SOURCES_FREE_MAXIMIZE": "true",
            "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "true",
        },
        "deleted_provider_env": removed_provider_env(),
        "providers": {},
    }


def compute(policy: dict[str, Any]) -> tuple[dict[str, str], list[dict[str, Any]], list[str]]:
    env: dict[str, str] = {str(k): str(v) for k, v in dict(policy.get("base_env") or {}).items()}
    env["PROVIDER_REQUEST_BUDGET_VERSION"] = str(policy.get("version") or "unknown")
    env["PROVIDER_REQUEST_BUDGET_APPLIED"] = "true"
    env.update(removed_provider_env())
    env.update(_drop_legacy_limited_provider_disables({str(k): str(v) for k, v in dict(policy.get("deleted_provider_env") or {}).items()}))

    decisions: list[dict[str, Any]] = []
    for provider, raw_cfg in dict(policy.get("providers") or {}).items():
        provider_key = str(provider).strip().lower()
        if provider_key in REMOVED_PROVIDERS:
            decisions.append({"provider": provider, "status": "removed", "grant": 0, "configured_grant": 0, "reason": "removed_from_project", "secret_env_keys": [], "api_key_present": None})
            continue
        cfg = raw_cfg if isinstance(raw_cfg, dict) else {}
        configured_grant = max(0, int(float(cfg.get("grant") or 0)))
        secret_keys = [str(item) for item in (cfg.get("secret_env_keys") or []) if str(item).strip()]
        missing_key = bool(secret_keys) and not env_present(secret_keys)
        grant = 0 if missing_key and configured_grant > 0 else configured_grant
        reason = str(cfg.get("reason") or ("missing_key" if missing_key else "granted" if grant > 0 else "disabled_by_policy"))
        status = str(cfg.get("status") or ("missing_key" if missing_key else "working" if grant > 0 else "disabled_by_policy"))
        provider_env = {str(k): str(v) for k, v in dict(cfg.get("env") or {}).items()}
        if provider_key in {"api_football", "oddspapi"}:
            provider_env = _drop_legacy_limited_provider_disables(provider_env)
        if missing_key and configured_grant > 0:
            provider_env.update(disabled_env(str(provider)))
        env.update(provider_env)
        p = prefix(str(provider))
        env[f"{p}_REQUEST_BUDGET_GRANTED"] = str(grant)
        env[f"{p}_REQUEST_BUDGET_REASON"] = reason
        env.setdefault(f"{p}_MAX_HTTP_REQUESTS_PER_RUN", str(grant))
        decisions.append({
            "provider": provider,
            "status": status,
            "grant": grant,
            "configured_grant": configured_grant,
            "reason": reason,
            "secret_env_keys": secret_keys,
            "api_key_present": None if not secret_keys else not missing_key,
        })

    integrity = final_market_integrity_env()
    env.update(integrity)
    notes = [
        "config/provider_runtime_policy.json is the base provider budget source.",
        "Final per-run grants are applied after this step by scripts/apply_per_run_api_quota_contract.py.",
        "Only bookies_api is forcibly removed. API-Football and OddsPapi are controlled by per-run free-limit grants.",
        "Context sources do not confirm price; publication requires market depth.",
    ]
    env["ODDS_API_IO_ACCOUNT2_ACTIVE"] = "true" if env_present(["ODDS_API_IO_KEY_2", "ODDS_API_IO_KEY2"]) else "false"
    if env["ODDS_API_IO_ACCOUNT2_ACTIVE"] == "false":
        notes.append("ODDS_API_IO_KEY_2 is missing; account2 bookmakers cannot be queried.")
    return env, decisions, notes


def main() -> int:
    now = datetime.now(UTC)
    policy = load_policy()
    env, decisions, notes = compute(policy)
    append_github_env(env)
    state = load_json(STATE_PATH, {})
    if not isinstance(state, dict):
        state = {}
    state.update({"version": policy.get("version"), "policy_path": str(POLICY_PATH), "updated_at": now.isoformat(), "last_decisions": decisions})
    write_json(STATE_PATH, state)
    check = market_integrity_check(env, policy.get("version"))
    export = {
        "version": policy.get("version"),
        "policy_path": str(POLICY_PATH),
        "event": os.getenv("GITHUB_EVENT_NAME") or "",
        "utc_now": now.isoformat(),
        "msk_now": now.astimezone(MSK).isoformat(),
        "slot_msk": now.astimezone(MSK).strftime("%H:%M MSK"),
        "mode": policy.get("mode") or "per_run_only",
        "deleted_providers": sorted(REMOVED_PROVIDERS),
        "decisions": decisions,
        "env_written_count": len(env),
        "integrity_env": final_market_integrity_env(),
        "market_integrity_check": check,
        "notes": notes,
    }
    effective_runtime = {
        "policy_version": env["HARIZON_EFFECTIVE_RUNTIME_POLICY_VERSION"],
        "provider_policy_version": policy.get("version"),
        "created_at_utc": now.isoformat(),
        "local_now": now.astimezone(MSK).isoformat(),
        "env_updates": env,
        "provider_decisions": decisions,
        "market_integrity_check": check,
        "notes": notes,
        "source": "scripts/apply_provider_request_budget.py base layer; final per-run contract follows",
    }
    write_json(EXPORT_PATH, export)
    write_json(EFFECTIVE_RUNTIME_PATH, effective_runtime)
    write_json(MARKET_INTEGRITY_CHECK_PATH, check)
    print(json.dumps(export, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if check.get("status") == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
