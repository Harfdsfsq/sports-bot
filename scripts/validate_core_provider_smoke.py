from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

SMOKE_PATH = Path(os.getenv("PROVIDER_SMOKE_REPORT_PATH") or ".data/exports/latest-provider-smoke.json")
OUT_PATH = Path(os.getenv("CORE_PROVIDER_SMOKE_REPORT_PATH") or ".data/exports/latest-core-provider-smoke-validation.json")


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _core_providers() -> list[str]:
    raw = os.getenv("PROVIDER_SMOKE_REQUIRED_CORE_PROVIDERS") or "odds_api_io,sstats,bzzoiro"
    return [x.strip() for x in raw.split(",") if x.strip()]


def _load(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except Exception as exc:
        return {"_load_error": str(exc)}


def main() -> int:
    report = _load(SMOKE_PATH)
    checks = report.get("checks") if isinstance(report, dict) else []
    checks = checks if isinstance(checks, list) else []
    by_provider = {str(c.get("provider") or "").strip(): c for c in checks if isinstance(c, dict)}
    required = _core_providers()
    failures: list[dict[str, Any]] = []
    for provider in required:
        check = by_provider.get(provider)
        if not check:
            failures.append({"provider": provider, "reason": "missing_smoke_check"})
            continue
        if not bool(check.get("ok")):
            failures.append({
                "provider": provider,
                "reason": "core_provider_not_ok",
                "status": check.get("status"),
                "http_status": check.get("http_status"),
                "error": check.get("error") or check.get("body_preview"),
            })
    payload = {
        "status": "ok" if not failures else "failed",
        "smoke_report": str(SMOKE_PATH),
        "required_core_providers": required,
        "failures": failures,
        "optional_provider_failures_ignored": [
            {
                "provider": str(c.get("provider") or ""),
                "status": c.get("status"),
                "error": c.get("error") or c.get("body_preview"),
            }
            for c in checks
            if str(c.get("provider") or "") not in required and not bool(c.get("ok"))
        ],
    }
    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    if failures and _truthy(os.getenv("PROVIDER_SMOKE_FAIL_ON_CORE_ERROR") or "true"):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
