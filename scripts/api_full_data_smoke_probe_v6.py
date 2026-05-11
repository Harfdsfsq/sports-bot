from __future__ import annotations

"""Small diagnostic override for quota-safe full-data probe.

v5 is already quota-safe. v6 only improves signal quality:
- /odds/updated uses /sports-derived ids/slugs instead of plain `football`;
- /odds/movements 404/no-data is recorded as diagnostic noise, not an error.
"""

import asyncio
import json
from datetime import timedelta
from typing import Any

from scripts import api_full_data_smoke_probe as base
from scripts import api_full_data_smoke_probe_v5 as v5


def _sport_values(rows: list[dict[str, Any]]) -> list[str]:
    values: list[str] = []
    for row in rows:
        blob = json.dumps(row, ensure_ascii=False).lower()
        if "football" not in blob and "soccer" not in blob:
            continue
        for key in ("key", "slug", "id", "sport_key", "sportId"):
            item = row.get(key)
            if item in (None, "") or isinstance(item, dict):
                continue
            text = str(item).strip()
            if text.lower() in {"football", "soccer"}:
                continue
            if text and text not in values:
                values.append(text)
    return values[:4]


async def _limited_get_v6(client, section, root, endpoint, params):
    if endpoint != "/events":
        section["extra_requests"] = int(section.get("extra_requests") or 0) + 1
        if int(section.get("extra_requests") or 0) > v5._extra_cap():
            section["extra_request_cap_hit"] = True
            section.setdefault("diagnostic_examples", []).append(f"{endpoint}: skipped by quota-safe cap")
            return None
    before_errors = int(section.get("errors") or 0)
    before_examples = list(section.get("error_examples") or [])
    payload = await base._get(client, "odds_api_io", f"{root}{endpoint}", endpoint=endpoint, params=params, section=section)
    if payload is None and endpoint == "/odds/movements":
        section["errors"] = before_errors
        section["error_examples"] = before_examples
        section.setdefault("diagnostic_examples", []).append("/odds/movements: no sampled movement for params")
    return payload


async def _probe_updated_v6(client, section, root, secret, book, event_rows, now):
    sports_payload = await _limited_get_v6(client, section, root, "/sports", {v5.KEY_PARAM: secret})
    sports_rows = base._rows(sports_payload)
    candidates = _sport_values(sports_rows)
    section["sports_rows_count"] = len(sports_rows)
    section["updated_sport_candidates"] = candidates
    section["sports_sample"] = sports_rows[:5]

    since_unix = str(int((now - timedelta(seconds=30)).timestamp()))
    rows: list[dict[str, Any]] = []
    used: dict[str, Any] = {}
    for sport in candidates:
        before_errors = int(section.get("errors") or 0)
        before_examples = list(section.get("error_examples") or [])
        params = {v5.KEY_PARAM: secret, "since": since_unix, "sport": sport, v5.BOOK_PARAM: book}
        payload = await _limited_get_v6(client, section, root, "/odds/updated", params)
        if payload is not None:
            rows = base._rows(payload)
            used = {"since": since_unix, "sport": sport, v5.BOOK_PARAM: book}
            break
        section["errors"] = before_errors
        section["error_examples"] = before_examples
        section.setdefault("diagnostic_examples", []).append(f"/odds/updated: rejected sampled sport={sport}")
        await asyncio.sleep(0.1)

    if not candidates:
        section.setdefault("diagnostic_examples", []).append("/odds/updated: skipped because /sports exposed no valid football/soccer id")
    section["updated_rows_count"] = len(rows)
    section["updated_sample"] = rows[:5]
    section["updated_params_used"] = base._sanitize(used)


async def run() -> dict[str, Any]:
    original_limited_get = v5._limited_get
    original_probe_updated = v5._probe_updated
    v5._limited_get = _limited_get_v6
    v5._probe_updated = _probe_updated_v6
    try:
        return await v5.run()
    finally:
        v5._limited_get = original_limited_get
        v5._probe_updated = original_probe_updated


def main() -> int:
    payload = asyncio.run(run())
    print(base._render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
