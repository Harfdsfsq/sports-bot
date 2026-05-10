from __future__ import annotations

"""Quota-safe full-data smoke wrapper.

provider_smoke_fast currently imports v4, and v4 routes here. Therefore this
file must not import v4. It wraps v3 with a hard request cap for odds-api.io extra
endpoints so the smoke test cannot burn the hourly quota before matching
inventory diagnostics run.
"""

import asyncio
import os

from scripts import api_full_data_smoke_probe as base
from scripts import api_full_data_smoke_probe_v3 as probe_base


async def run() -> dict:
    os.environ.setdefault("API_FULL_SMOKE_ODDS_EVENT_LIMIT", "1")
    os.environ.setdefault("API_FULL_SMOKE_ODDS_MARKETS", "1x2,h2h,totals")
    os.environ.setdefault("API_FULL_SMOKE_ODDS_EXTRA_MAX_REQUESTS", "12")

    original_get = base._get
    counter = {"odds_extra": 0}
    cap = max(4, int(float(os.getenv("API_FULL_SMOKE_ODDS_EXTRA_MAX_REQUESTS") or 12)))

    async def capped_get(client, api, url, *, endpoint, params=None, headers=None, section):
        if api == "odds_api_io" and endpoint not in {"/events"}:
            counter["odds_extra"] += 1
            if counter["odds_extra"] > cap:
                section["extra_request_cap_hit"] = True
                section.setdefault("error_examples", []).append(f"{endpoint}: skipped by quota-safe probe cap")
                return None
        return await original_get(client, api, url, endpoint=endpoint, params=params, headers=headers, section=section)

    base._get = capped_get
    try:
        payload = await probe_base.run()
    finally:
        base._get = original_get
    payload["quota_safe_wrapper"] = {"odds_extra_requests_attempted": counter["odds_extra"], "cap": cap}
    return payload


def main() -> int:
    payload = asyncio.run(run())
    print(base._render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
