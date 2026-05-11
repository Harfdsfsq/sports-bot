from __future__ import annotations

"""v10 quota-safe full-data smoke.

SportLogic free quota is 500/day and the latest smoke showed the key already at
648 used. By default v10 skips SportLogic inside the broad all-provider probe so
we can continue repairing Bzzoiro/SStats/football-data/coverage without burning
more SportLogic requests. Set API_FULL_SMOKE_SPORTLOGIC_ENABLED=true to include
one lightweight SportLogic discovery request.
"""

import asyncio
import os
from typing import Any

from scripts import api_full_data_smoke_probe_v9 as v9
from scripts import api_full_data_smoke_probe_v7 as base

_ORIG_BUILD_CALLS = base.build_calls
_ORIG_DETAIL_CALLS = v9.detail_calls


def _truthy(name: str, default: bool = False) -> bool:
    raw = str(os.getenv(name) or "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _sportlogic_lite_calls(calls: list[base.CallSpec]) -> list[base.CallSpec]:
    sportlogic = [call for call in calls if call.provider == "sportlogic"]
    if not sportlogic:
        return []
    # Prefer active odds because it proves odds discovery path and usually has
    # game_id for follow-up parsing. Fall back to first available SportLogic call.
    for call in sportlogic:
        if call.command == "active_odds":
            return [call]
    return sportlogic[:1]


def build_calls() -> list[base.CallSpec]:
    calls = list(_ORIG_BUILD_CALLS())
    enabled = _truthy("API_FULL_SMOKE_SPORTLOGIC_ENABLED", False)
    if not enabled:
        return [call for call in calls if call.provider != "sportlogic"]
    return [call for call in calls if call.provider != "sportlogic"] + _sportlogic_lite_calls(calls)


async def detail_calls(client, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not _truthy("API_FULL_SMOKE_SPORTLOGIC_DETAILS_ENABLED", False):
        # Keep Bzzoiro and odds-api details from v9/base, but avoid SportLogic
        # game detail fan-out unless explicitly requested.
        filtered_results = [row for row in results if row.get("provider") != "sportlogic"]
        return await _ORIG_DETAIL_CALLS(client, filtered_results)
    return await _ORIG_DETAIL_CALLS(client, results)


def _render(payload: dict[str, Any]) -> str:
    return base.render(payload).replace("diagnostics v9", "diagnostics v10").replace("diagnostics v7", "diagnostics v10")


def install() -> None:
    v9.install()
    base.build_calls = build_calls
    base.detail_calls = detail_calls
    base.render = _render


async def run() -> dict[str, Any]:
    install()
    payload = await base.run()
    payload["mode"] = "api_full_data_smoke_probe_v10"
    payload.setdefault("notes", []).append(
        "v10: SportLogic is skipped by default in broad smoke. Enable API_FULL_SMOKE_SPORTLOGIC_ENABLED=true for one lightweight SportLogic probe."
    )
    base.JSON_OUT.write_text(base.json.dumps(base.safe(payload), ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    base.TXT_OUT.write_text(_render(payload), encoding="utf-8")
    print(_render(payload))
    return payload


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
