from __future__ import annotations

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

ODDS_KEYS = ("price_confirmation_sources_count", "odds_sources_count", "latest_odds_sources_max", "independent_odds_sources_count")
CONTEXT_KEYS = ("context_sources_count", "latest_context_sources_max", "confirmation_sources_count", "xg_sources_count", "form_sources_count")


def env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value))) if value not in (None, "") else default
    except Exception:
        return default


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(',', '.')) if value not in (None, "") else default
    except Exception:
        return default


def load(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if value is not None else default
    except Exception:
        return default


def write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def inv_path(crosswalk: dict[str, Any]) -> Path:
    raw = str(crosswalk.get("inventory_path") or "").strip()
    if raw and Path(raw).exists():
        return Path(raw)
    return sstats_crosswalk_probe.inventory_path()


def rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("data", "items", "result", "results", "matches", "games", "odds"):
            value = payload.get(key)
            if isinstance(value, list):
                return value
            if isinstance(value, dict):
                nested = rows(value)
                if nested:
                    return nested
        return [payload] if payload else []
    return []


def count(row: dict[str, Any], keys: tuple[str, ...]) -> int:
    best = 0
    for src in (row, row.get("coverage") if isinstance(row.get("coverage"), dict) else {}, row.get("source_summary") if isinstance(row.get("source_summary"), dict) else {}):
        for key in keys:
            best = max(best, as_int(src.get(key), 0))
    return best


def src_list(row: dict[str, Any], key: str) -> list[str]:
    value = row.get(key)
    parts = value if isinstance(value, list) else str(value or "").split(",")
    out: list[str] = []
    for item in parts:
        text = str(item or "").strip()
        if text and text.lower() not in {"none", "null", "unknown"} and text not in out:
            out.append(text)
    return out


def add_src(row: dict[str, Any], key: str, source: str) -> bool:
    vals = src_list(row, key)
    added = source not in vals
    if added:
        vals.append(source)
    row[key] = vals
    return added


def set_count(row: dict[str, Any], key: str, list_key: str, before: int, added: bool) -> int:
    value = max(as_int(row.get(key), 0), before + (1 if added else 0), len(src_list(row, list_key)))
    row[key] = value
    cov = row.setdefault("coverage", {})
    if isinstance(cov, dict):
        cov[key] = value
    return value


def mark(row: dict[str, Any], game_id: str, deep_ok: bool, detail_ok: bool, odds_ok: bool) -> None:
    row.setdefault("source_ids", {})["sstats"] = str(game_id)
    row.setdefault("provider_source_ids", {})["sstats"] = str(game_id)
    add_src(row, "sources_seen", "sstats")
    row["sstats_game_id"] = str(game_id)
    row["sstats_deep_enriched"] = deep_ok
    row["sstats_detail_enriched"] = detail_ok
    row["sstats_odds_rescue_enriched"] = odds_ok
    cov = row.setdefault("coverage", {})
    if not isinstance(cov, dict):
        cov = {}
        row["coverage"] = cov
    if deep_ok:
        c0, x0, f0 = count(row, CONTEXT_KEYS), as_int(row.get("xg_sources_count")), as_int(row.get("form_sources_count"))
        c_added = add_src(row, "context_sources", "sstats")
        x_added = add_src(row, "xg_sources", "sstats")
        f_added = add_src(row, "form_sources", "sstats")
        row["context_sources_count"] = set_count(row, "context_sources_count", "context_sources", c0, c_added)
        row["xg_sources_count"] = set_count(row, "xg_sources_count", "xg_sources", x0, x_added)
        row["form_sources_count"] = set_count(row, "form_sources_count", "form_sources", f0, f_added)
        row["latest_context_sources_max"] = max(as_int(row.get("latest_context_sources_max")), row["context_sources_count"])
        cov.update({"context": True, "xg": True, "form": True})
    if detail_ok:
        cov.update({"lineups": True, "venue_referee": True})
    if odds_ok:
        o0 = count(row, ODDS_KEYS)
        o_added = add_src(row, "odds_sources", "sstats")
        row["odds_sources_count"] = set_count(row, "odds_sources_count", "odds_sources", o0, o_added)
        row["price_confirmation_sources_count"] = max(as_int(row.get("price_confirmation_sources_count")), row["odds_sources_count"])
        row["latest_odds_sources_max"] = max(as_int(row.get("latest_odds_sources_max")), row["odds_sources_count"])
        cov.update({"odds": True, "odds_sources_count": row["odds_sources_count"]})


async def call(client: httpx.AsyncClient, name: str, path: str, params: dict[str, Any], *, include_payload: bool = False) -> dict[str, Any]:
    params = dict(params)
    key = env("SSTATS_API_KEY")
    if key:
        params.setdefault("apikey", key)
    started = time.perf_counter()
    sleep_s = max(0.0, as_float(env("SSTATS_DEEP_REQUEST_SLEEP_SECONDS"), 0.42))
    max_retries = max(0, as_int(env("SSTATS_DEEP_429_RETRIES"), 2))
    last: dict[str, Any] | None = None
    for attempt in range(max_retries + 1):
        if attempt > 0:
            await asyncio.sleep(max(1.0, sleep_s) * attempt)
        try:
            response = await client.get(BASE_URL + path, params=params)
            status = response.status_code
            try:
                payload = response.json()
            except Exception:
                payload = response.text
            result = {"name": name, "status": "OK" if 200 <= status < 300 else str(status), "http_status": status, "rows": len(rows(payload)), "ms": round((time.perf_counter() - started) * 1000, 1), "attempt": attempt + 1}
            if include_payload:
                result["payload"] = payload
            last = result
            if status != 429:
                break
        except Exception as exc:
            last = {"name": name, "status": "ERROR", "error": f"{type(exc).__name__}: {exc}", "rows": 0, "attempt": attempt + 1}
            break
    if sleep_s > 0:
        await asyncio.sleep(sleep_s)
    return last or {"name": name, "status": "ERROR", "error": "no_result", "rows": 0}


async def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cross = load(OUT_DIR / "latest-sstats-crosswalk.json", {})
    if not isinstance(cross.get("summary"), dict):
        cross = await sstats_crosswalk_probe.run()
    path = inv_path(cross)
    inventory = load(path, {})
    matches = inventory.get("matches") if isinstance(inventory, dict) and isinstance(inventory.get("matches"), list) else []
    by_key = {str(m.get("match_key") or m.get("canonical_match_id") or ""): m for m in matches if isinstance(m, dict)}
    queue = [q for q in (cross.get("enrichment_queue") or []) if isinstance(q, dict)]
    max_req = max(0, as_int(env("SSTATS_DEEP_DETAIL_LIMIT_PER_RUN"), 100))
    detail_left = max(0, as_int(env("SSTATS_GAME_DETAIL_LIMIT_PER_RUN"), 12))
    odds_left = max(0, as_int(env("SSTATS_ODDS_RESCUE_LIMIT_PER_RUN"), 30))
    threshold = max(1, as_int(env("SSTATS_ODDS_RESCUE_ONLY_IF_ODDS_SOURCES_LT"), 2))
    req = 0
    enriched: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    timeout = float(env("SSTATS_DEEP_ENRICHMENT_TIMEOUT_SECONDS", "16"))
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(6.0, timeout)), follow_redirects=True, headers={"User-Agent": "HARIZON-sstats-deep-v2"}) as client:
        for item in queue:
            if req + 2 > max_req:
                break
            game_id = str(item.get("sstats_game_id") or "").strip()
            row = by_key.get(str(item.get("match_key") or ""))
            if not game_id or row is None:
                continue
            g = await call(client, "glicko", f"/Games/glicko/{game_id}", {})
            l = await call(client, "last_games_stats", "/Games/last-games-stats", {"gameId": game_id, "limit": 25, "sameLeague": "false", "sameSeason": "false", "homeAway": "false"})
            req += 2
            d = {"status": "SKIPPED", "rows": 0}
            o = {"status": "SKIPPED", "rows": 0}
            if detail_left and req < max_req:
                d = await call(client, "game_detail", f"/Games/{game_id}", {})
                detail_left -= 1
                req += 1
            if count(row, ODDS_KEYS) < threshold and odds_left and req < max_req:
                o = await call(client, "odds", f"/Odds/{game_id}", {"opening": "false"})
                odds_left -= 1
                req += 1
            statuses.extend([g, l, d, o])
            deep_ok = g.get("status") == "OK" or l.get("status") == "OK"
            detail_ok = d.get("status") == "OK"
            odds_ok = o.get("status") == "OK" and as_int(o.get("rows")) > 0
            mark(row, game_id, deep_ok, detail_ok, odds_ok)
            if deep_ok or detail_ok or odds_ok:
                enriched.append({"match_key": item.get("match_key"), "game_id": game_id, "home_team": row.get("home_team"), "away_team": row.get("away_team"), "deep_ok": deep_ok, "detail_ok": detail_ok, "odds_ok": odds_ok, "context_sources_count": row.get("context_sources_count"), "odds_sources_count": row.get("odds_sources_count")})
    if isinstance(inventory, dict):
        meta = inventory.setdefault("metadata", {})
        if isinstance(meta, dict):
            meta["sstats_deep_inventory_enrichment"] = {"created_at_utc": datetime.now(UTC).isoformat(), "request_count": req, "enriched_matches": len(enriched)}
    write(path, inventory)
    for alias in (Path(".data/day_inventory/latest.json"), Path(".data/day_inventory/current.json"), Path(".data/day_inventory/today.json")):
        try:
            if alias.exists() and alias.resolve() != path.resolve():
                write(alias, inventory)
        except Exception:
            pass
    counts: dict[str, int] = {}
    for s in statuses:
        counts[str(s.get("status"))] = counts.get(str(s.get("status")), 0) + 1
    payload = {"created_at_utc": datetime.now(UTC).isoformat(), "mode": "sstats_deep_inventory_enrichment_v2", "status": "ok", "inventory_path": str(path), "crosswalk_matched": (cross.get("summary") or {}).get("matched"), "queue_seen": len(queue), "request_count": req, "enriched_matches": len(enriched), "command_status_counts": counts, "enriched_sample": enriched[:40], "command_sample": statuses[:20]}
    write(JSON_OUT, payload)
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return payload


def render(payload: dict[str, Any]) -> str:
    lines = ["# SStats deep inventory enrichment", f"status: {payload.get('status')}", f"inventory_path: {payload.get('inventory_path')}", f"crosswalk_matched: {payload.get('crosswalk_matched')}", f"queue_seen: {payload.get('queue_seen')}", f"request_count: {payload.get('request_count')}", f"enriched_matches: {payload.get('enriched_matches')}", f"command_status_counts: {json.dumps(payload.get('command_status_counts') or {}, ensure_ascii=False)}", "", "## Enriched sample"]
    for item in payload.get("enriched_sample") or []:
        lines.append(f"- {item.get('home_team')} — {item.get('away_team')} | gameId={item.get('game_id')} deep={item.get('deep_ok')} detail={item.get('detail_ok')} odds={item.get('odds_ok')} context={item.get('context_sources_count')} odds_sources={item.get('odds_sources_count')}")
    return "\n".join(lines) + "\n"


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
