from __future__ import annotations

"""Apply full SStats crosswalk IDs to day inventory.

After discovery-first merge, the inventory can grow from the old odds-first pool
to 300 matches. The SStats crosswalk must be applied to that merged inventory so
rows keep provider_source_ids.sstats/source_ids.sstats before deep enrichment and
coverage accounting.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts import provider_day_discovery_canonical_pool as normalize_helpers
from scripts import sstats_crosswalk_probe_v2

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "sstats-crosswalk-inventory-apply.json"
TXT_OUT = OUT_DIR / "sstats-crosswalk-inventory-apply.txt"


def load(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if value is not None else default
    except Exception:
        return default


def write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def target_date_msk() -> str:
    raw = normalize_helpers.env("DAY_INVENTORY_TARGET_DATE") or normalize_helpers.env("PROVIDER_SMOKE_TARGET_DATE")
    if raw:
        return raw[:10]
    return (datetime.now(UTC) + timedelta(hours=3)).date().isoformat()


def inventory_path(crosswalk: dict[str, Any]) -> Path:
    raw = str(crosswalk.get("inventory_path") or "")
    if raw and Path(raw).exists():
        return Path(raw)
    for path in (Path(".data/day_inventory") / f"{target_date_msk()}.json", Path(".data/day_inventory/latest.json"), Path(".data/day_inventory/current.json"), Path(".data/day_inventory/today.json")):
        if path.exists():
            return path
    return Path(".data/day_inventory") / f"{target_date_msk()}.json"


def aliases(primary: Path) -> list[Path]:
    paths = [primary, Path(".data/day_inventory") / f"{target_date_msk()}.json", Path(".data/day_inventory/latest.json"), Path(".data/day_inventory/current.json"), Path(".data/day_inventory/today.json")]
    out: list[Path] = []
    for path in paths:
        if path not in out:
            out.append(path)
    return out


def sources(value: Any) -> list[str]:
    parts = value if isinstance(value, list) else str(value or "").split(",")
    out: list[str] = []
    for item in parts:
        text = str(item or "").strip()
        if text and text.lower() not in {"none", "null", "unknown"} and text not in out:
            out.append(text)
    return out


def add_source(row: dict[str, Any], key: str, source: str) -> bool:
    vals = sources(row.get(key))
    added = source not in vals
    if added:
        vals.append(source)
    row[key] = vals
    return added


def score_row(row: dict[str, Any], matched: dict[str, Any]) -> float:
    inv = {
        "home_norm": normalize_helpers.normalize(row.get("home_team") or row.get("home")),
        "away_norm": normalize_helpers.normalize(row.get("away_team") or row.get("away")),
        "league_norm": normalize_helpers.normalize(row.get("league_name") or row.get("league")),
        "kickoff_utc": row.get("kickoff_utc") or row.get("commence_time") or row.get("start_time"),
    }
    event = {
        "home_norm": normalize_helpers.normalize(matched.get("home_team") or matched.get("sstats_home_team")),
        "away_norm": normalize_helpers.normalize(matched.get("away_team") or matched.get("sstats_away_team")),
        "league_norm": normalize_helpers.normalize(matched.get("league_name") or matched.get("sstats_league_name")),
        "kickoff_utc": matched.get("kickoff_utc") or matched.get("sstats_kickoff_utc"),
    }
    score, _debug = normalize_helpers.match_score(inv, event)
    return score


def apply_match(row: dict[str, Any], matched: dict[str, Any]) -> None:
    game_id = str(matched.get("sstats_game_id") or "").strip()
    if not game_id:
        return
    row.setdefault("source_ids", {})["sstats"] = game_id
    row.setdefault("provider_source_ids", {})["sstats"] = game_id
    row["sstats_game_id"] = game_id
    add_source(row, "sources_seen", "sstats")
    add_source(row, "fixture_sources", "sstats")
    row["fixture_sources_count"] = max(len(sources(row.get("fixture_sources"))), int(row.get("fixture_sources_count") or 0))
    row["sstats_crosswalk_applied"] = True
    cov = row.setdefault("coverage", {})
    if isinstance(cov, dict):
        cov["fixture_source_2plus"] = row["fixture_sources_count"] >= 2


async def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    cross = load(OUT_DIR / "latest-sstats-crosswalk.json", {})
    if not isinstance(cross.get("summary"), dict) or not isinstance(cross.get("matched"), list):
        cross = await sstats_crosswalk_probe_v2.run()
    primary = inventory_path(cross)
    inventory = load(primary, {})
    matches = inventory.get("matches") if isinstance(inventory, dict) and isinstance(inventory.get("matches"), list) else []
    matched_rows = [m for m in (cross.get("matched") or []) if isinstance(m, dict) and m.get("sstats_game_id")]
    by_key = {str(row.get("match_key") or row.get("canonical_match_id") or ""): row for row in matches if isinstance(row, dict)}
    applied = 0
    fuzzy_applied = 0
    skipped = 0
    sample: list[dict[str, Any]] = []
    for item in matched_rows:
        key = str(item.get("match_key") or "")
        row = by_key.get(key)
        fuzzy = False
        if row is None:
            best_row = None
            best_score = 0.0
            for candidate in matches:
                if not isinstance(candidate, dict):
                    continue
                score = score_row(candidate, item)
                if score > best_score:
                    best_score, best_row = score, candidate
            if best_row is not None and best_score >= 0.74:
                row = best_row
                fuzzy = True
        if row is None:
            skipped += 1
            continue
        apply_match(row, item)
        applied += 1
        fuzzy_applied += 1 if fuzzy else 0
        if len(sample) < 50:
            sample.append({"home_team": row.get("home_team"), "away_team": row.get("away_team"), "game_id": item.get("sstats_game_id"), "fuzzy": fuzzy, "fixture_sources_count": row.get("fixture_sources_count")})
    if isinstance(inventory, dict):
        meta = inventory.setdefault("metadata", {})
        if isinstance(meta, dict):
            meta["sstats_crosswalk_inventory_apply"] = {"created_at_utc": datetime.now(UTC).isoformat(), "matched_rows": len(matched_rows), "applied": applied, "fuzzy_applied": fuzzy_applied, "skipped": skipped}
    for path in aliases(primary):
        write(path, inventory)
    payload = {"created_at_utc": datetime.now(UTC).isoformat(), "mode": "sstats_crosswalk_inventory_apply_v1", "status": "ok", "inventory_path": str(primary), "aliases_written": [str(p) for p in aliases(primary)], "inventory_matches": len(matches), "matched_rows_seen": len(matched_rows), "applied": applied, "fuzzy_applied": fuzzy_applied, "skipped": skipped, "sample": sample}
    write(JSON_OUT, payload)
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return payload


def render(payload: dict[str, Any]) -> str:
    lines = ["# SStats crosswalk inventory apply", f"status: {payload.get('status')}", f"inventory_path: {payload.get('inventory_path')}", f"inventory_matches: {payload.get('inventory_matches')}", f"matched_rows_seen: {payload.get('matched_rows_seen')}", f"applied: {payload.get('applied')}", f"fuzzy_applied: {payload.get('fuzzy_applied')}", f"skipped: {payload.get('skipped')}", "", "## Sample"]
    for item in payload.get("sample") or []:
        lines.append(f"- {item.get('home_team')} — {item.get('away_team')} | gameId={item.get('game_id')} fuzzy={item.get('fuzzy')} fixture_sources={item.get('fixture_sources_count')}")
    return "\n".join(lines) + "\n"


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
