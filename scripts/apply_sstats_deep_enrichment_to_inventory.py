from __future__ import annotations

"""Apply SStats deep enrichment to the current day inventory.

SStats crosswalk already proves which inventory rows map to concrete SStats
`gameId` values. This script makes the next runtime step real: it calls selected
SStats deep endpoints and mutates `.data/day_inventory/*.json` so the coverage
matrix/model can see SStats as actual per-match context/xG/form and optional odds
rescue, not only as a projection.

Quota model:
- default max HTTP requests per run: SSTATS_DEEP_DETAIL_LIMIT_PER_RUN, default 100;
- each selected match first gets `/Games/glicko/{id}` and `/Games/last-games-stats`;
- `/Games/{id}` is called only for the highest-priority subset for venue/lineups;
- `/Odds/{gameId}` is called only when existing odds source count is < 2.
"""

import asyncio
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from scripts import sstats_crosswalk_probe

UTC = timezone.utc
BASE_URL = "https://api.sstats.net"
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "latest-sstats-deep-inventory-enrichment.json"
TXT_OUT = OUT_DIR / "latest-sstats-deep-inventory-enrichment.txt"
UA = "HARIZON-sstats-deep-inventory-enrichment/1.0"

ODDS_COUNT_KEYS = (
    "price_confirmation_sources_count",
    "latest_books_max",
    "books_count",
    "bookmaker_count",
    "bookmakers_count",
    "odds_sources_count",
    "latest_odds_sources_max",
    "price_sources_count",
    "independent_odds_sources_count",
    "exact_price_sources_count",
    "exact_sources_count",
)
CONTEXT_COUNT_KEYS = (
    "context_sources_count",
    "latest_context_sources_max",
    "confirmation_sources_count",
    "latest_confirmation_sources_max",
    "context_source_count",
    "xg_sources_count",
    "form_sources_count",
)


def env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value))) if value not in (None, "") else default
    except Exception:
        return default


def truthy(name: str, default: bool = True) -> bool:
    raw = env(name).lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def load_json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if value is not None else default
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def inventory_path_from_crosswalk(crosswalk: dict[str, Any]) -> Path:
    raw = str(crosswalk.get("inventory_path") or "").strip()
    if raw:
        p = Path(raw)
        if p.exists():
            return p
    return sstats_crosswalk_probe.inventory_path()


def rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "items", "result", "results", "matches", "games", "odds"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = rows(value)
            if nested:
                return nested
    return [payload] if payload else []


def safe_sample(value: Any, limit: int = 3) -> Any:
    if isinstance(value, list):
        return [safe_sample(item, limit) for item in value[:limit]]
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for idx, (key, item) in enumerate(value.items()):
            if idx >= 40:
                out["..."] = "truncated"
                break
            if str(key).lower() in {"apikey", "api_key", "key", "token", "authorization"}:
                out[str(key)] = "***"
            else:
                out[str(key)] = safe_sample(item, limit)
        return out
    if isinstance(value, str):
        api_key = env("SSTATS_API_KEY")
        text = value.replace(api_key, "***") if api_key else value
        return text[:800]
    return value


def get_count(row: dict[str, Any], keys: tuple[str, ...]) -> int:
    best = 0
    containers = [row]
    for key in ("coverage", "source_summary", "market_summary", "price_summary", "metadata", "model_inputs", "enrichment"):
        value = row.get(key)
        if isinstance(value, dict):
            containers.append(value)
    for container in containers:
        for key in keys:
            best = max(best, as_int(container.get(key), 0))
    return best


def ensure_list_source(value: Any) -> list[str]:
    if isinstance(value, list):
        parts = value
    else:
        parts = str(value or "").split(",")
    out: list[str] = []
    for item in parts:
        text = str(item or "").strip()
        if text and text.lower() not in {"none", "null", "unknown"} and text not in out:
            out.append(text)
    return out


def add_source(row: dict[str, Any], family: str, source: str) -> None:
    key = f"{family}_sources"
    values = ensure_list_source(row.get(key))
    if source not in values:
        values.append(source)
    row[key] = values


def bump_count(row: dict[str, Any], keys: tuple[str, ...], primary_key: str, source: str) -> int:
    current = get_count(row, keys)
    already = source in ensure_list_source(row.get(primary_key.replace("_count", "_sources")))
    new_value = current if already else current + 1
    row[primary_key] = max(new_value, as_int(row.get(primary_key), 0))
    cov = row.setdefault("coverage", {}) if isinstance(row.setdefault("coverage", {}), dict) else {}
    cov[primary_key] = row[primary_key]
    return row[primary_key]


def mark_inventory_row(row: dict[str, Any], game_id: str, deep_ok: bool, odds_ok: bool, detail_ok: bool) -> None:
    source_ids = row.setdefault("source_ids", {}) if isinstance(row.setdefault("source_ids", {}), dict) else {}
    source_ids["sstats"] = str(game_id)
    provider_source_ids = row.setdefault("provider_source_ids", {}) if isinstance(row.setdefault("provider_source_ids", {}), dict) else {}
    provider_source_ids["sstats"] = str(game_id)
    sources_seen = ensure_list_source(row.get("sources_seen"))
    if "sstats" not in sources_seen:
        sources_seen.append("sstats")
    row["sources_seen"] = sources_seen
    row["sstats_game_id"] = str(game_id)
    row["sstats_deep_enriched"] = bool(deep_ok)
    row["sstats_detail_enriched"] = bool(detail_ok)
    row["sstats_odds_rescue_enriched"] = bool(odds_ok)
    cov = row.setdefault("coverage", {}) if isinstance(row.setdefault("coverage", {}), dict) else {}
    if deep_ok:
        add_source(row, "context", "sstats")
        add_source(row, "xg", "sstats")
        add_source(row, "form", "sstats")
        row["context_sources_count"] = max(get_count(row, CONTEXT_COUNT_KEYS) + (0 if "sstats" in ensure_list_source(row.get("context_sources"))[:-1] else 0), as_int(row.get("context_sources_count"), 0), len(ensure_list_source(row.get("context_sources"))))
        row["xg_sources_count"] = max(as_int(row.get("xg_sources_count"), 0), len(ensure_list_source(row.get("xg_sources"))))
        row["form_sources_count"] = max(as_int(row.get("form_sources_count"), 0), len(ensure_list_source(row.get("form_sources"))))
        cov["context"] = True
        cov["xg"] = True
        cov["form"] = True
        cov["context_sources_count"] = row["context_sources_count"]
        cov["xg_sources_count"] = row["xg_sources_count"]
        cov["form_sources_count"] = row["form_sources_count"]
        row["latest_context_sources_max"] = max(as_int(row.get("latest_context_sources_max"), 0), row["context_sources_count"])
    if detail_ok:
        cov["lineups"] = True
        cov["venue_referee"] = True
    if odds_ok:
        add_source(row, "odds", "sstats")
        row["odds_sources_count"] = max(get_count(row, ODDS_COUNT_KEYS), len(ensure_list_source(row.get("odds_sources"))))
        row["price_confirmation_sources_count"] = max(as_int(row.get("price_confirmation_sources_count"), 0), row["odds_sources_count"])
        row["latest_odds_sources_max"] = max(as_int(row.get("latest_odds_sources_max"), 0), row["odds_sources_count"])
        cov["odds"] = True
        cov["odds_sources_count"] = row["odds_sources_count"]


async def call_sstats(client: httpx.AsyncClient, command: str, path: str, params: dict[str, Any]) -> dict[str, Any]:
    api_key = env("SSTATS_API_KEY")
    req_params = dict(params)
    if api_key:
        req_params.setdefault("apikey", api_key)
    started = time.perf_counter()
    payload: Any = None
    error = ""
    http_status: int | None = None
    try:
        response = await client.get(BASE_URL + path, params=req_params)
        http_status = response.status_code
        try:
            payload = response.json()
        except Exception:
            payload = response.text
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    rs = rows(payload)
    status = "OK" if http_status and 200 <= http_status < 300 and (rs or isinstance(payload, dict)) else "ERROR"
    if http_status == 429:
        status = "RATE_LIMIT"
    elif http_status in {401, 403}:
        status = "AUTH"
    return {
        "command": command,
        "path": path,
        "status": status,
        "http_status": http_status,
        "rows_count": len(rs),
        "duration_ms": round((time.perf_counter() - started) * 1000, 1),
        "error": error,
        "sample": safe_sample(rs[:2]),
    }


async def enrich() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    if not truthy("SSTATS_DEEP_ENRICHMENT_ENABLED", True):
        payload = {"status": "disabled", "reason": "SSTATS_DEEP_ENRICHMENT_ENABLED=false"}
        write_json(JSON_OUT, payload)
        TXT_OUT.write_text(render(payload), encoding="utf-8")
        return payload
    crosswalk = load_json(OUT_DIR / "latest-sstats-crosswalk.json", {})
    if not crosswalk or not isinstance(crosswalk.get("summary"), dict):
        crosswalk = await sstats_crosswalk_probe.run()
    inv_path = inventory_path_from_crosswalk(crosswalk)
    inventory = load_json(inv_path, {})
    matches = inventory.get("matches") if isinstance(inventory, dict) and isinstance(inventory.get("matches"), list) else []
    by_key: dict[str, dict[str, Any]] = {}
    for row in matches:
        if isinstance(row, dict):
            key = str(row.get("match_key") or row.get("canonical_match_id") or "")
            if key:
                by_key[key] = row
    queue = crosswalk.get("enrichment_queue") if isinstance(crosswalk.get("enrichment_queue"), list) else []
    max_requests = max(0, as_int(env("SSTATS_DEEP_DETAIL_LIMIT_PER_RUN"), 100))
    detail_limit = max(0, as_int(env("SSTATS_GAME_DETAIL_LIMIT_PER_RUN"), 12))
    odds_limit = max(0, as_int(env("SSTATS_ODDS_RESCUE_LIMIT_PER_RUN"), 30))
    timeout_seconds = float(env("SSTATS_DEEP_ENRICHMENT_TIMEOUT_SECONDS", "16"))
    request_count = 0
    enriched: list[dict[str, Any]] = []
    command_status: list[dict[str, Any]] = []

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout_seconds, connect=min(6.0, timeout_seconds)), follow_redirects=True, headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"}) as client:
        for item in queue:
            if request_count + 2 > max_requests:
                break
            if not isinstance(item, dict):
                continue
            key = str(item.get("match_key") or "")
            game_id = str(item.get("sstats_game_id") or "").strip()
            row = by_key.get(key)
            if not row or not game_id:
                continue
            glicko = await call_sstats(client, "games_glicko", f"/Games/glicko/{game_id}", {})
            request_count += 1
            last_stats = await call_sstats(client, "games_last_games_stats", "/Games/last-games-stats", {"gameId": game_id, "limit": 25, "sameLeague": "false", "sameSeason": "false", "homeAway": "false"})
            request_count += 1
            detail = {"status": "SKIPPED", "rows_count": 0}
            odds = {"status": "SKIPPED", "rows_count": 0}
            if detail_limit > 0 and request_count < max_requests:
                detail = await call_sstats(client, "games_detail", f"/Games/{game_id}", {})
                detail_limit -= 1
                request_count += 1
            needs_odds = get_count(row, ODDS_COUNT_KEYS) < as_int(env("SSTATS_ODDS_RESCUE_ONLY_IF_ODDS_SOURCES_LT"), 2)
            if needs_odds and odds_limit > 0 and request_count < max_requests:
                odds = await call_sstats(client, "odds_prematch_full", f"/Odds/{game_id}", {"opening": "false"})
                odds_limit -= 1
                request_count += 1
            command_status.extend([glicko, last_stats, detail, odds])
            deep_ok = glicko.get("status") == "OK" or last_stats.get("status") == "OK"
            detail_ok = detail.get("status") == "OK"
            odds_ok = odds.get("status") == "OK" and as_int(odds.get("rows_count"), 0) > 0
            mark_inventory_row(row, game_id, deep_ok, odds_ok, detail_ok)
            if deep_ok or odds_ok or detail_ok:
                enriched.append({
                    "match_key": key,
                    "game_id": game_id,
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "deep_ok": deep_ok,
                    "detail_ok": detail_ok,
                    "odds_ok": odds_ok,
                    "context_sources_count": row.get("context_sources_count"),
                    "odds_sources_count": row.get("odds_sources_count"),
                })

    inventory.setdefault("metadata", {}) if isinstance(inventory, dict) else None
    if isinstance(inventory.get("metadata"), dict):
        inventory["metadata"]["sstats_deep_inventory_enrichment"] = {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "request_count": request_count,
            "enriched_matches": len(enriched),
        }
    write_json(inv_path, inventory)
    # Keep common aliases in sync when they exist.
    for alias in (Path(".data/day_inventory/latest.json"), Path(".data/day_inventory/current.json"), Path(".data/day_inventory/today.json")):
        try:
            if alias.exists() and alias.resolve() != inv_path.resolve():
                write_json(alias, inventory)
        except Exception:
            pass
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "sstats_deep_inventory_enrichment_v1",
        "status": "ok",
        "inventory_path": str(inv_path),
        "crosswalk_matched": (crosswalk.get("summary") or {}).get("matched"),
        "queue_seen": len(queue),
        "request_count": request_count,
        "enriched_matches": len(enriched),
        "command_status_counts": {},
        "enriched_sample": enriched[:40],
        "command_sample": command_status[:20],
        "notes": [
            "This mutates day_inventory by adding provider_source_ids.sstats, source_ids.sstats and SStats context/xG/form/odds-rescue counters for successfully enriched matches.",
            "Odds rescue is called only when existing odds source count is below the configured threshold.",
        ],
    }
    counts: dict[str, int] = {}
    for row in command_status:
        status = str(row.get("status") or "unknown")
        counts[status] = counts.get(status, 0) + 1
    payload["command_status_counts"] = counts
    write_json(JSON_OUT, payload)
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return payload


def render(payload: dict[str, Any]) -> str:
    lines = [
        "# SStats deep inventory enrichment",
        f"status: {payload.get('status')}",
        f"inventory_path: {payload.get('inventory_path')}",
        f"crosswalk_matched: {payload.get('crosswalk_matched')}",
        f"queue_seen: {payload.get('queue_seen')}",
        f"request_count: {payload.get('request_count')}",
        f"enriched_matches: {payload.get('enriched_matches')}",
        f"command_status_counts: {json.dumps(payload.get('command_status_counts') or {}, ensure_ascii=False)}",
        "",
        "## Enriched sample",
    ]
    for item in payload.get("enriched_sample") or []:
        lines.append(f"- {item.get('home_team')} — {item.get('away_team')} | gameId={item.get('game_id')} deep={item.get('deep_ok')} detail={item.get('detail_ok')} odds={item.get('odds_ok')} context={item.get('context_sources_count')} odds_sources={item.get('odds_sources_count')}")
    return "\n".join(lines) + "\n"


def main() -> int:
    asyncio.run(enrich())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
