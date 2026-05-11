from __future__ import annotations

"""Merge discovery-first canonical pool into day inventory.

The provider-day discovery pool asks fixture-capable APIs for daily matches,
normalizes them, and preserves provider source_ids. This script applies that
pool to `.data/day_inventory/*.json` before coverage/enrichment so downstream
steps can use source_ids instead of late fuzzy matching.

It is intentionally conservative:
- merge to an existing inventory row when teams/kickoff are close;
- append only minimal canonical rows when the match is missing;
- mark provider/fixture/context/odds sources but leave publication guards intact.
"""

import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from scripts import provider_day_discovery_canonical_pool_v2 as discovery
from scripts import provider_day_discovery_canonical_pool as base_discovery
from scripts import sstats_crosswalk_probe

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "provider-day-discovery-inventory-merge.json"
TXT_OUT = OUT_DIR / "provider-day-discovery-inventory-merge.txt"
PRIMARY_PROVIDERS = {"odds_api_io", "bzzoiro", "sstats"}


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
    raw = base_discovery.env("DAY_INVENTORY_TARGET_DATE") or base_discovery.env("PROVIDER_SMOKE_TARGET_DATE")
    if raw:
        return raw[:10]
    return (datetime.now(UTC) + timedelta(hours=3)).date().isoformat()


def inventory_path() -> Path:
    candidates = [Path(".data/day_inventory") / f"{target_date_msk()}.json", Path(".data/day_inventory/latest.json"), Path(".data/day_inventory/current.json"), Path(".data/day_inventory/today.json")]
    for path in candidates:
        if path.exists():
            return path
    return candidates[0]


def inventory_aliases(primary: Path) -> list[Path]:
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


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value))) if value not in (None, "") else default
    except Exception:
        return default


def set_count(row: dict[str, Any], key: str, minimum: int) -> None:
    row[key] = max(as_int(row.get(key), 0), minimum)
    cov = row.setdefault("coverage", {})
    if isinstance(cov, dict):
        cov[key] = row[key]


def event_from_inventory(row: dict[str, Any]) -> dict[str, Any]:
    home = str(row.get("home_team") or row.get("home") or row.get("home_name") or "")
    away = str(row.get("away_team") or row.get("away") or row.get("away_name") or "")
    league = str(row.get("league_name") or row.get("league") or row.get("competition_name") or "")
    return {
        "home_team": home,
        "away_team": away,
        "league_name": league,
        "kickoff_utc": row.get("kickoff_utc") or row.get("commence_time") or row.get("start_time"),
        "home_norm": base_discovery.normalize(home),
        "away_norm": base_discovery.normalize(away),
        "league_norm": base_discovery.normalize(league),
    }


def best_inventory_match(canon: dict[str, Any], rows: list[dict[str, Any]], min_score: float = 0.74) -> tuple[dict[str, Any] | None, float, dict[str, Any]]:
    best_row: dict[str, Any] | None = None
    best_score = 0.0
    best_debug: dict[str, Any] = {}
    canon_event = {
        "home_norm": canon.get("home_norm") or base_discovery.normalize(canon.get("home_team")),
        "away_norm": canon.get("away_norm") or base_discovery.normalize(canon.get("away_team")),
        "league_norm": canon.get("league_norm") or base_discovery.normalize(canon.get("league_name")),
        "kickoff_utc": canon.get("kickoff_utc"),
    }
    for row in rows:
        score, debug = base_discovery.match_score(event_from_inventory(row), canon_event)
        if score > best_score:
            best_row, best_score, best_debug = row, score, debug
    if best_row is not None and best_score >= min_score:
        return best_row, best_score, best_debug
    return None, best_score, best_debug


def make_inventory_row(canon: dict[str, Any]) -> dict[str, Any]:
    return {
        "match_key": canon.get("canonical_match_key"),
        "canonical_match_id": canon.get("canonical_match_key"),
        "home_team": canon.get("home_team"),
        "away_team": canon.get("away_team"),
        "league_name": canon.get("league_name"),
        "kickoff_utc": canon.get("kickoff_utc"),
        "source": "provider_day_discovery_canonical_pool",
        "priority": len(canon.get("providers") or []),
        "coverage": {},
    }


def apply_sources(row: dict[str, Any], canon: dict[str, Any]) -> dict[str, Any]:
    providers = [str(p) for p in (canon.get("providers") or []) if p]
    source_ids = canon.get("source_ids") if isinstance(canon.get("source_ids"), dict) else {}
    row.setdefault("source_ids", {})
    row.setdefault("provider_source_ids", {})
    if isinstance(row.get("source_ids"), dict):
        row["source_ids"].update({str(k): str(v) for k, v in source_ids.items() if v not in (None, "")})
    if isinstance(row.get("provider_source_ids"), dict):
        row["provider_source_ids"].update({str(k): str(v) for k, v in source_ids.items() if v not in (None, "")})
    for provider in providers:
        add_source(row, "sources_seen", provider)
        add_source(row, "fixture_sources", provider)
    cov = row.setdefault("coverage", {})
    if not isinstance(cov, dict):
        cov = {}
        row["coverage"] = cov
    if "odds_api_io" in providers:
        add_source(row, "odds_sources", "odds_api_io")
        cov["odds"] = True
        set_count(row, "odds_sources_count", len(sources(row.get("odds_sources"))))
        row["price_confirmation_sources_count"] = max(as_int(row.get("price_confirmation_sources_count")), row["odds_sources_count"])
    if "bzzoiro" in providers:
        add_source(row, "context_sources", "bzzoiro")
        add_source(row, "xg_sources", "bzzoiro")
        cov["context"] = True
        cov["xg"] = True
        set_count(row, "context_sources_count", len(sources(row.get("context_sources"))))
        row["xg_sources_count"] = max(as_int(row.get("xg_sources_count")), len(sources(row.get("xg_sources"))))
    if "sstats" in providers:
        # SStats source_id is saved here; actual context/form/xG source is added
        # later by SStats deep enrichment after endpoint calls succeed.
        row["sstats_game_id"] = source_ids.get("sstats") or row.get("sstats_game_id")
    row["fixture_sources_count"] = max(as_int(row.get("fixture_sources_count")), len(sources(row.get("fixture_sources"))))
    row["provider_day_discovery_merged"] = True
    row["provider_day_primary_source_count"] = len(set(providers) & PRIMARY_PROVIDERS)
    row["provider_day_source_count"] = len(set(providers))
    return row


async def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    pool = load(OUT_DIR / "provider-day-discovery-canonical-pool.json", {})
    if not isinstance(pool.get("summary"), dict) or pool.get("mode") != "provider_day_discovery_canonical_pool_v2_cached_sstats":
        pool = await discovery.run()
    primary = inventory_path()
    inventory = load(primary, {})
    if not isinstance(inventory, dict):
        inventory = {"matches": []}
    matches = inventory.get("matches") if isinstance(inventory.get("matches"), list) else []
    inventory["matches"] = matches
    canonical_rows = pool.get("canonical_matches_sample") if isinstance(pool.get("canonical_matches_sample"), list) else []
    # The sample is capped; enough for smoke validation. Production replacement
    # should use the full canonical pool once promoted out of diagnostics.
    matched_existing = 0
    appended = 0
    updated_rows: list[dict[str, Any]] = []
    unmatched_low_score: list[dict[str, Any]] = []
    rows_dict = [row for row in matches if isinstance(row, dict)]
    for canon in canonical_rows:
        if not isinstance(canon, dict):
            continue
        row, score, debug = best_inventory_match(canon, rows_dict)
        if row is None:
            row = make_inventory_row(canon)
            matches.append(row)
            rows_dict.append(row)
            appended += 1
            merge_type = "appended"
        else:
            matched_existing += 1
            merge_type = "matched_existing"
        apply_sources(row, canon)
        updated_rows.append({"merge_type": merge_type, "score": round(score, 4), "home_team": row.get("home_team"), "away_team": row.get("away_team"), "providers": canon.get("providers"), "source_ids": canon.get("source_ids"), "context_sources_count": row.get("context_sources_count"), "odds_sources_count": row.get("odds_sources_count")})
        if merge_type == "appended" and score > 0:
            unmatched_low_score.append({"score": round(score, 4), "debug": debug, "home_team": canon.get("home_team"), "away_team": canon.get("away_team")})
    meta = inventory.setdefault("metadata", {})
    if isinstance(meta, dict):
        meta["provider_day_discovery_inventory_merge"] = {"created_at_utc": datetime.now(UTC).isoformat(), "matched_existing": matched_existing, "appended": appended, "canonical_rows_seen": len(canonical_rows)}
    for path in inventory_aliases(primary):
        write(path, inventory)
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "provider_day_discovery_inventory_merge_v1",
        "status": "ok",
        "inventory_path": str(primary),
        "aliases_written": [str(p) for p in inventory_aliases(primary)],
        "inventory_matches_after": len(matches),
        "canonical_rows_seen": len(canonical_rows),
        "matched_existing": matched_existing,
        "appended": appended,
        "updated_sample": updated_rows[:50],
        "unmatched_low_score_sample": unmatched_low_score[:20],
        "pool_summary": pool.get("summary") or {},
    }
    write(JSON_OUT, payload)
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return payload


def render(payload: dict[str, Any]) -> str:
    lines = [
        "# Provider day discovery inventory merge",
        f"status: {payload.get('status')}",
        f"inventory_path: {payload.get('inventory_path')}",
        f"aliases_written: {', '.join(payload.get('aliases_written') or [])}",
        f"canonical_rows_seen: {payload.get('canonical_rows_seen')}",
        f"matched_existing: {payload.get('matched_existing')}",
        f"appended: {payload.get('appended')}",
        f"inventory_matches_after: {payload.get('inventory_matches_after')}",
        "",
        "## Updated sample",
    ]
    for item in payload.get("updated_sample") or []:
        lines.append(f"- {item.get('merge_type')} score={item.get('score')} | {item.get('home_team')} — {item.get('away_team')} | providers={','.join(item.get('providers') or [])} context={item.get('context_sources_count')} odds={item.get('odds_sources_count')}")
    return "\n".join(lines) + "\n"


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
