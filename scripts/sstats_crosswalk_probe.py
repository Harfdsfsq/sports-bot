from __future__ import annotations

"""SStats crosswalk probe for provider-smoke.

Goal: connect the 300-match day inventory to concrete SStats gameId values.
SStats deep smoke proved the detail endpoints work. This probe answers the next
question: how many inventory matches can be enriched through those endpoints and
which nearest matches should be enriched first?
"""

import asyncio
import json
import os
import re
import time
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

import httpx

UTC = timezone.utc
BASE_URL = "https://api.sstats.net"
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "latest-sstats-crosswalk.json"
TXT_OUT = OUT_DIR / "latest-sstats-crosswalk.txt"
UA = "HARIZON-provider-smoke-sstats-crosswalk/1.0"

STOPWORDS = {
    "fc", "cf", "sc", "afc", "club", "football", "soccer", "the", "fk", "sk", "ac", "as", "cd", "sd",
    "women", "w", "u19", "u20", "u21", "u23", "reserves", "ii", "b",
}


def env(name: str, default: str = "") -> str:
    return str(os.getenv(name) or default).strip()


def as_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value))) if value not in (None, "") else default
    except Exception:
        return default


def today_msk() -> str:
    return (datetime.now(UTC) + timedelta(hours=3)).date().isoformat()


def tomorrow_msk() -> str:
    return ((datetime.now(UTC) + timedelta(hours=3)).date() + timedelta(days=1)).isoformat()


def load_json(path: Path, default: Any) -> Any:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if value is not None else default
    except Exception:
        return default


def inventory_path() -> Path:
    target = env("DAY_INVENTORY_TARGET_DATE") or env("PROVIDER_SMOKE_TARGET_DATE")
    candidates: list[Path] = []
    if target:
        candidates.append(Path(".data/day_inventory") / f"{target}.json")
    candidates.extend([
        Path(".data/day_inventory/latest.json"),
        Path(".data/day_inventory/current.json"),
        Path(".data/day_inventory/today.json"),
    ])
    for path in candidates:
        if path.exists():
            return path
    return candidates[-1]


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    text = str(value).strip()
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(text[:19], fmt)
            return dt.replace(tzinfo=UTC)
        except Exception:
            continue
    return None


def normalize(text: Any) -> str:
    raw = str(text or "").lower().strip()
    raw = unicodedata.normalize("NFKD", raw).encode("ascii", "ignore").decode("ascii")
    raw = raw.replace("&", " and ")
    raw = re.sub(r"[^a-z0-9]+", " ", raw)
    tokens = [tok for tok in raw.split() if tok and tok not in STOPWORDS]
    return " ".join(tokens)


def sim(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return max(0.88, SequenceMatcher(None, a, b).ratio())
    aset, bset = set(a.split()), set(b.split())
    jaccard = len(aset & bset) / max(1, len(aset | bset))
    ratio = SequenceMatcher(None, a, b).ratio()
    return max(ratio, jaccard)


def first_text(row: dict[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        cur: Any = row
        for part in key.split("."):
            if not isinstance(cur, dict):
                cur = None
                break
            cur = cur.get(part)
        if isinstance(cur, dict):
            for name_key in ("name", "Name", "title", "Title", "shortName", "short_name"):
                if cur.get(name_key) not in (None, ""):
                    return str(cur.get(name_key)).strip()
        elif cur not in (None, ""):
            return str(cur).strip()
    return ""


def extract_sstats_event(row: dict[str, Any]) -> dict[str, Any] | None:
    home = first_text(row, (
        "homeTeam.name", "homeTeam.Name", "homeTeamName", "home_team", "home", "Home", "homeName", "team1", "Team1", "teamHome.name",
    ))
    away = first_text(row, (
        "awayTeam.name", "awayTeam.Name", "awayTeamName", "away_team", "away", "Away", "awayName", "team2", "Team2", "teamAway.name",
    ))
    if not home or not away:
        # Some SStats rows expose nested teams with different names.
        teams = row.get("teams") if isinstance(row.get("teams"), list) else []
        if len(teams) >= 2:
            home = first_text(teams[0] if isinstance(teams[0], dict) else {}, ("name", "Name"))
            away = first_text(teams[1] if isinstance(teams[1], dict) else {}, ("name", "Name"))
    if not home or not away:
        return None
    gid = first_text(row, ("id", "Id", "gameId", "GameId"))
    league = first_text(row, ("league.name", "league.Name", "leagueName", "LeagueName", "league", "League", "competition.name"))
    kickoff_raw = first_text(row, ("dateTime", "DateTime", "startTime", "StartTime", "kickoff", "Kickoff", "date", "Date", "gameTime", "GameTime"))
    kickoff = parse_dt(kickoff_raw)
    return {
        "game_id": gid,
        "home_team": home,
        "away_team": away,
        "league_name": league,
        "kickoff_utc": kickoff.isoformat() if kickoff else None,
        "home_norm": normalize(home),
        "away_norm": normalize(away),
        "league_norm": normalize(league),
        "raw_keys": sorted(str(k) for k in row.keys())[:40],
    }


def rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("data", "items", "result", "results", "matches", "games"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            nested = rows(value)
            if nested:
                return nested
    return [payload] if payload else []


async def fetch_sstats_games() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    api_key = env("SSTATS_API_KEY")
    calls = [
        ("games_today_date", {"Date": today_msk(), "TimeZone": 3, "Limit": 1000, "Offset": 0, "Order": 1}),
        ("games_upcoming", {"Upcoming": "true", "TimeZone": 3, "Limit": 1000, "Offset": 0, "Order": 1}),
        ("games_tomorrow_date", {"Date": tomorrow_msk(), "TimeZone": 3, "Limit": 1000, "Offset": 0, "Order": 1}),
    ]
    out_rows: list[dict[str, Any]] = []
    call_results: list[dict[str, Any]] = []
    timeout = float(env("SSTATS_CROSSWALK_TIMEOUT_SECONDS", "18"))
    async with httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=min(6.0, timeout)), follow_redirects=True, headers={"User-Agent": UA, "Accept": "application/json,text/plain,*/*"}) as client:
        for command, params in calls:
            if api_key:
                params = dict(params)
                params.setdefault("apikey", api_key)
            started = time.perf_counter()
            status = "ERROR"
            http_status: int | None = None
            rs: list[Any] = []
            error = ""
            try:
                response = await client.get(BASE_URL + "/Games/list", params=params)
                http_status = response.status_code
                try:
                    payload = response.json()
                except Exception:
                    payload = response.text
                rs = rows(payload)
                status = "OK" if http_status and 200 <= http_status < 300 else ("RATE_LIMIT" if http_status == 429 else "HTTP_ERROR")
            except Exception as exc:
                error = f"{type(exc).__name__}: {exc}"
            events = []
            for row in rs:
                if isinstance(row, dict):
                    event = extract_sstats_event(row)
                    if event:
                        events.append(event)
            out_rows.extend(events)
            call_results.append({
                "command": command,
                "status": status,
                "http_status": http_status,
                "duration_ms": round((time.perf_counter() - started) * 1000, 1),
                "rows_count": len(rs),
                "event_like_rows": len(events),
                "error": error,
            })
    # Dedupe by game_id + teams.
    deduped: dict[str, dict[str, Any]] = {}
    for event in out_rows:
        key = str(event.get("game_id") or "") or f"{event.get('home_norm')}|{event.get('away_norm')}|{event.get('kickoff_utc')}"
        deduped[key] = event
    return list(deduped.values()), call_results


def match_score(inv: dict[str, Any], event: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    ih = normalize(inv.get("home_team") or inv.get("home_team_norm"))
    ia = normalize(inv.get("away_team") or inv.get("away_team_norm"))
    il = normalize(inv.get("league_name"))
    eh = str(event.get("home_norm") or "")
    ea = str(event.get("away_norm") or "")
    el = str(event.get("league_norm") or "")
    home_sim = sim(ih, eh)
    away_sim = sim(ia, ea)
    swapped_sim = (sim(ih, ea) + sim(ia, eh)) / 2.0
    pair_sim = (home_sim + away_sim) / 2.0
    league_sim = sim(il, el)
    kickoff_inv = parse_dt(inv.get("kickoff_utc") or inv.get("commence_time"))
    kickoff_event = parse_dt(event.get("kickoff_utc"))
    time_bonus = 0.0
    delta_minutes: float | None = None
    if kickoff_inv and kickoff_event:
        delta_minutes = abs((kickoff_inv - kickoff_event).total_seconds()) / 60.0
        if delta_minutes <= 15:
            time_bonus = 0.12
        elif delta_minutes <= 60:
            time_bonus = 0.08
        elif delta_minutes <= 180:
            time_bonus = 0.04
        elif delta_minutes <= 720:
            time_bonus = 0.01
        else:
            time_bonus = -0.15
    base_score = pair_sim * 0.78 + league_sim * 0.10 + time_bonus
    if swapped_sim > pair_sim + 0.08:
        base_score -= 0.25
    return max(0.0, min(1.0, base_score)), {
        "home_sim": round(home_sim, 4),
        "away_sim": round(away_sim, 4),
        "league_sim": round(league_sim, 4),
        "pair_sim": round(pair_sim, 4),
        "swapped_pair_sim": round(swapped_sim, 4),
        "delta_minutes": round(delta_minutes, 1) if delta_minutes is not None else None,
    }


def coverage_flags(row: dict[str, Any]) -> dict[str, bool]:
    coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
    return {
        "odds": bool(coverage.get("odds")),
        "context": bool(coverage.get("context")),
        "xg": bool(coverage.get("xg")),
        "form": bool(coverage.get("form")),
        "weather": bool(coverage.get("weather")),
        "news": bool(coverage.get("news")),
        "ready_for_model": bool(coverage.get("ready_for_model")),
        "ready_for_publish": bool(coverage.get("ready_for_publish")),
    }


def bucket(kickoff: Any) -> str:
    dt = parse_dt(kickoff)
    if not dt:
        return "unknown"
    hours = (dt - datetime.now(UTC)).total_seconds() / 3600.0
    if hours < 0:
        return "started"
    if hours <= 2:
        return "0_2h"
    if hours <= 6:
        return "2_6h"
    if hours <= 12:
        return "6_12h"
    if hours <= 24:
        return "12_24h"
    return "24h_plus"


async def run() -> dict[str, Any]:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    inv_path = inventory_path()
    inventory = load_json(inv_path, {})
    matches = [row for row in inventory.get("matches", []) if isinstance(row, dict)] if isinstance(inventory, dict) else []
    limit = max(1, as_int(env("SSTATS_CROSSWALK_MATCH_LIMIT"), 300))
    selected = sorted(matches, key=lambda r: (str(r.get("kickoff_utc") or ""), -float(r.get("priority") or 0)))[:limit]
    sstats_events, call_results = await fetch_sstats_games()
    min_score = float(env("SSTATS_CROSSWALK_MIN_SCORE", "0.72"))
    matched: list[dict[str, Any]] = []
    unmatched: list[dict[str, Any]] = []
    for inv in selected:
        best_event: dict[str, Any] | None = None
        best_score = 0.0
        best_debug: dict[str, Any] = {}
        for event in sstats_events:
            score, debug = match_score(inv, event)
            if score > best_score:
                best_score = score
                best_event = event
                best_debug = debug
        flags = coverage_flags(inv)
        row_base = {
            "match_key": inv.get("match_key") or inv.get("canonical_match_id"),
            "kickoff_utc": inv.get("kickoff_utc"),
            "bucket": bucket(inv.get("kickoff_utc")),
            "league_name": inv.get("league_name"),
            "home_team": inv.get("home_team"),
            "away_team": inv.get("away_team"),
            "priority": inv.get("priority"),
            "coverage": flags,
        }
        if best_event and best_score >= min_score:
            matched.append({
                **row_base,
                "score": round(best_score, 4),
                "debug": best_debug,
                "sstats_game_id": best_event.get("game_id"),
                "sstats_home_team": best_event.get("home_team"),
                "sstats_away_team": best_event.get("away_team"),
                "sstats_league_name": best_event.get("league_name"),
                "sstats_kickoff_utc": best_event.get("kickoff_utc"),
                "recommended_deep_endpoints": ["/Games/glicko/{id}", "/Games/last-games-stats", "/Games/{id}", "/Games/profits", "/Games/injuries"],
                "recommended_odds_endpoint": "/Odds/{gameId}" if not flags.get("odds") else None,
            })
        else:
            unmatched.append({**row_base, "best_score": round(best_score, 4), "best_candidate": best_event, "debug": best_debug})

    by_bucket: dict[str, dict[str, int]] = {}
    for item in matched:
        b = str(item.get("bucket") or "unknown")
        by_bucket.setdefault(b, {"matched": 0, "missing_context": 0, "missing_xg": 0, "missing_form": 0, "odds_rescue": 0})
        by_bucket[b]["matched"] += 1
        cov = item.get("coverage") or {}
        if not cov.get("context"):
            by_bucket[b]["missing_context"] += 1
        if not cov.get("xg"):
            by_bucket[b]["missing_xg"] += 1
        if not cov.get("form"):
            by_bucket[b]["missing_form"] += 1
        if not cov.get("odds"):
            by_bucket[b]["odds_rescue"] += 1

    future_order = {"0_2h": 0, "2_6h": 1, "6_12h": 2, "12_24h": 3, "24h_plus": 4, "unknown": 5, "started": 6}
    enrichment_queue = sorted(
        [item for item in matched if not (item.get("coverage") or {}).get("context") or not (item.get("coverage") or {}).get("xg") or not (item.get("coverage") or {}).get("form")],
        key=lambda item: (future_order.get(str(item.get("bucket")), 5), str(item.get("kickoff_utc") or ""), -float(item.get("priority") or 0)),
    )[:80]

    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "sstats_crosswalk_probe_v1",
        "duration_seconds": round(time.perf_counter() - started, 2),
        "inventory_path": str(inv_path),
        "inventory_matches_seen": len(matches),
        "inventory_matches_checked": len(selected),
        "sstats_events_seen": len(sstats_events),
        "sstats_call_results": call_results,
        "min_score": min_score,
        "summary": {
            "matched": len(matched),
            "unmatched": len(unmatched),
            "match_rate_pct": round((len(matched) / max(1, len(selected))) * 100, 2),
            "potential_context_deep": len(matched),
            "potential_xg_or_rating": len(matched),
            "potential_form": len(matched),
            "potential_odds_rescue": sum(1 for item in matched if not (item.get("coverage") or {}).get("odds")),
        },
        "by_bucket": by_bucket,
        "matched_sample": matched[:40],
        "unmatched_sample": unmatched[:40],
        "enrichment_queue": enrichment_queue,
        "notes": [
            "A matched row means SStats gameId can be used for deep context: glicko, last-games-stats, game detail, profits, injuries and optional Odds/{gameId}.",
            "This probe does not mutate inventory yet; it estimates the runtime uplift and exposes bad aliases/time mismatches.",
        ],
    }
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return payload


def render(payload: dict[str, Any]) -> str:
    s = payload.get("summary") or {}
    lines = [
        "# SStats inventory crosswalk probe",
        f"UTC: {payload.get('created_at_utc')}",
        f"Inventory checked: {payload.get('inventory_matches_checked')}/{payload.get('inventory_matches_seen')} | SStats events: {payload.get('sstats_events_seen')}",
        f"Matched: {s.get('matched', 0)} | unmatched: {s.get('unmatched', 0)} | rate={s.get('match_rate_pct', 0)}%",
        f"Potential uplift: context_deep={s.get('potential_context_deep', 0)} xg_or_rating={s.get('potential_xg_or_rating', 0)} form={s.get('potential_form', 0)} odds_rescue={s.get('potential_odds_rescue', 0)}",
        "",
        "## SStats calls",
    ]
    for row in payload.get("sstats_call_results") or []:
        lines.append(f"- {row.get('command')}: {row.get('status')} http={row.get('http_status')} rows={row.get('rows_count')} event_like={row.get('event_like_rows')}")
    lines += ["", "## By kickoff bucket"]
    for bucket_name, row in sorted((payload.get("by_bucket") or {}).items()):
        lines.append(f"- {bucket_name}: matched={row.get('matched')} missing_context={row.get('missing_context')} missing_xg={row.get('missing_xg')} missing_form={row.get('missing_form')} odds_rescue={row.get('odds_rescue')}")
    lines += ["", "## Next SStats deep enrichment queue"]
    for item in (payload.get("enrichment_queue") or [])[:25]:
        lines.append(
            f"- {item.get('bucket')} | {item.get('kickoff_utc')} | {item.get('home_team')} — {item.get('away_team')} "
            f"=> gameId={item.get('sstats_game_id')} score={item.get('score')} missing="
            f"context:{not (item.get('coverage') or {}).get('context')} xg:{not (item.get('coverage') or {}).get('xg')} form:{not (item.get('coverage') or {}).get('form')}"
        )
    lines += ["", "## Unmatched sample"]
    for item in (payload.get("unmatched_sample") or [])[:20]:
        lines.append(f"- {item.get('bucket')} | {item.get('home_team')} — {item.get('away_team')} | best_score={item.get('best_score')}")
    return "\n".join(lines) + "\n"


def main() -> int:
    asyncio.run(run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
