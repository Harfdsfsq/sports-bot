from __future__ import annotations

"""SStats deep inventory enrichment v3.

Fixes two runtime issues found in provider-smoke:
1. v2 enriched `.data/day_inventory/latest.json`, while the coverage matrix read
   `.data/day_inventory/<target_date>.json`; v3 writes all inventory aliases.
2. v2 consumed the SStats budget on rows with 0 context first. v3 prioritizes
   matches where SStats can immediately create 2+ context sources and move a
   match closer to model/publication readiness.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from scripts import apply_sstats_deep_inventory_enrichment_v2 as v2
from scripts import sstats_crosswalk_probe

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "latest-sstats-deep-inventory-enrichment.json"
TXT_OUT = OUT_DIR / "latest-sstats-deep-inventory-enrichment.txt"


def target_date_msk() -> str:
    raw = v2.env("DAY_INVENTORY_TARGET_DATE") or v2.env("PROVIDER_SMOKE_TARGET_DATE")
    if raw:
        return raw[:10]
    return (datetime.now(UTC) + timedelta(hours=3)).date().isoformat()


def inventory_aliases(primary: Path) -> list[Path]:
    paths = [primary, Path(".data/day_inventory/latest.json"), Path(".data/day_inventory/current.json"), Path(".data/day_inventory/today.json"), Path(".data/day_inventory") / f"{target_date_msk()}.json"]
    out: list[Path] = []
    for path in paths:
        if path not in out:
            out.append(path)
    return out


def bucket_rank(bucket: str) -> int:
    return {"0_2h": 0, "2_6h": 1, "6_12h": 2, "12_24h": 3, "24h_plus": 4, "unknown": 5, "started": 6}.get(str(bucket or "unknown"), 5)


def priority(item: dict[str, Any], by_key: dict[str, dict[str, Any]]) -> tuple[int, int, str, str]:
    row = by_key.get(str(item.get("match_key") or ""), {})
    context = v2.count(row, v2.CONTEXT_KEYS)
    odds = v2.count(row, v2.ODDS_KEYS)
    xg = v2.as_int(row.get("xg_sources_count"), 0) or (1 if (row.get("coverage") or {}).get("xg") else 0)
    # Highest priority: can immediately become 2-context and already has/near has lines.
    if context >= 1 and odds >= 2:
        group = 0
    elif context >= 1:
        group = 1
    elif odds >= 2:
        group = 2
    elif context == 0 and odds == 0:
        group = 4
    else:
        group = 3
    # Prefer missing xG/form among otherwise good rows.
    xg_penalty = 0 if xg == 0 else 1
    return (group, xg_penalty + bucket_rank(str(item.get("bucket") or "unknown")), str(item.get("kickoff_utc") or ""), str(item.get("match_key") or ""))


async def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cross = v2.load(OUT_DIR / "latest-sstats-crosswalk.json", {})
    if not isinstance(cross.get("summary"), dict):
        cross = await sstats_crosswalk_probe.run()
    primary_path = v2.inv_path(cross)
    inventory = v2.load(primary_path, {})
    matches = inventory.get("matches") if isinstance(inventory, dict) and isinstance(inventory.get("matches"), list) else []
    by_key = {str(m.get("match_key") or m.get("canonical_match_id") or ""): m for m in matches if isinstance(m, dict)}
    raw_queue = [q for q in (cross.get("enrichment_queue") or []) if isinstance(q, dict)]
    queue = sorted(raw_queue, key=lambda item: priority(item, by_key))

    max_req = max(0, v2.as_int(v2.env("SSTATS_DEEP_DETAIL_LIMIT_PER_RUN"), 100))
    detail_left = max(0, v2.as_int(v2.env("SSTATS_GAME_DETAIL_LIMIT_PER_RUN"), 12))
    odds_left = max(0, v2.as_int(v2.env("SSTATS_ODDS_RESCUE_LIMIT_PER_RUN"), 30))
    threshold = max(1, v2.as_int(v2.env("SSTATS_ODDS_RESCUE_ONLY_IF_ODDS_SOURCES_LT"), 2))
    req = 0
    enriched: list[dict[str, Any]] = []
    statuses: list[dict[str, Any]] = []
    group_counts: dict[str, int] = {}
    timeout = float(v2.env("SSTATS_DEEP_ENRICHMENT_TIMEOUT_SECONDS", "16"))

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(6.0, timeout)), follow_redirects=True, headers={"User-Agent": "HARIZON-sstats-deep-v3"}) as client:
        for item in queue:
            if req + 2 > max_req:
                break
            key = str(item.get("match_key") or "")
            game_id = str(item.get("sstats_game_id") or "").strip()
            row = by_key.get(key)
            if not game_id or row is None:
                continue
            before_context = v2.count(row, v2.CONTEXT_KEYS)
            before_odds = v2.count(row, v2.ODDS_KEYS)
            group = f"context{before_context}_odds{before_odds}"
            group_counts[group] = group_counts.get(group, 0) + 1

            g = await v2.call(client, "glicko", f"/Games/glicko/{game_id}", {})
            l = await v2.call(client, "last_games_stats", "/Games/last-games-stats", {"gameId": game_id, "limit": 25, "sameLeague": "false", "sameSeason": "false", "homeAway": "false"})
            req += 2
            d = {"status": "SKIPPED", "rows": 0}
            o = {"status": "SKIPPED", "rows": 0}
            if detail_left and req < max_req:
                d = await v2.call(client, "game_detail", f"/Games/{game_id}", {})
                detail_left -= 1
                req += 1
            if before_odds < threshold and odds_left and req < max_req:
                o = await v2.call(client, "odds", f"/Odds/{game_id}", {"opening": "false"})
                odds_left -= 1
                req += 1
            statuses.extend([g, l, d, o])
            deep_ok = g.get("status") == "OK" or l.get("status") == "OK"
            detail_ok = d.get("status") == "OK"
            odds_ok = o.get("status") == "OK" and v2.as_int(o.get("rows")) > 0
            v2.mark(row, game_id, deep_ok, detail_ok, odds_ok)
            if deep_ok or detail_ok or odds_ok:
                enriched.append({
                    "match_key": key,
                    "game_id": game_id,
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "deep_ok": deep_ok,
                    "detail_ok": detail_ok,
                    "odds_ok": odds_ok,
                    "before_context": before_context,
                    "after_context": row.get("context_sources_count"),
                    "before_odds": before_odds,
                    "after_odds": row.get("odds_sources_count"),
                })

    if isinstance(inventory, dict):
        meta = inventory.setdefault("metadata", {})
        if isinstance(meta, dict):
            meta["sstats_deep_inventory_enrichment"] = {"created_at_utc": datetime.now(UTC).isoformat(), "request_count": req, "enriched_matches": len(enriched), "version": "v3_prioritized_alias_sync"}
    for path in inventory_aliases(primary_path):
        v2.write(path, inventory)

    counts: dict[str, int] = {}
    for s in statuses:
        counts[str(s.get("status"))] = counts.get(str(s.get("status")), 0) + 1
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "sstats_deep_inventory_enrichment_v3",
        "status": "ok",
        "inventory_path": str(primary_path),
        "inventory_aliases_written": [str(p) for p in inventory_aliases(primary_path)],
        "crosswalk_matched": (cross.get("summary") or {}).get("matched"),
        "queue_seen": len(raw_queue),
        "request_count": req,
        "enriched_matches": len(enriched),
        "priority_group_counts": group_counts,
        "command_status_counts": counts,
        "enriched_sample": enriched[:50],
        "command_sample": statuses[:20],
    }
    v2.write(JSON_OUT, payload)
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return payload


def render(payload: dict[str, Any]) -> str:
    lines = [
        "# SStats deep inventory enrichment v3",
        f"status: {payload.get('status')}",
        f"inventory_path: {payload.get('inventory_path')}",
        f"aliases_written: {', '.join(payload.get('inventory_aliases_written') or [])}",
        f"crosswalk_matched: {payload.get('crosswalk_matched')}",
        f"queue_seen: {payload.get('queue_seen')}",
        f"request_count: {payload.get('request_count')}",
        f"enriched_matches: {payload.get('enriched_matches')}",
        f"priority_group_counts: {json.dumps(payload.get('priority_group_counts') or {}, ensure_ascii=False)}",
        f"command_status_counts: {json.dumps(payload.get('command_status_counts') or {}, ensure_ascii=False)}",
        "",
        "## Enriched sample",
    ]
    for item in payload.get("enriched_sample") or []:
        lines.append(f"- {item.get('home_team')} — {item.get('away_team')} | gameId={item.get('game_id')} deep={item.get('deep_ok')} detail={item.get('detail_ok')} odds={item.get('odds_ok')} context:{item.get('before_context')}→{item.get('after_context')} odds:{item.get('before_odds')}→{item.get('after_odds')}")
    return "\n".join(lines) + "\n"


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
