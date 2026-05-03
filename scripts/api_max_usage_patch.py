from __future__ import annotations

"""Legacy compatibility shim plus mandatory SportLogic runtime unquarantine.

Provider budgets are primarily applied by scripts/apply_provider_request_budget.py
from config/provider_runtime_policy.json. The workflow still calls this shim
immediately before provider budget application, so it is the safest explicit
place to patch SportLogic out of the old probe-only quarantine before budgets
are computed.
"""

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
POLICY_PATH = ROOT / "config" / "provider_runtime_policy.json"
OUT_PATH = ROOT / ".data" / "exports" / "latest-api-max-usage-patch.json"

SPORTLOGIC_ENV = {
    "ENABLE_SPORTLOGIC": "true",
    "SPORTLOGIC_ENABLED": "true",
    "SPORTLOGIC_BASE_URL": "https://api.sportlogic.io/api/v1",
    "SPORTLOGIC_HEADER_NAME": "X-API-Key",
    "SPORTLOGIC_PER_RUN_MAX": "40",
    "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": "40",
    "SPORTLOGIC_MATCH_LIMIT": "120",
    "SPORTLOGIC_CONTEXT_MATCH_LIMIT": "120",
    "SPORTLOGIC_ODDS_MATCH_LIMIT": "32",
    "SPORTLOGIC_BOOKMAKERS": "",
    "SPORTLOGIC_ODDS_DISABLED_REASON": "",
}


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _append_github_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ[str(key)] = str(value)
    github_env = os.getenv("GITHUB_ENV")
    if not github_env:
        for key in sorted(values):
            print(f"{key}={values[key]}")
        return
    with open(github_env, "a", encoding="utf-8") as fh:
        for key in sorted(values):
            fh.write(f"{key}={values[key]}\n")


def _patch_sportlogic_policy() -> dict[str, Any]:
    policy = _load_json(POLICY_PATH, {})
    if not isinstance(policy, dict):
        policy = {}
    providers = policy.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        policy["providers"] = providers
    sportlogic = providers.setdefault("sportlogic", {})
    if not isinstance(sportlogic, dict):
        sportlogic = {}
        providers["sportlogic"] = sportlogic

    before = json.dumps(sportlogic, sort_keys=True, ensure_ascii=False)
    policy["version"] = "v22-sportlogic-odds-enabled-parser-hardened"
    policy["description"] = (
        "Single runtime source of truth for provider request grants. "
        "SportLogic odds are enabled after parser hardening and bounded by per-run request budgets."
    )
    sportlogic.clear()
    sportlogic.update({
        "status": "odds_enabled_parser_hardened",
        "grant": 40,
        "reason": "granted_after_parser_hardening",
        "secret_env_keys": ["SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN"],
        "env": dict(SPORTLOGIC_ENV),
    })
    after = json.dumps(sportlogic, sort_keys=True, ensure_ascii=False)
    changed = before != after or str(policy.get("version")) != "v22-sportlogic-odds-enabled-parser-hardened"
    _write_json(POLICY_PATH, policy)
    return {
        "changed": changed,
        "policy_version": policy.get("version"),
        "sportlogic_status": sportlogic.get("status"),
        "sportlogic_grant": sportlogic.get("grant"),
        "sportlogic_reason": sportlogic.get("reason"),
        "sportlogic_odds_match_limit": (sportlogic.get("env") or {}).get("SPORTLOGIC_ODDS_MATCH_LIMIT"),
    }


def apply_api_max_usage_patch() -> None:
    result = _patch_sportlogic_policy()
    env = dict(SPORTLOGIC_ENV)
    env.update({
        "SPORTLOGIC_REQUEST_BUDGET_GRANTED": "40",
        "SPORTLOGIC_REQUEST_BUDGET_REASON": "granted_after_parser_hardening",
        "PROVIDER_REQUEST_BUDGET_VERSION": "v22-sportlogic-odds-enabled-parser-hardened",
    })
    _append_github_env(env)
    _write_json(OUT_PATH, {"sportlogic_unquarantine": result, "env_written": env})


if __name__ == "__main__":
    apply_api_max_usage_patch()
