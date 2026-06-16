from __future__ import annotations

"""SStats deep inventory enrichment v4.

v3 proved actual SStats requests work, but priority/counting still treated rows
with coverage.context=true and no numeric context_sources_count as context=0.
That wasted budget on 0->1 rows. v4 treats existing bool coverage as one source
for prioritization and counter increments, so adding SStats becomes 1->2 when a
match already had context/odds from another provider.
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


def bool_cov(row: dict[str, Any], key: str) -> bool:
    cov = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    if bool(cov.get(key)) or bool(row.get(key)):
        return True
    if key == "context" and (bool(row.get("bzzoiro_context")) or bool(row.get("has_context"))):
        return True
    if key == "odds" and (bool(row.get("has_odds")) or bool(row.get("odds"))):
        return True
    return False


def count_family(row: dict[str, Any], family: str) -> int:
    if family == "context":
        base = v2.count(row, v2.CONTEXT_KEYS)
        list_count = len(v2.src_list(row, "context_sources"))
        bool_count = 1 if bool_cov(row, "context") else 0
        return max(base, list_count, bool_count)
    if family == "odds":
        base = v2.count(row, v2.ODDS_KEYS)
        list_count = len(v2.src_list(row, "odds_sources"))
        bool_count = 1 if bool_cov(row, "odds") else 0
        return max(base, list_count, bool_count)
    if family == "xg":
        return max(v2.as_int(row.get("xg_sources_count")), len(v2.src_list(row, "xg_sources")), 1 if bool_cov(row, "xg") else 0)
    if family == "form":
        return max(v2.as_int(row.get("form_sources_count")), len(v2.src_list(row, "form_sources")), 1 if bool_cov(row, "form") else 0)
    return 0


def _float_or_none(value: Any) -> float | None:
    try:
        if value in (None, "") or isinstance(value, dict):
            return None
        return float(str(value).replace(",", "."))
    except Exception:
        return None


def _first_float(row: dict[str, Any], keys: list[str]) -> float | None:
    for key in keys:
        value = _float_or_none(row.get(key))
        if value is not None:
            return value
    return None


def _nested_first_float(payload: Any, keys: list[str], *, side: str) -> float | None:
    side_aliases = [side, side.capitalize(), "team1" if side == "home" else "team2", "Team1" if side == "home" else "Team2"]
    if isinstance(payload, dict):
        for alias in side_aliases:
            obj = payload.get(alias)
            if isinstance(obj, dict):
                value = _first_float(obj, keys)
                if value is not None:
                    return value
        value = _first_float(payload, [f"{side}_{key}" for key in keys] + [f"{side}{key}" for key in keys])
        if value is not None:
            return value
        for child in payload.values():
            value = _nested_first_float(child, keys, side=side)
            if value is not None:
                return value
    elif isinstance(payload, list):
        for item in payload:
            value = _nested_first_float(item, keys, side=side)
            if value is not None:
                return value
    return None


def _valid_xg_pair(home: Any, away: Any) -> tuple[float | None, float | None]:
    h = _float_or_none(home)
    a = _float_or_none(away)
    if h is None or a is None:
        return None, None
    if h < 0 or a < 0 or h + a < 0.25:
        return None, None
    return round(max(0.15, min(4.5, h)), 3), round(max(0.15, min(4.5, a)), 3)


def extract_expected_goals(*payloads: Any) -> tuple[float | None, float | None, str]:
    for source_name, payload in payloads:
        if payload in (None, ""):
            continue
        if source_name == "last_games_stats":
            hxg = _nested_first_float(payload, ["xg", "xG", "expectedGoals", "expected_goals"], side="home")
            axg = _nested_first_float(payload, ["xg", "xG", "expectedGoals", "expected_goals"], side="away")
            h, a = _valid_xg_pair(hxg, axg)
            if h is not None and a is not None:
                return h, a, source_name
            hgf = _nested_first_float(payload, ["goalsFor", "goals_for", "goalsScored", "scored"], side="home")
            aga = _nested_first_float(payload, ["goalsAgainst", "goals_against", "conceded"], side="away")
            agf = _nested_first_float(payload, ["goalsFor", "goals_for", "goalsScored", "scored"], side="away")
            hga = _nested_first_float(payload, ["goalsAgainst", "goals_against", "conceded"], side="home")
            if None not in (hgf, aga, agf, hga):
                h, a = _valid_xg_pair((float(hgf) + float(aga)) / 2.0, (float(agf) + float(hga)) / 2.0)
                if h is not None and a is not None:
                    return h, a, "last_games_stats_goals_blend"
        for row in v2.rows(payload):
            if not isinstance(row, dict):
                continue
            h, a = _valid_xg_pair(
                _first_float(row, ["ExpectedGoalsHome", "HomeXg", "homeXg", "xgHome", "expectedHomeGoals", "home_expected_goals", "home_xg"]),
                _first_float(row, ["ExpectedGoalsAway", "AwayXg", "awayXg", "xgAway", "expectedAwayGoals", "away_expected_goals", "away_xg"]),
            )
            if h is not None and a is not None:
                return h, a, source_name
    return None, None, "missing"


def mark(row: dict[str, Any], game_id: str, deep_ok: bool, detail_ok: bool, odds_ok: bool, before_context: int, before_odds: int, *, glicko_payload: Any = None, last_stats_payload: Any = None, detail_payload: Any = None) -> None:
    row.setdefault("source_ids", {})["sstats"] = str(game_id)
    row.setdefault("provider_source_ids", {})["sstats"] = str(game_id)
    v2.add_src(row, "sources_seen", "sstats")
    row["sstats_game_id"] = str(game_id)
    row["sstats_deep_enriched"] = deep_ok
    row["sstats_detail_enriched"] = detail_ok
    row["sstats_odds_rescue_enriched"] = odds_ok
    cov = row.setdefault("coverage", {})
    if not isinstance(cov, dict):
        cov = {}
        row["coverage"] = cov
    existing_home, existing_away = _valid_xg_pair(row.get("expected_home"), row.get("expected_away"))
    xg_home, xg_away, xg_source = extract_expected_goals(
        ("last_games_stats", last_stats_payload),
        ("glicko", glicko_payload),
        ("game_detail", detail_payload),
    )
    if xg_home is None or xg_away is None:
        xg_home, xg_away, xg_source = existing_home, existing_away, "existing_inventory"
    has_xg_pair = xg_home is not None and xg_away is not None
    if has_xg_pair:
        row["expected_home"] = xg_home
        row["expected_away"] = xg_away
        row["sstats_expected_home"] = xg_home
        row["sstats_expected_away"] = xg_away
        row["sstats_xg_source"] = xg_source
    if deep_ok:
        c_added = v2.add_src(row, "context_sources", "sstats")
        f_added = v2.add_src(row, "form_sources", "sstats")
        row["context_sources_count"] = v2.set_count(row, "context_sources_count", "context_sources", before_context, c_added)
        if has_xg_pair:
            x_added = v2.add_src(row, "xg_sources", "sstats")
            row["xg_sources_count"] = v2.set_count(row, "xg_sources_count", "xg_sources", count_family(row, "xg"), x_added)
        row["form_sources_count"] = v2.set_count(row, "form_sources_count", "form_sources", count_family(row, "form"), f_added)
        row["latest_context_sources_max"] = max(v2.as_int(row.get("latest_context_sources_max")), row["context_sources_count"])
        row["latest_confirmation_sources_max"] = max(v2.as_int(row.get("latest_confirmation_sources_max")), row["context_sources_count"])
        cov.update({"context": True, "form": True})
        cov["xg"] = has_xg_pair
    if detail_ok:
        cov.update({"lineups": True, "venue_referee": True})
    if odds_ok:
        o_added = v2.add_src(row, "odds_sources", "sstats")
        row["odds_sources_count"] = v2.set_count(row, "odds_sources_count", "odds_sources", before_odds, o_added)
        row["price_confirmation_sources_count"] = max(v2.as_int(row.get("price_confirmation_sources_count")), row["odds_sources_count"])
        row["latest_odds_sources_max"] = max(v2.as_int(row.get("latest_odds_sources_max")), row["odds_sources_count"])
        cov.update({"odds": True, "odds_sources_count": row["odds_sources_count"]})


def bucket_rank(bucket: str) -> int:
    return {"0_2h": 0, "2_6h": 1, "6_12h": 2, "12_24h": 3, "24h_plus": 4, "unknown": 5, "started": 6}.get(str(bucket or "unknown"), 5)


def priority(item: dict[str, Any], by_key: dict[str, dict[str, Any]]) -> tuple[int, int, str, str]:
    row = by_key.get(str(item.get("match_key") or ""), {})
    context = count_family(row, "context")
    odds = count_family(row, "odds")
    if context >= 1 and odds >= 2:
        group = 0
    elif context >= 1:
        group = 1
    elif odds >= 2:
        group = 2
    elif odds >= 1:
        group = 3
    else:
        group = 4
    return (group, bucket_rank(str(item.get("bucket") or "unknown")), str(item.get("kickoff_utc") or ""), str(item.get("match_key") or ""))


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

    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(6.0, timeout)), follow_redirects=True, headers={"User-Agent": "HARIZON-sstats-deep-v4"}) as client:
        for item in queue:
            if req + 2 > max_req:
                break
            key = str(item.get("match_key") or "")
            game_id = str(item.get("sstats_game_id") or "").strip()
            row = by_key.get(key)
            if not game_id or row is None:
                continue
            before_context = count_family(row, "context")
            before_odds = count_family(row, "odds")
            group = f"context{before_context}_odds{before_odds}"
            group_counts[group] = group_counts.get(group, 0) + 1
            g = await v2.call(client, "glicko", f"/Games/glicko/{game_id}", {}, include_payload=True)
            l = await v2.call(client, "last_games_stats", "/Games/last-games-stats", {"gameId": game_id, "limit": 25, "sameLeague": "false", "sameSeason": "false", "homeAway": "false"}, include_payload=True)
            req += 2
            d = {"status": "SKIPPED", "rows": 0}
            o = {"status": "SKIPPED", "rows": 0}
            if detail_left and req < max_req:
                d = await v2.call(client, "game_detail", f"/Games/{game_id}", {}, include_payload=True)
                detail_left -= 1
                req += 1
            if before_odds < threshold and odds_left and req < max_req:
                o = await v2.call(client, "odds", f"/Odds/{game_id}", {"opening": "false"})
                odds_left -= 1
                req += 1
            statuses.extend([{k: v for k, v in item.items() if k != "payload"} for item in (g, l, d, o)])
            deep_ok = g.get("status") == "OK" or l.get("status") == "OK"
            detail_ok = d.get("status") == "OK"
            odds_ok = o.get("status") == "OK" and v2.as_int(o.get("rows")) > 0
            mark(row, game_id, deep_ok, detail_ok, odds_ok, before_context, before_odds, glicko_payload=g.get("payload"), last_stats_payload=l.get("payload"), detail_payload=d.get("payload"))
            if deep_ok or detail_ok or odds_ok:
                enriched.append({"match_key": key, "game_id": game_id, "home_team": row.get("home_team"), "away_team": row.get("away_team"), "deep_ok": deep_ok, "detail_ok": detail_ok, "odds_ok": odds_ok, "before_context": before_context, "after_context": row.get("context_sources_count"), "before_odds": before_odds, "after_odds": row.get("odds_sources_count"), "expected_home": row.get("expected_home"), "expected_away": row.get("expected_away"), "xg_source": row.get("sstats_xg_source")})

    if isinstance(inventory, dict):
        meta = inventory.setdefault("metadata", {})
        if isinstance(meta, dict):
            meta["sstats_deep_inventory_enrichment"] = {"created_at_utc": datetime.now(UTC).isoformat(), "request_count": req, "enriched_matches": len(enriched), "version": "v4_bool_count_alias_sync"}
    for path in inventory_aliases(primary_path):
        v2.write(path, inventory)
    counts: dict[str, int] = {}
    for s in statuses:
        counts[str(s.get("status"))] = counts.get(str(s.get("status")), 0) + 1
    payload = {"created_at_utc": datetime.now(UTC).isoformat(), "mode": "sstats_deep_inventory_enrichment_v4", "status": "ok", "inventory_path": str(primary_path), "inventory_aliases_written": [str(p) for p in inventory_aliases(primary_path)], "crosswalk_matched": (cross.get("summary") or {}).get("matched"), "queue_seen": len(raw_queue), "request_count": req, "enriched_matches": len(enriched), "priority_group_counts": group_counts, "command_status_counts": counts, "enriched_sample": enriched[:50], "command_sample": statuses[:20]}
    v2.write(JSON_OUT, payload)
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return payload


def render(payload: dict[str, Any]) -> str:
    lines = ["# SStats deep inventory enrichment v4", f"status: {payload.get('status')}", f"inventory_path: {payload.get('inventory_path')}", f"aliases_written: {', '.join(payload.get('inventory_aliases_written') or [])}", f"crosswalk_matched: {payload.get('crosswalk_matched')}", f"queue_seen: {payload.get('queue_seen')}", f"request_count: {payload.get('request_count')}", f"enriched_matches: {payload.get('enriched_matches')}", f"priority_group_counts: {json.dumps(payload.get('priority_group_counts') or {}, ensure_ascii=False)}", f"command_status_counts: {json.dumps(payload.get('command_status_counts') or {}, ensure_ascii=False)}", "", "## Enriched sample"]
    for item in payload.get("enriched_sample") or []:
        lines.append(f"- {item.get('home_team')} — {item.get('away_team')} | gameId={item.get('game_id')} deep={item.get('deep_ok')} detail={item.get('detail_ok')} odds={item.get('odds_ok')} context:{item.get('before_context')}→{item.get('after_context')} odds:{item.get('before_odds')}→{item.get('after_odds')}")
    return "\n".join(lines) + "\n"


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
