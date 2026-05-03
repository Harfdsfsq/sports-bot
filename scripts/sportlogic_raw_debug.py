from __future__ import annotations

"""Raw SportLogic diagnostics.

This script intentionally bypasses the provider parser. It answers one question:
what does the SportLogic API return for documented /games filters right now?

Outputs:
  .data/exports/latest-sportlogic-debug.json
  .data/exports/latest-sportlogic-debug.txt
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

UTC = timezone.utc
OUT_JSON = Path(".data/exports/latest-sportlogic-debug.json")
OUT_TXT = Path(".data/exports/latest-sportlogic-debug.txt")
SECRET_KEYS = ("key", "token", "secret", "authorization", "api_key", "apikey")


def truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 5:
        return "..."
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in list(value.items())[:60]:
            low = str(key).lower()
            out[str(key)] = "***" if any(token in low for token in SECRET_KEYS) else sanitize(item, depth + 1)
        return out
    if isinstance(value, list):
        return [sanitize(item, depth + 1) for item in value[:8]]
    if isinstance(value, str):
        return value[:900]
    return value


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
    except Exception:
        return None


def extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("data", "response", "results", "items", "games", "fixtures", "matches", "events"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = extract_rows(value)
            if nested:
                return nested
    return []


def row_start(row: dict[str, Any]) -> datetime | None:
    for key in ("start_time", "commence_time", "date", "kickoff", "scheduled_at", "time"):
        dt = parse_dt(row.get(key))
        if dt is not None:
            return dt.astimezone(UTC)
    return None


def row_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    starts = sorted([dt for row in rows if (dt := row_start(row)) is not None])
    return {
        "row_count": len(rows),
        "min_start": starts[0].isoformat() if starts else "",
        "max_start": starts[-1].isoformat() if starts else "",
        "sample_start_times": [dt.isoformat() for dt in starts[:5]],
        "sample_rows": sanitize(rows[:3]),
    }


def headers(api_key: str) -> dict[str, str]:
    header_name = os.getenv("SPORTLOGIC_HEADER_NAME", "X-API-Key").strip() or "X-API-Key"
    out = {"Accept": "application/json"}
    if header_name.lower() == "authorization":
        scheme = os.getenv("SPORTLOGIC_AUTH_SCHEME", "Bearer").strip()
        out["Authorization"] = f"{scheme} {api_key}".strip()
    else:
        out[header_name] = api_key
    return out


def build_queries(now: datetime) -> list[tuple[str, str, dict[str, Any], bool]]:
    utc_today = now.date()
    local_today = now.astimezone().date()
    dates = []
    for day in [utc_today - timedelta(days=1), utc_today, utc_today + timedelta(days=1), local_today, local_today + timedelta(days=1)]:
        key = day.isoformat()
        if key not in dates:
            dates.append(key)
    queries: list[tuple[str, str, dict[str, Any], bool]] = [
        ("health", "/health", {}, False),
        ("games_unfiltered", "/games", {"per_page": 5}, True),
    ]
    for key in dates:
        next_key = (datetime.fromisoformat(key).date() + timedelta(days=1)).isoformat()
        queries.extend([
            (f"games_date_from_scheduled_{key}", "/games", {"date_from": key, "status": "scheduled", "per_page": 5}, True),
            (f"games_day_scheduled_inclusive_{key}", "/games", {"date_from": key, "date_to": key, "status": "scheduled", "per_page": 5}, True),
            (f"games_day_scheduled_exclusive_{key}", "/games", {"date_from": key, "date_to": next_key, "status": "scheduled", "per_page": 5}, True),
            (f"games_day_no_status_{key}", "/games", {"date_from": key, "date_to": next_key, "per_page": 5}, True),
            (f"games_date_from_no_status_{key}", "/games", {"date_from": key, "per_page": 5}, True),
        ])
    # Deliberately undocumented control: if this returns rows while documented
    # filters return zero, it proves unknown params are ignored or behave outside
    # the documented contract.
    queries.append((f"control_undocumented_date_{utc_today.isoformat()}", "/games", {"date": utc_today.isoformat(), "per_page": 5}, True))
    return queries


async def main() -> int:
    api_key = (os.getenv("SPORTLOGIC_API_KEY") or os.getenv("SPORTLOGIC_KEY") or os.getenv("SPORTLOGIC_TOKEN") or "").strip()
    base_url = str(os.getenv("SPORTLOGIC_BASE_URL") or "https://api.sportlogic.io/api/v1").rstrip("/")
    timeout = float(os.getenv("SPORTLOGIC_TIMEOUT_SECONDS") or 20)
    now = datetime.now(UTC)
    payload: dict[str, Any] = {
        "created_at_utc": now.isoformat(),
        "base_url": base_url,
        "api_key_present": bool(api_key),
        "header_name": os.getenv("SPORTLOGIC_HEADER_NAME", "X-API-Key"),
        "queries": [],
    }
    if not api_key:
        payload["fatal"] = "missing_sportlogic_api_key"
    else:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            for label, path, params, use_auth in build_queries(now):
                item: dict[str, Any] = {"label": label, "path": path, "params": params}
                try:
                    response = await client.get(f"{base_url}{path}", headers=headers(api_key) if use_auth else {"Accept": "application/json"}, params=params or None)
                    text = response.text or ""
                    item.update({
                        "status_code": response.status_code,
                        "url_path": str(response.url).replace(api_key, "***"),
                        "rate_limit_limit": response.headers.get("X-RateLimit-Limit", ""),
                        "rate_limit_remaining": response.headers.get("X-RateLimit-Remaining", ""),
                        "content_type": response.headers.get("content-type", ""),
                        "body_preview": text[:900],
                    })
                    try:
                        body = response.json()
                    except Exception:
                        body = None
                    if isinstance(body, dict):
                        item["top_level_keys"] = list(body.keys())[:20]
                        item["success"] = body.get("success")
                        if isinstance(body.get("error"), dict):
                            item["error"] = sanitize(body.get("error"))
                        if isinstance(body.get("pagination"), dict):
                            item["pagination"] = sanitize(body.get("pagination"))
                        if isinstance(body.get("meta"), dict):
                            item["meta"] = sanitize(body.get("meta"))
                    rows = extract_rows(body)
                    item.update(row_summary(rows))
                except Exception as exc:
                    item.update({"exception": f"{type(exc).__name__}: {exc}"})
                payload["queries"].append(item)

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    lines = [
        "🧪 SportLogic raw debug",
        f"• time UTC: {payload.get('created_at_utc')}",
        f"• base_url: {payload.get('base_url')}",
        f"• api_key_present: {payload.get('api_key_present')}",
    ]
    for item in payload.get("queries", []):
        lines.append(
            f"• {item.get('label')}: http={item.get('status_code')} rows={item.get('row_count')} "
            f"span={item.get('min_start','')}..{item.get('max_start','')} success={item.get('success')}"
        )
        if item.get("error"):
            lines.append(f"  error: {json.dumps(item.get('error'), ensure_ascii=False)[:500]}")
        if item.get("body_preview") and item.get("row_count", 0) == 0:
            lines.append(f"  body: {str(item.get('body_preview'))[:350]}")
    OUT_TXT.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    print(OUT_TXT.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(__import__("asyncio").run(main()))
