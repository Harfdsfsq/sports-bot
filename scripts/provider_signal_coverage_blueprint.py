from __future__ import annotations

"""Provider signal coverage blueprint for provider-smoke.

This diagnostic contract separates predictive hard context from soft mapping,
weather/news support and market evidence.  The goal is to show whether the
current top-300 inventory is merely covered, or covered with signals that can
actually improve forecast quality.
"""

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
OUT_DIR = Path(".data/exports")
JSON_OUT = OUT_DIR / "provider-signal-coverage-blueprint.json"
TXT_OUT = OUT_DIR / "provider-signal-coverage-blueprint.txt"
LATEST_JSON_OUT = OUT_DIR / "latest-provider-signal-coverage-blueprint.json"
LATEST_TXT_OUT = OUT_DIR / "latest-provider-signal-coverage-blueprint.txt"

PROVIDER_ROLES: dict[str, dict[str, Any]] = {
    "odds_api_io": {"tier": 1, "signals": ["odds", "fixture_inventory"], "quota": "100/hour/account; project has 2 accounts x 2 bookmakers", "runtime": "inventory + odds/multi batches"},
    "bzzoiro": {"tier": 1, "signals": ["context", "xg", "prediction", "lineups", "metadata", "odds_comparison", "fixture_mapping"], "quota": "docs say no public rate limit; still cache/timeout", "runtime": "day events + priority detail endpoints"},
    "sstats": {"tier": 1, "signals": ["form", "xg", "rating", "lineups", "injuries", "odds", "profits", "fixture_mapping"], "quota": "project/contract 150/min; uploaded spec says 30/min per IP without key and 300/min shared without key", "runtime": "list once + deep detail only for matched priority games"},
    "football_data": {"tier": 2, "signals": ["fixture_mapping", "standings", "team_mapping", "competition_mapping"], "quota": "10/min registered free", "runtime": "cache daily matches/standings; cooldown on 429"},
    "allsportsapi": {"tier": 2, "signals": ["fixture_mapping", "standings", "team_mapping", "livescore"], "quota": "260/hour free soccer; limited random leagues", "runtime": "discover accessible leagues then scoped fixtures"},
    "thesportsdb": {"tier": 2, "signals": ["fixture_mapping", "team_aliases", "league_aliases", "venue_mapping"], "quota": "30/min free", "runtime": "eventsday + team/league alias cache"},
    "clubelo": {"tier": 2, "signals": ["rating", "team_strength"], "quota": "no published numeric quota; cache daily CSV", "runtime": "daily CSV once; rating signal only"},
    "open_meteo": {"tier": 2, "signals": ["weather"], "quota": "fair use 10000/day 5000/hour 600/min", "runtime": "bulk coordinates from venue map"},
    "weatherapi": {"tier": 3, "signals": ["weather", "venue_weather_mapping"], "quota": "100000/month free", "runtime": "priority match forecast fallback"},
    "openweathermap": {"tier": 3, "signals": ["weather", "geocoding"], "quota": "60/min; 1M/month for free weather APIs", "runtime": "geocoding/fallback"},
    "meteostat": {"tier": 4, "signals": ["historical_weather"], "quota": "RapidAPI free around 500/month", "runtime": "historical/backtest fallback only"},
    "newsapi": {"tier": 3, "signals": ["news", "injury_news", "lineup_news"], "quota": "100/day developer", "runtime": "team alias shortlist only"},
    "currents": {"tier": 3, "signals": ["news"], "quota": "1000/day free", "runtime": "team alias shortlist only"},
    "gnews": {"tier": 4, "signals": ["news"], "quota": "100/day free", "runtime": "disabled until auth fixed"},
    "newsdata": {"tier": 3, "signals": ["news"], "quota": "200 credits/day free", "runtime": "team alias shortlist only"},
    "guardian": {"tier": 3, "signals": ["news"], "quota": "500/day and 1/sec developer", "runtime": "section=football + team alias shortlist"},
    "sportlogic": {"tier": 4, "signals": ["odds", "fixture_mapping", "outcomes"], "quota": "500/day 10/min free", "runtime": "disabled in broad smoke; dedicated repair only"},
    "futrixmetrics": {"tier": 4, "signals": ["ratings", "model_context"], "quota": "public basic 300/hour 30 RPM", "runtime": "schema discovery then priority context"},
    "highlightly": {"tier": 3, "signals": ["fixture_mapping", "standings", "lineups", "stats", "odds"], "quota": "100/day basic", "runtime": "daily list + priority details"},
    "football_data_co_uk": {"tier": 3, "signals": ["historical_results", "historical_odds", "market_priors"], "quota": "free CSV; responsible use", "runtime": "offline cache/backtesting"},
    "free_football_rapidapi": {"tier": 4, "signals": ["fixture_mapping", "stats", "lineups", "odds"], "quota": "often 100/day; dashboard-specific", "runtime": "disabled unless quota available"},
    "wikidata": {"tier": 3, "signals": ["team_aliases", "venue_mapping", "coordinates", "city_mapping"], "quota": "SPARQL 60s/60s, 5 parallel, 30 error/min", "runtime": "offline/cache aliases only"},
}

TARGETS = {
    "odds_sources_min": 2,
    "context_sources_min": 2,
    "required_match_flags": ["odds", "context"],
    "desired_match_flags": ["xg", "form", "rating", "weather", "news", "lineups", "injuries", "venue"],
}

HARD_CONTEXT = {"sstats", "bzzoiro", "clubelo", "futrixmetrics", "api_football", "highlightly", "allsportsapi", "football_data_co_uk"}
SOFT_CONTEXT = {"football_data", "thesportsdb", "openligadb", "wikidata", "dayinventory", "providerdaydiscoverycanonicalpool", "metadata"}
ENV_CONTEXT = {"open_meteo", "weatherapi", "openweathermap", "meteostat", "weather"}
NEWS_CONTEXT = {"newsapi", "currents", "gnews", "newsdata", "guardian", "news"}
MARKET_SOURCES = {"odds_api_io", "bzzoiro", "sportlogic", "sstats", "betfair_exchange", "pinnacle", "fonbet"}


def load_json(path: Path) -> dict[str, Any]:
    try:
        obj = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def collect_provider_results(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, dict):
        for key in ("results", "checks"):
            value = payload.get(key)
            if isinstance(value, list):
                rows.extend([item for item in value if isinstance(item, dict)])
        for key in ("raw_smoke_payload", "api_full_data_enrichment", "provider_status_summary"):
            value = payload.get(key)
            if isinstance(value, dict):
                rows.extend(collect_provider_results(value))
        diagnostics = payload.get("diagnostics")
        if isinstance(diagnostics, dict):
            providers = diagnostics.get("providers")
            if isinstance(providers, list):
                rows.extend([item for item in providers if isinstance(item, dict)])
    return rows


def source_items(value: Any) -> list[str]:
    if isinstance(value, dict):
        return [str(k) for k, v in value.items() if str(k).strip() and v not in (None, "", False, [], {})]
    if isinstance(value, list):
        return [str(x) for x in value if str(x).strip()]
    if isinstance(value, str):
        return [x for x in re.split(r"[,|;/]+", value) if x.strip()]
    return []


def norm_source(value: Any) -> str:
    text = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    text = re.sub(r"[^a-z0-9_]+", "", text)
    aliases = {"oddsapiio": "odds_api_io", "bzzoiro_v2": "bzzoiro", "club_elo": "clubelo", "open_meteo_forecast": "open_meteo"}
    return aliases.get(text, text)


def collect_sources(row: dict[str, Any], keys: tuple[str, ...]) -> set[str]:
    out: set[str] = set()
    for obj in (row, row.get("coverage") if isinstance(row.get("coverage"), dict) else {}, row.get("source_summary") if isinstance(row.get("source_summary"), dict) else {}):
        if isinstance(obj, dict):
            for key in keys:
                out.update(norm_source(x) for x in source_items(obj.get(key)))
    return {x for x in out if x and x not in {"none", "null", "unknown"}}


def classify_match_signals(row: dict[str, Any]) -> dict[str, Any]:
    context = collect_sources(row, ("context_sources", "context_confirmations", "confirmation_sources"))
    market = collect_sources(row, ("odds_sources", "line_sources", "price_sources"))
    if row.get("weather") or row.get("weather_factor"):
        context.add("weather")
    if row.get("clubelo_diff") or row.get("clubelo_home"):
        context.add("clubelo")
    hard = sorted(context & HARD_CONTEXT)
    soft = sorted(context & SOFT_CONTEXT)
    env = sorted(context & ENV_CONTEXT)
    news = sorted(context & NEWS_CONTEXT)
    market = sorted(market & MARKET_SOURCES)
    if len(hard) >= 2 and len(market) >= 2:
        tier = "elite_data"
    elif len(hard) >= 1 and len(market) >= 2:
        tier = "strong_market_hard_context"
    elif len(market) >= 2:
        tier = "market_confirmed_soft_context"
    elif len(hard) >= 1:
        tier = "hard_context_single_market"
    else:
        tier = "mapping_only_or_weak"
    return {
        "tier": tier,
        "hard_context_sources": hard,
        "soft_context_sources": soft,
        "environment_sources": env,
        "news_sources": news,
        "market_sources": market,
        "hard_context_count": len(hard),
        "soft_context_count": len(soft),
        "environment_count": len(env),
        "market_source_count": len(market),
    }


def current_provider_status() -> dict[str, Any]:
    payloads = [
        load_json(OUT_DIR / "latest-provider-smoke-diagnostics.json"),
        load_json(OUT_DIR / "latest-provider-smoke-fast.json"),
        load_json(OUT_DIR / "latest-api-full-data-enrichment.json"),
        load_json(OUT_DIR / "latest-sstats-deep-smoke.json"),
    ]
    rows: list[dict[str, Any]] = []
    for payload in payloads:
        rows.extend(collect_provider_results(payload))
    by_provider: dict[str, dict[str, Any]] = {}
    for row in rows:
        provider = str(row.get("provider") or row.get("name") or "unknown").lower()
        if provider.startswith("sstats"):
            provider = "sstats"
        status = str(row.get("status") or row.get("integration_status") or "unknown")
        target = by_provider.setdefault(provider, {"commands": 0, "ok": 0, "rate_limit": 0, "auth": 0, "rows": 0, "event_like": 0, "capabilities": Counter(), "not_ok": []})
        target["commands"] += 1
        if status.lower() in {"ok", "ready", "skipped_preserve_runtime_quota"}:
            target["ok"] += 1
        if status.lower() in {"rate_limit", "429"}:
            target["rate_limit"] += 1
        if status.lower() in {"auth", "401", "403"}:
            target["auth"] += 1
        target["rows"] += int(row.get("rows_count") or row.get("item_count") or row.get("max_rows") or 0)
        target["event_like"] += int(row.get("event_like_rows") or 0)
        for cap in row.get("capabilities") or []:
            target["capabilities"][str(cap)] += 1
        if status.lower() not in {"ok", "ready", "skipped_preserve_runtime_quota"}:
            if len(target["not_ok"]) < 5:
                target["not_ok"].append({"command": row.get("command") or row.get("group") or row.get("role"), "status": status, "reason": row.get("reason") or row.get("error") or row.get("body_preview")})
    serializable = {}
    for provider, item in by_provider.items():
        serializable[provider] = dict(item)
        serializable[provider]["capabilities"] = dict(item["capabilities"])
    return serializable


def current_coverage() -> dict[str, Any]:
    matrix = load_json(OUT_DIR / "provider-smoke-coverage-matrix.json")
    summary = matrix.get("summary") if isinstance(matrix.get("summary"), dict) else {}
    queue = matrix.get("next_enrichment_queue") if isinstance(matrix.get("next_enrichment_queue"), list) else []
    return {
        "summary": summary,
        "queue_top": queue[:20],
        "missing_counter": dict(Counter(m for item in queue if isinstance(item, dict) for m in (item.get("missing") or []))),
    }


def signal_quality_summary() -> dict[str, Any]:
    inventory = load_json(Path(".data/day_inventory/latest.json")) or load_json(Path(".data/day_inventory/current.json"))
    rows = inventory.get("matches") if isinstance(inventory.get("matches"), list) else []
    tiers: Counter[str] = Counter()
    hard_sources: Counter[str] = Counter()
    env_sources: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        sig = classify_match_signals(row)
        tiers[sig["tier"]] += 1
        hard_sources.update(sig["hard_context_sources"])
        env_sources.update(sig["environment_sources"])
        if len(examples) < 12 and sig["tier"] in {"elite_data", "strong_market_hard_context"}:
            examples.append({
                "home_team": row.get("home_team") or row.get("home"),
                "away_team": row.get("away_team") or row.get("away"),
                "kickoff": row.get("kickoff_utc") or row.get("commence_time"),
                "signal_quality": sig,
            })
    return {
        "inventory_rows": len(rows),
        "tier_counts": dict(tiers.most_common()),
        "hard_source_counts": dict(hard_sources.most_common()),
        "environment_source_counts": dict(env_sources.most_common()),
        "examples": examples,
        "note": "weather/news/metadata are support context and never independent price confirmation",
    }


def recommendations(provider_status: dict[str, Any], coverage: dict[str, Any], signal_quality: dict[str, Any]) -> list[str]:
    recs: list[str] = []
    summary = coverage.get("summary") or {}
    tiers = signal_quality.get("tier_counts") if isinstance(signal_quality.get("tier_counts"), dict) else {}
    if int(summary.get("context_2plus_sources") or 0) < 250:
        recs.append("context_2plus is still a bottleneck: spend Bzzoiro + SStats-deep on rows with fewer than two real context families.")
    if int(tiers.get("elite_data") or 0) < 50:
        recs.append("elite_data is low: prioritize hard context families (Bzzoiro/SStats/ClubElo/rating) over pure mapping context.")
    if int(summary.get("weather") or 0) == 0:
        recs.append("weather is 0: build venue/city coordinates from Bzzoiro metadata, TheSportsDB stadium fields and Wikidata cache, then call Open-Meteo only for shortlist or material weather checks.")
    if int(summary.get("news") or 0) == 0:
        recs.append("news is 0: query news only for shortlisted team aliases and keep it as support context, not price confirmation.")
    if provider_status.get("allsportsapi", {}).get("rows", 0) == 0:
        recs.append("AllSportsAPI returns wrapper-only data: first discover accessible free leagues, then call Fixtures scoped by leagueId/countryId instead of broad from/to only.")
    if provider_status.get("football_data", {}).get("rate_limit", 0):
        recs.append("football-data is quota-sensitive: add run-level cooldown after 429 and use cached matches/standings for the rest of the smoke.")
    if provider_status.get("sstats", {}).get("ok", 0):
        recs.append("SStats deep endpoints should stay targeted after SStats id match: glicko, last-games-stats, profits, injuries and odds can add hard context/odds signals.")
    return recs


def render(payload: dict[str, Any]) -> str:
    lines = [
        "# Provider signal coverage blueprint",
        f"UTC: {payload.get('created_at_utc')}",
        "",
        "## Target",
        f"- odds sources per match: >= {TARGETS['odds_sources_min']}",
        f"- context sources per match: >= {TARGETS['context_sources_min']}",
        f"- desired flags: {', '.join(TARGETS['desired_match_flags'])}",
        "",
        "## Current coverage summary",
    ]
    summary = ((payload.get("current_coverage") or {}).get("summary") or {})
    if summary:
        for key in ("total", "odds_any", "odds_2plus_sources", "context_any", "context_2plus_sources", "xg", "form", "weather", "news", "ready_for_model", "publishable_like"):
            if key in summary:
                lines.append(f"- {key}: {summary.get(key)}")
    else:
        lines.append("- no coverage matrix found")
    signal_quality = payload.get("signal_quality") or {}
    if signal_quality:
        lines += ["", "## Hard/soft signal quality"]
        lines.append(f"- inventory_rows: {signal_quality.get('inventory_rows')}")
        for key, value in (signal_quality.get("tier_counts") or {}).items():
            lines.append(f"- {key}: {value}")
        if signal_quality.get("hard_source_counts"):
            lines.append(f"- hard_sources: {signal_quality.get('hard_source_counts')}")
        if signal_quality.get("environment_source_counts"):
            lines.append(f"- environment_sources: {signal_quality.get('environment_source_counts')}")
    lines.append("")
    lines.append("## Provider roles and current status")
    status = payload.get("current_provider_status") or {}
    for provider, meta in sorted(PROVIDER_ROLES.items(), key=lambda kv: (kv[1]["tier"], kv[0])):
        cur = status.get(provider, {})
        lines.append(
            f"- {provider}: tier={meta['tier']} signals={','.join(meta['signals'])} | "
            f"commands={cur.get('commands', 0)} ok={cur.get('ok', 0)} rows={cur.get('rows', 0)} rate_limit={cur.get('rate_limit', 0)} auth={cur.get('auth', 0)}"
        )
    lines.append("")
    lines.append("## Recommendations")
    for rec in payload.get("recommendations") or []:
        lines.append(f"- {rec}")
    lines.append("")
    lines.append("## Next enrichment queue top missing reasons")
    for reason, count in sorted(((payload.get("current_coverage") or {}).get("missing_counter") or {}).items(), key=lambda kv: (-kv[1], kv[0]))[:20]:
        lines.append(f"- {reason}: {count}")
    return "\n".join(lines) + "\n"


def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    provider_status = current_provider_status()
    coverage = current_coverage()
    signal_quality = signal_quality_summary()
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "mode": "provider_signal_coverage_blueprint_v2_hard_soft_signal_split",
        "targets": TARGETS,
        "provider_roles": PROVIDER_ROLES,
        "current_provider_status": provider_status,
        "current_coverage": coverage,
        "signal_quality": signal_quality,
        "recommendations": recommendations(provider_status, coverage, signal_quality),
    }
    text = render(payload)
    for path in (JSON_OUT, LATEST_JSON_OUT):
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    for path in (TXT_OUT, LATEST_TXT_OUT):
        path.write_text(text, encoding="utf-8")
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
