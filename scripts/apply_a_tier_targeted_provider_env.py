from __future__ import annotations

"""Export targeted A-tier provider hints to GITHUB_ENV.

The existing provider scripts read env limits/policy. This helper makes the
A-tier targeting explicit in workflow/runtime artifacts without relaxing any
publication guard.
"""

import json
import os
from pathlib import Path
from typing import Any

EXPORT = Path(".data/exports")
QUEUE = EXPORT / "latest-a-tier-targeted-enrichment-queue.json"
OUT = EXPORT / "latest-a-tier-targeted-provider-env.json"


def _load(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _int(v: Any) -> int:
    try:
        return int(float(v))
    except Exception:
        return 0


def _write_github_env(values: dict[str, str]) -> None:
    env_path = os.getenv("GITHUB_ENV")
    if not env_path:
        return
    with open(env_path, "a", encoding="utf-8") as fh:
        for k, v in values.items():
            fh.write(f"{k}={v}\n")


def main() -> int:
    queue = _load(QUEUE)
    summary = queue.get("summary") if isinstance(queue.get("summary"), dict) else {}
    bzz_targets = _int(summary.get("bzzoiro_odds_target_count"))
    ctx_targets = _int(summary.get("context_projection_target_count"))
    recheck_targets = _int(summary.get("high_value_recheck_target_count"))
    values = {
        "A_TIER_TARGETED_ENRICHMENT_ENABLED": "true",
        "A_TIER_BZZOIRO_TARGET_COUNT": str(bzz_targets),
        "A_TIER_CONTEXT_TARGET_COUNT": str(ctx_targets),
        "A_TIER_HIGH_VALUE_RECHECK_COUNT": str(recheck_targets),
        "BZZOIRO_TARGETED_ODDS_CONFIRMATION_ENABLED": "true",
        "SSTATS_TARGETED_CONTEXT_PROJECTION_ENABLED": "true",
        "HIGH_VALUE_FAST_RECHECK_ENABLED": "true",
    }
    _write_github_env(values)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"status":"ok","values":values,"publication_contract_relaxed":False}, ensure_ascii=False, indent=2, sort_keys=True)+"\n", encoding="utf-8")
    print(json.dumps(values, ensure_ascii=False))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
