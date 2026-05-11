from __future__ import annotations

"""Provider backfill priority plan.

Reads provider-smoke artifacts and creates a deterministic repair/enrichment
queue:
1. top providers first: odds-api.io, Bzzoiro, SStats;
2. backup providers only for missing roles: weather, news, mapping, odds rescue.

This is diagnostic/planning output for provider-smoke. It does not publish picks
and does not call external APIs.
"""

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "provider-backfill-priority-plan.json"
TXT_OUT = OUT_DIR / "provider-backfill-priority-plan.txt"

ROLE_PROVIDERS = {
    "odds_primary": ["odds_api_io_account1", "odds_api_io_account2"],
    "context_primary": ["bzzoiro", "sstats_deep"],
    "xg_primary": ["bzzoiro", "sstats_glicko", "sstats_last_games_stats"],
    "form_primary": ["sstats_last_games_stats", "sstats_games_list", "football_data_co_uk"],
    "odds_rescue": ["sstats_odds_game", "bzzoiro_odds_comparison", "highlightly_odds", "sportlogic_dedicated_only"],
    "mapping_rescue": ["thesportsdb", "football_data", "allsportsapi_scoped", "wikidata_cache", "highlightly"],
    "weather": ["open_meteo", "weatherapi", "openweathermap"],
    "news": ["newsapi", "currents", "guardian", "newsdata", "gnews_after_auth_fix"],
}

BUCKET_ORDER = {"0_2h": 0, "2_6h": 1, "6_12h": 2, "12_24h": 3, "24h_plus": 4, "unknown": 5, "started": 6}


def load(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def missing_roles(item: dict[str, Any]) -> list[str]:
    missing = set(str(x) for x in (item.get("missing") or []))
    odds_sources = int(item.get("odds_sources") or 0)
    context_sources = int(item.get("context_sources") or 0)
    roles: list[str] = []
    if odds_sources < 2 or "odds_source_2plus" in missing:
        roles.append("odds_rescue")
    if context_sources < 2 or "context_source_2plus" in missing:
        roles.append("context_primary")
    if "xg" in missing:
        roles.append("xg_primary")
    if "weather" in missing:
        roles.append("weather")
    if "fixture_source_2plus" in missing:
        roles.append("mapping_rescue")
    return roles


def match_key(item: dict[str, Any]) -> tuple[int, str, int, int]:
    bucket = str(item.get("bucket") or "unknown")
    roles = missing_roles(item)
    return (BUCKET_ORDER.get(bucket, 5), str(item.get("kickoff_utc") or ""), -len(roles), int(item.get("context_sources") or 0) + int(item.get("odds_sources") or 0))


def build_plan() -> dict[str, Any]:
    matrix = load(OUT_DIR / "provider-smoke-coverage-matrix.json")
    crosswalk = load(OUT_DIR / "latest-sstats-crosswalk.json")
    blueprint = load(OUT_DIR / "provider-signal-coverage-blueprint.json")
    queue = matrix.get("next_enrichment_queue") if isinstance(matrix.get("next_enrichment_queue"), list) else []
    sstats_queue = crosswalk.get("enrichment_queue") if isinstance(crosswalk.get("enrichment_queue"), list) else []
    sstats_by_key: dict[str, dict[str, Any]] = {}
    for row in sstats_queue:
        if isinstance(row, dict):
            key = str(row.get("match_key") or "")
            if key:
                sstats_by_key[key] = row
    tasks: list[dict[str, Any]] = []
    for item in sorted([x for x in queue if isinstance(x, dict)], key=match_key):
        key = str(item.get("match_key") or "")
        roles = missing_roles(item)
        sstats = sstats_by_key.get(key, {})
        providers: list[str] = []
        for role in roles:
            providers.extend(ROLE_PROVIDERS.get(role, []))
        providers = list(dict.fromkeys(providers))
        task = {
            "match_key": key,
            "bucket": item.get("bucket"),
            "kickoff_utc": item.get("kickoff_utc"),
            "home_team": item.get("home_team"),
            "away_team": item.get("away_team"),
            "league_name": item.get("league_name"),
            "current_odds_sources": item.get("odds_sources"),
            "current_context_sources": item.get("context_sources"),
            "missing_roles": roles,
            "recommended_providers": providers,
            "sstats_game_id": sstats.get("sstats_game_id"),
            "sstats_score": sstats.get("score"),
            "primary_actions": [],
            "backup_actions": [],
        }
        if "context_primary" in roles and sstats:
            task["primary_actions"].append("SStats: call /Games/glicko/{id} and /Games/last-games-stats; count as independent context+xG/form source")
        if "odds_rescue" in roles and sstats:
            task["primary_actions"].append("SStats: call /Odds/{gameId} only if odds-api.io has <2 price confirmations")
        if "context_primary" in roles:
            task["primary_actions"].append("Bzzoiro: use predictions/events/details/metadata/odds-comparison when matched")
        if "weather" in roles:
            task["backup_actions"].append("Mapping first: derive venue/city from Bzzoiro/SStats/TheSportsDB/Wikidata, then Open-Meteo bulk forecast")
        if "mapping_rescue" in roles:
            task["backup_actions"].append("Use TheSportsDB + football-data cached + AllSportsAPI scoped league discovery for fixture aliases")
        if "odds_rescue" in roles and not sstats:
            task["backup_actions"].append("Try Bzzoiro odds comparison, Highlightly odds, SportLogic only in dedicated quota-safe run")
        tasks.append(task)
    role_counter = Counter(role for task in tasks for role in task.get("missing_roles", []))
    provider_counter = Counter(provider for task in tasks for provider in task.get("recommended_providers", []))
    summary = {
        "matrix_version": matrix.get("matrix_version"),
        "coverage_totals": matrix.get("totals") if isinstance(matrix.get("totals"), dict) else {},
        "sstats_crosswalk": (blueprint.get("sstats_crosswalk_plan") if isinstance(blueprint.get("sstats_crosswalk_plan"), dict) else {}) or (matrix.get("sstats_crosswalk_projection") if isinstance(matrix.get("sstats_crosswalk_projection"), dict) else {}),
        "tasks_total": len(tasks),
        "missing_role_counts": dict(role_counter),
        "recommended_provider_counts": dict(provider_counter),
    }
    return {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "provider_backfill_priority_plan_v1",
        "summary": summary,
        "role_providers": ROLE_PROVIDERS,
        "top_api_strategy": [
            "odds-api.io remains fixture+price backbone; maximize two-account bookmaker coverage before using odds rescue.",
            "Bzzoiro is first context/xG/prediction source for matched fixtures.",
            "SStats deep is second context/xG/form source for all crosswalked gameIds and odds rescue only where odds-api.io has <2 sources.",
            "TheSportsDB/football-data/AllSportsAPI/Wikidata are mapping backfill, not primary model signal unless context remains missing.",
            "Weather/news providers should run only after venue/team alias binding exists and only for nearest/high-priority matches.",
        ],
        "tasks": tasks[:120],
    }


def render(payload: dict[str, Any]) -> str:
    summary = payload.get("summary") or {}
    lines = [
        "# Provider backfill priority plan",
        f"UTC: {payload.get('created_at_utc')}",
        f"tasks_total: {summary.get('tasks_total', 0)}",
        "",
        "## Strategy",
    ]
    for item in payload.get("top_api_strategy") or []:
        lines.append(f"- {item}")
    lines += ["", "## Missing roles"]
    for role, count in sorted((summary.get("missing_role_counts") or {}).items(), key=lambda kv: (-kv[1], kv[0])):
        lines.append(f"- {role}: {count}")
    lines += ["", "## Recommended provider pressure"]
    for provider, count in sorted((summary.get("recommended_provider_counts") or {}).items(), key=lambda kv: (-kv[1], kv[0]))[:30]:
        lines.append(f"- {provider}: {count}")
    lines += ["", "## Top tasks"]
    for task in payload.get("tasks", [])[:30]:
        lines.append(
            f"- {task.get('bucket')} | {task.get('kickoff_utc')} | {task.get('home_team')} — {task.get('away_team')} | "
            f"odds={task.get('current_odds_sources')} context={task.get('current_context_sources')} roles={','.join(task.get('missing_roles') or [])} "
            f"sstats={task.get('sstats_game_id') or '-'}"
        )
        for action in (task.get("primary_actions") or [])[:3]:
            lines.append(f"  - primary: {action}")
        for action in (task.get("backup_actions") or [])[:2]:
            lines.append(f"  - backup: {action}")
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    payload = build_plan()
    JSON_OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    TXT_OUT.write_text(render(payload), encoding="utf-8")
    print(render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
