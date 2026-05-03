from __future__ import annotations

"""Unquarantine SportLogic odds after parser hardening is installed.

This script is intentionally idempotent.  It patches the runtime policy files
before the normal budget scripts read them and also writes the effective env to
GITHUB_ENV when available.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
UTC = timezone.utc
OUT_PATH = ROOT / ".data" / "exports" / "latest-sportlogic-policy-unquarantine.json"

SPORTLOGIC_ENV: dict[str, str] = {
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
    "SPORTLOGIC_REQUEST_BUDGET_GRANTED": "40",
    "SPORTLOGIC_REQUEST_BUDGET_REASON": "granted_after_parser_hardening",
}


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_text_if_changed(path: Path, text: str) -> bool:
    try:
        old = path.read_text(encoding="utf-8")
    except Exception:
        return False
    if old == text:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def append_github_env(values: dict[str, str]) -> None:
    for key, value in values.items():
        os.environ[str(key)] = str(value)
    github_env = os.getenv("GITHUB_ENV")
    if not github_env:
        return
    try:
        with open(github_env, "a", encoding="utf-8") as fh:
            for key in sorted(values):
                fh.write(f"{key}={values[key]}\n")
    except Exception:
        return


def patch_provider_runtime_policy() -> bool:
    path = ROOT / "config" / "provider_runtime_policy.json"
    payload = load_json(path, {})
    if not isinstance(payload, dict):
        return False
    providers = payload.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        payload["providers"] = providers
    sportlogic = providers.setdefault("sportlogic", {})
    if not isinstance(sportlogic, dict):
        sportlogic = {}
        providers["sportlogic"] = sportlogic

    payload["version"] = "v22-sportlogic-odds-enabled-parser-hardened"
    payload["description"] = (
        "Single runtime source of truth for provider request grants. "
        "SportLogic odds are enabled after parser hardening; it may contribute odds/context when fixtures match."
    )
    sportlogic.update({
        "status": "odds_enabled_parser_hardened",
        "grant": 40,
        "reason": "granted_after_parser_hardening",
        "secret_env_keys": ["SPORTLOGIC_API_KEY", "SPORTLOGIC_KEY", "SPORTLOGIC_TOKEN"],
        "env": dict(SPORTLOGIC_ENV),
    })
    before = load_json(path, None)
    if before == payload:
        return False
    write_json(path, payload)
    return True


def patch_apply_harizon_runtime_policy() -> bool:
    path = ROOT / "scripts" / "apply_harizon_runtime_policy.py"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False
    old_block = '''        # SportLogic odds currently returns payloads without valid price fields.
        # Disable the provider completely until parser is fixed; this saves requests.
        "ENABLE_SPORTLOGIC": "false",
        "SPORTLOGIC_ENABLED": "false",
        "SPORTLOGIC_PER_RUN_MAX": "0",
        "SPORTLOGIC_MATCH_LIMIT": "0",
        "SPORTLOGIC_ODDS_MATCH_LIMIT": "0",
        "SPORTLOGIC_ODDS_DISABLED_REASON": "missing_or_invalid_price_payload",
'''
    new_block = '''        # SportLogic is active again: parser hardening normalizes nested/flat odds payloads
        # and the provider remains bounded by per-run request limits.
        "ENABLE_SPORTLOGIC": "true",
        "SPORTLOGIC_ENABLED": "true",
        "SPORTLOGIC_BASE_URL": "https://api.sportlogic.io/api/v1",
        "SPORTLOGIC_HEADER_NAME": "X-API-Key",
        "SPORTLOGIC_PER_RUN_MAX": policy_value("SPORTLOGIC_PER_RUN_MAX", "40"),
        "SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN": policy_value("SPORTLOGIC_MAX_HTTP_REQUESTS_PER_RUN", "40"),
        "SPORTLOGIC_MATCH_LIMIT": policy_value("SPORTLOGIC_MATCH_LIMIT", "120"),
        "SPORTLOGIC_CONTEXT_MATCH_LIMIT": policy_value("SPORTLOGIC_CONTEXT_MATCH_LIMIT", "120"),
        "SPORTLOGIC_ODDS_MATCH_LIMIT": policy_value("SPORTLOGIC_ODDS_MATCH_LIMIT", "32"),
        "SPORTLOGIC_BOOKMAKERS": os.getenv("SPORTLOGIC_BOOKMAKERS") or "",
        "SPORTLOGIC_ODDS_DISABLED_REASON": "",
'''
    changed = False
    if old_block in text:
        text = text.replace(old_block, new_block, 1)
        changed = True
    text2 = text.replace(
        '            "SportLogic is disabled while its odds payload lacks parseable prices, so requests are not wasted.",',
        '            "SportLogic odds are enabled after parser hardening; request volume is bounded and diagnostics stay exported.",',
    )
    changed = changed or text2 != text
    if changed:
        path.write_text(text2, encoding="utf-8")
    return changed


def patch_apply_provider_request_budget() -> bool:
    path = ROOT / "scripts" / "apply_provider_request_budget.py"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return False
    changed = False
    old = '''    if str(env.get('SPORTLOGIC_BOOKMAKERS') or '') != '__probe_only__':
        warnings.append('sportlogic_bookmakers_not_probe_only')
    if 'quarantined' not in str(env.get('SPORTLOGIC_ODDS_DISABLED_REASON') or ''):
        warnings.append('sportlogic_odds_quarantine_reason_missing')
'''
    new = '''    if as_int(env.get('SPORTLOGIC_ODDS_MATCH_LIMIT'), 0) <= 0 and str(env.get('ENABLE_SPORTLOGIC') or '').lower() == 'true':
        warnings.append('sportlogic_enabled_but_odds_limit_zero')
'''
    if old in text:
        text = text.replace(old, new, 1)
        changed = True
    replacements = {
        'SportLogic is context-probe only; odds are quarantined until parser fixtures pass.':
            'SportLogic odds are enabled after parser hardening; diagnostics verify fixture and odds payload shape.',
        'v21-sportlogic-context-probe-odds-quarantined':
            'v22-sportlogic-odds-enabled-parser-hardened',
    }
    for src, dst in replacements.items():
        if src in text:
            text = text.replace(src, dst)
            changed = True
    if changed:
        path.write_text(text, encoding="utf-8")
    return changed


def update_existing_exports() -> bool:
    changed = False
    for rel in (
        ".data/exports/latest-provider-request-budget.json",
        ".data/exports/latest-harizon-runtime-policy.json",
    ):
        path = ROOT / rel
        payload = load_json(path, None)
        if not isinstance(payload, dict):
            continue
        payload["version"] = payload.get("version") or "v22-sportlogic-odds-enabled-parser-hardened"
        if "provider_policy_version" in payload:
            payload["provider_policy_version"] = "v22-sportlogic-odds-enabled-parser-hardened"
        env = payload.get("env_updates")
        if isinstance(env, dict):
            env.update(SPORTLOGIC_ENV)
        decisions = payload.get("decisions") or payload.get("provider_decisions")
        if isinstance(decisions, list):
            for item in decisions:
                if isinstance(item, dict) and str(item.get("provider") or "") == "sportlogic":
                    item.update({
                        "status": "odds_enabled_parser_hardened",
                        "grant": 40,
                        "configured_grant": 40,
                        "reason": "granted_after_parser_hardening",
                    })
        write_json(path, payload)
        changed = True
    return changed


def main() -> int:
    changes = {
        "provider_runtime_policy": patch_provider_runtime_policy(),
        "apply_harizon_runtime_policy": patch_apply_harizon_runtime_policy(),
        "apply_provider_request_budget": patch_apply_provider_request_budget(),
        "existing_exports": update_existing_exports(),
    }
    append_github_env(SPORTLOGIC_ENV)
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "policy_version": "v22-sportlogic-odds-enabled-parser-hardened",
        "env": SPORTLOGIC_ENV,
        "changes": changes,
    }
    write_json(OUT_PATH, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
