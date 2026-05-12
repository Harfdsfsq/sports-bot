from __future__ import annotations

"""Final core-source contract for progressive coverage.

The betting bot has three primary APIs:
- odds_api_io: primary live odds/current lines;
- bzzoiro: secondary odds + context;
- sstats: statistical context and core historical/model line signal.

Supplemental providers may still be queried, but they must not hide gaps in the
primary coverage plan. A match is considered core-ready only when it has:
- 2+ core line/odds sources from {odds_api_io, bzzoiro, sstats};
- 2+ core context sources from {sstats, bzzoiro}.
"""

import atexit
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
DAY_INV_DIR = ROOT / ".data" / "day_inventory"
REPORT_PATH = EXPORT_DIR / "latest-progressive-core-sources-finalizer.json"

CORE_PROVIDERS = {"odds_api_io", "sstats", "bzzoiro"}
CORE_ODDS_PROVIDERS = {"odds_api_io", "sstats", "bzzoiro"}
CORE_CONTEXT_PROVIDERS = {"sstats", "bzzoiro"}
SUPPLEMENTAL_ODDS_PROVIDERS = {"sportlogic", "allsportsapi", "oddspapi", "bookies_api"}
SUPPLEMENTAL_CONTEXT_PROVIDERS = {"api_football", "espn", "thesportsdb", "football_data", "openligadb", "futrixmetrics", "openfootball", "newsapi", "gnews", "sportlogic", "weather", "self_history"}


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _now() -> datetime:
    return datetime.now(UTC)


def _install_patch() -> dict[str, Any]:
    from app.services import progressive_coverage_runtime_patch as p

    p.CORE_PROVIDERS = set(CORE_PROVIDERS)
    p.ODDS_PROVIDERS = set(CORE_ODDS_PROVIDERS | SUPPLEMENTAL_ODDS_PROVIDERS)
    p.CONTEXT_PROVIDERS = set(CORE_CONTEXT_PROVIDERS | SUPPLEMENTAL_CONTEXT_PROVIDERS)

    def core_tokens(row: dict[str, Any], field: str, allowed: set[str]) -> set[str]:
        return set(p._provider_tokens(row.get(field))) & set(allowed)

    def all_tokens(row: dict[str, Any], field: str) -> set[str]:
        return set(p._provider_tokens(row.get(field)))

    def sources_from_inventory_match_core(match: Any) -> tuple[set[str], set[str]]:
        meta = p._match_meta(match)
        source_ids = meta.get("provider_source_ids") if isinstance(meta.get("provider_source_ids"), dict) else {}
        sources_seen = p._provider_tokens(meta.get("sources_seen")) | p._provider_tokens(source_ids)
        if isinstance(match, dict):
            sources_seen |= p._provider_tokens(match.get("sources_seen")) | p._provider_tokens(match.get("source_ids"))
            coverage = match.get("coverage") if isinstance(match.get("coverage"), dict) else {}
        else:
            sources_seen.add(str(getattr(match, "source", "") or "").strip().lower())
            coverage = {}
        odds_sources: set[str] = set()
        context_sources: set[str] = set()
        # User contract: odds_api_io + bzzoiro + sstats are all primary line/odds
        # sources. SStats may arrive through context/list endpoints, but its
        # matched source id still counts as a core line/model signal source.
        odds_sources |= sources_seen & CORE_ODDS_PROVIDERS
        if coverage.get("odds"):
            odds_sources.add("odds_api_io")
        context_sources |= sources_seen & CORE_CONTEXT_PROVIDERS
        if meta.get("bzzoiro_has_context_hint"):
            context_sources.add("bzzoiro")
            odds_sources.add("bzzoiro")
        if meta.get("sstats_has_context_hint"):
            context_sources.add("sstats")
            odds_sources.add("sstats")
        return odds_sources, context_sources

    def coverage_counts_core(row: dict[str, Any]) -> tuple[int, int]:
        return len(core_tokens(row, "odds_sources", CORE_ODDS_PROVIDERS)), len(core_tokens(row, "context_sources", CORE_CONTEXT_PROVIDERS))

    def priority_for_core(match: Any, row: dict[str, Any], provider: str, method_name: str, now: datetime) -> float:
        min_odds = max(1, _to_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_ODDS_SOURCES") or 2, 2))
        min_context = max(1, _to_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_CONTEXT_SOURCES") or 2, 2))
        core_odds = core_tokens(row, "odds_sources", CORE_ODDS_PROVIDERS)
        core_context = core_tokens(row, "context_sources", CORE_CONTEXT_PROVIDERS)
        all_odds = all_tokens(row, "odds_sources")
        all_context = all_tokens(row, "context_sources")
        odds_count = len(core_odds)
        context_count = len(core_context)
        window, hours = p._window_score(match, now)
        provider = str(provider or "").lower()
        method_name = str(method_name or "").lower()
        score = float(window)

        if method_name == "fetch_offers":
            deficit = max(0, min_odds - odds_count)
            score += deficit * 110
            if provider in CORE_ODDS_PROVIDERS:
                score += 70
                if provider not in core_odds:
                    score += 55
                else:
                    score -= 35
            elif provider in SUPPLEMENTAL_ODDS_PROVIDERS:
                # Supplemental lines are useful only after the core providers
                # have been attempted or when the match is very urgent.
                score += 10
                if odds_count < min_odds:
                    score -= 20
                if provider not in all_odds:
                    score += 8
            else:
                score -= 40
            if provider == "odds_api_io" and "odds_api_io" not in core_odds:
                score += 40
            if provider == "bzzoiro" and "bzzoiro" not in core_odds:
                score += 65
            if provider == "sstats" and "sstats" not in core_odds:
                score += 45
        elif method_name == "fetch_context":
            deficit = max(0, min_context - context_count)
            score += deficit * 105
            if provider in CORE_CONTEXT_PROVIDERS:
                score += 70
                if provider not in core_context:
                    score += 55
                else:
                    score -= 30
            elif provider in SUPPLEMENTAL_CONTEXT_PROVIDERS:
                score += 8
                if context_count < min_context:
                    score -= 18
                if provider not in all_context:
                    score += 6
            else:
                score -= 35
            # Context calls from SStats/Bzzoiro also satisfy core odds-source
            # coverage by contract, so prioritize them when core odds are thin.
            if provider in CORE_ODDS_PROVIDERS and provider not in core_odds:
                score += 45
            if provider == "sstats" and "sstats" not in core_context:
                score += 45
            if provider == "bzzoiro" and ("bzzoiro" not in core_context or "bzzoiro" not in core_odds):
                score += 65

        score += p._stale_bonus(row, provider, now)
        if 0 <= hours <= 2.5:
            score += 45
        elif 0 <= hours <= 4:
            score += 28
        row["coverage_priority_last"] = round(score, 3)
        row["coverage_gap"] = {
            "core_odds_sources": odds_count,
            "core_context_sources": context_count,
            "all_odds_sources": len(all_odds),
            "all_context_sources": len(all_context),
            "core_odds_needed": max(0, min_odds - odds_count),
            "core_context_needed": max(0, min_context - context_count),
            # Backward compatible names now mean core gaps.
            "odds_sources": odds_count,
            "context_sources": context_count,
            "odds_needed": max(0, min_odds - odds_count),
            "context_needed": max(0, min_context - context_count),
            "core_contract": "odds_api_io+bzzoiro+sstats lines; sstats+bzzoiro context",
        }
        return score

    def iter_map_keys(data: Any) -> Iterable[tuple[str, Any]]:
        if isinstance(data, dict):
            for key, value in data.items():
                yield str(key), value

    def value_has_data(value: Any) -> bool:
        if value in (None, "", [], {}):
            return False
        if isinstance(value, (list, tuple, set, dict)):
            return len(value) > 0
        return True

    def record_provider_success_core(data: Any, provider: str, method_name: str, stats: Any | None = None) -> None:
        provider = str(provider or "unknown").lower()
        method_name = str(method_name or "").lower()
        if method_name not in {"fetch_offers", "fetch_context"}:
            return
        state = p._load_state()
        now = _now().isoformat()
        successes = 0
        for key, value in iter_map_keys(data):
            if not key or not value_has_data(value):
                continue
            row = state.setdefault("matches", {}).setdefault(key, {"match_key": key})
            odds_sources = set(p._provider_tokens(row.get("odds_sources")))
            context_sources = set(p._provider_tokens(row.get("context_sources")))
            if method_name == "fetch_offers":
                if provider in CORE_ODDS_PROVIDERS or provider in SUPPLEMENTAL_ODDS_PROVIDERS:
                    odds_sources.add(provider)
                if isinstance(value, list):
                    for item in value[:200]:
                        src = getattr(item, "source", None) if not isinstance(item, dict) else item.get("source")
                        if src:
                            odds_sources.add(str(src).strip().lower())
            elif method_name == "fetch_context":
                if provider in CORE_CONTEXT_PROVIDERS or provider in SUPPLEMENTAL_CONTEXT_PROVIDERS:
                    context_sources.add(provider)
                # User contract: successful SStats/Bzzoiro context coverage also
                # counts as a core line/odds source for coverage planning.
                if provider in CORE_ODDS_PROVIDERS:
                    odds_sources.add(provider)
                if isinstance(value, list):
                    for item in value[:50]:
                        src = getattr(item, "source", None) if not isinstance(item, dict) else item.get("source")
                        if src:
                            src_l = str(src).strip().lower()
                            if src_l in CORE_CONTEXT_PROVIDERS or src_l in SUPPLEMENTAL_CONTEXT_PROVIDERS:
                                context_sources.add(src_l)
                            if src_l in CORE_ODDS_PROVIDERS or src_l in SUPPLEMENTAL_ODDS_PROVIDERS:
                                odds_sources.add(src_l)
                else:
                    src = getattr(value, "source", None) if not isinstance(value, dict) else value.get("source")
                    if src:
                        src_l = str(src).strip().lower()
                        if src_l in CORE_CONTEXT_PROVIDERS or src_l in SUPPLEMENTAL_CONTEXT_PROVIDERS:
                            context_sources.add(src_l)
                        if src_l in CORE_ODDS_PROVIDERS or src_l in SUPPLEMENTAL_ODDS_PROVIDERS:
                            odds_sources.add(src_l)
            row["odds_sources"] = sorted(odds_sources)
            row["context_sources"] = sorted(context_sources)
            row.setdefault("last_success_utc_by_provider", {})[provider] = now
            row["last_success_method"] = method_name
            odds_count, context_count = coverage_counts_core(row)
            row["coverage_gap"] = {
                "core_odds_sources": odds_count,
                "core_context_sources": context_count,
                "all_odds_sources": len(odds_sources),
                "all_context_sources": len(context_sources),
                "core_odds_needed": max(0, _to_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_ODDS_SOURCES") or 2, 2) - odds_count),
                "core_context_needed": max(0, _to_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_CONTEXT_SOURCES") or 2, 2) - context_count),
                "odds_sources": odds_count,
                "context_sources": context_count,
                "odds_needed": max(0, _to_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_ODDS_SOURCES") or 2, 2) - odds_count),
                "context_needed": max(0, _to_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_CONTEXT_SOURCES") or 2, 2) - context_count),
                "core_contract": "odds_api_io+bzzoiro+sstats lines; sstats+bzzoiro context",
            }
            successes += 1
        state.setdefault("runs", []).append({
            "created_at_utc": now,
            "provider": provider,
            "method": method_name,
            "matches_with_data": successes,
            "stats": {k: v for k, v in dict(stats or {}).items() if k not in {"last_body_preview"}} if isinstance(stats, dict) else {},
            "core_contract": "odds_api_io+bzzoiro+sstats lines; sstats+bzzoiro context",
        })
        state["runs"] = state.get("runs", [])[-120:]
        p._save_state(state)
        write_plan_report_core()

    def write_plan_report_core() -> None:
        state = p._load_state()
        matches = state.get("matches") if isinstance(state.get("matches"), dict) else {}
        min_odds = _to_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_ODDS_SOURCES") or 2, 2)
        min_context = _to_int(os.getenv("PROGRESSIVE_COVERAGE_MIN_CONTEXT_SOURCES") or 2, 2)
        counts = Counter()
        gap_rows = []
        now = _now()
        for key, row in matches.items():
            if not isinstance(row, dict):
                continue
            core_odds = core_tokens(row, "odds_sources", CORE_ODDS_PROVIDERS)
            core_context = core_tokens(row, "context_sources", CORE_CONTEXT_PROVIDERS)
            all_odds = all_tokens(row, "odds_sources")
            all_context = all_tokens(row, "context_sources")
            odds_count = len(core_odds)
            context_count = len(core_context)
            counts["matches_tracked"] += 1
            counts["core_odds_1plus"] += int(odds_count >= 1)
            counts["core_odds_2plus"] += int(odds_count >= min_odds)
            counts["core_context_1plus"] += int(context_count >= 1)
            counts["core_context_2plus"] += int(context_count >= min_context)
            counts["core_ready_2plus_both"] += int(odds_count >= min_odds and context_count >= min_context)
            counts["all_odds_1plus"] += int(len(all_odds) >= 1)
            counts["all_odds_2plus"] += int(len(all_odds) >= min_odds)
            counts["all_context_1plus"] += int(len(all_context) >= 1)
            counts["all_context_2plus"] += int(len(all_context) >= min_context)
            # Backward-compatible names now intentionally point to core coverage.
            counts["odds_1plus"] += int(odds_count >= 1)
            counts["odds_2plus"] += int(odds_count >= min_odds)
            counts["context_1plus"] += int(context_count >= 1)
            counts["context_2plus"] += int(context_count >= min_context)
            counts["ready_2plus_both"] += int(odds_count >= min_odds and context_count >= min_context)
            kickoff = p._parse_dt(row.get("kickoff_utc"))
            hours = (kickoff - now).total_seconds() / 3600.0 if kickoff else None
            if hours is not None and 0 <= hours <= 4:
                counts["window_0_4h"] += 1
                counts["window_0_4h_core_ready_2plus_both"] += int(odds_count >= min_odds and context_count >= min_context)
                counts["window_0_4h_ready_2plus_both"] += int(odds_count >= min_odds and context_count >= min_context)
            if hours is not None and 0 <= hours <= 12:
                counts["window_0_12h"] += 1
                counts["window_0_12h_core_ready_2plus_both"] += int(odds_count >= min_odds and context_count >= min_context)
                counts["window_0_12h_ready_2plus_both"] += int(odds_count >= min_odds and context_count >= min_context)
            if odds_count < min_odds or context_count < min_context:
                gap_rows.append({
                    "match_key": key,
                    "home_team": row.get("home_team"),
                    "away_team": row.get("away_team"),
                    "kickoff_utc": row.get("kickoff_utc"),
                    "core_odds_sources": sorted(core_odds),
                    "core_context_sources": sorted(core_context),
                    "supplemental_odds_sources": sorted(all_odds - core_odds),
                    "supplemental_context_sources": sorted(all_context - core_context),
                    "core_odds_needed": max(0, min_odds - odds_count),
                    "core_context_needed": max(0, min_context - context_count),
                    "hours_to_kickoff": round(hours, 2) if hours is not None else None,
                })
        gap_rows.sort(key=lambda row: (row.get("hours_to_kickoff") is None, row.get("hours_to_kickoff") or 999999, -row.get("core_odds_needed", 0), -row.get("core_context_needed", 0)))
        report = {
            "created_at_utc": now.isoformat(),
            "enabled": p._truthy(os.getenv("PROGRESSIVE_COVERAGE_ENABLED"), True),
            "contract": {
                "core_providers": sorted(CORE_PROVIDERS),
                "core_odds_providers": sorted(CORE_ODDS_PROVIDERS),
                "core_context_providers": sorted(CORE_CONTEXT_PROVIDERS),
                "supplemental_odds_providers": sorted(SUPPLEMENTAL_ODDS_PROVIDERS),
                "supplemental_context_providers": sorted(SUPPLEMENTAL_CONTEXT_PROVIDERS),
            },
            "min_core_odds_sources": min_odds,
            "min_core_context_sources": min_context,
            "counts": dict(counts),
            "core_gap_sample": gap_rows[:80],
            "gap_sample": gap_rows[:80],
            "runtime_events": p._RUNTIME_EVENTS[-40:],
            "state_path": str(p.STATE_PATH),
        }
        p._write_json(p.PLAN_PATH, report)

    def sync_inventory_rows_core() -> None:
        state = p._load_state()
        matches = state.get("matches") if isinstance(state.get("matches"), dict) else {}
        if not matches:
            return
        for path in [p.DAY_INV_DIR / "latest.json", p.DAY_INV_DIR / "current.json", p.DAY_INV_DIR / "today.json", p.DAY_INV_DIR / f"{state.get('date_local')}.json"]:
            payload = p._read_json(path, None)
            if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
                continue
            changed = False
            for row in payload["matches"]:
                if not isinstance(row, dict):
                    continue
                key = str(row.get("match_key") or "")
                st = matches.get(key)
                if not isinstance(st, dict):
                    continue
                all_odds = all_tokens(st, "odds_sources")
                all_context = all_tokens(st, "context_sources")
                core_odds = all_odds & CORE_ODDS_PROVIDERS
                core_context = all_context & CORE_CONTEXT_PROVIDERS
                coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
                coverage.update({
                    "odds": len(core_odds) >= 1,
                    "context": len(core_context) >= 1,
                    "core_odds_sources": sorted(core_odds),
                    "core_context_sources": sorted(core_context),
                    "supplemental_odds_sources": sorted(all_odds - core_odds),
                    "supplemental_context_sources": sorted(all_context - core_context),
                    "odds_sources": sorted(core_odds),
                    "context_sources": sorted(core_context),
                    "all_odds_sources": sorted(all_odds),
                    "all_context_sources": sorted(all_context),
                    "odds_sources_count": len(core_odds),
                    "context_sources_count": len(core_context),
                    "all_odds_sources_count": len(all_odds),
                    "all_context_sources_count": len(all_context),
                    "ready_for_model": len(core_odds) >= 1 and len(core_context) >= 1,
                    "ready_for_publish_coverage": len(core_odds) >= 2 and len(core_context) >= 2,
                    "core_contract": "odds_api_io+bzzoiro+sstats lines; sstats+bzzoiro context",
                })
                row["coverage"] = coverage
                row["progressive_coverage"] = {
                    "core_odds_sources": sorted(core_odds),
                    "core_context_sources": sorted(core_context),
                    "supplemental_odds_sources": sorted(all_odds - core_odds),
                    "supplemental_context_sources": sorted(all_context - core_context),
                    "all_odds_sources": sorted(all_odds),
                    "all_context_sources": sorted(all_context),
                    "coverage_gap": st.get("coverage_gap") or {},
                    "last_success_utc_by_provider": st.get("last_success_utc_by_provider") or {},
                    "provider_attempts": st.get("provider_attempts") or {},
                }
                changed = True
            if changed:
                payload["progressive_coverage_updated_at_utc"] = _now().isoformat()
                p._write_json(path, payload)

    p._sources_from_inventory_match = sources_from_inventory_match_core
    p._coverage_counts = coverage_counts_core
    p._priority_for = priority_for_core
    p._record_provider_success = record_provider_success_core
    p._write_plan_report = write_plan_report_core
    p._sync_inventory_rows_from_state = sync_inventory_rows_core
    atexit.register(sync_inventory_rows_core)
    atexit.register(write_plan_report_core)
    write_plan_report_core()
    return {
        "status": "installed",
        "core_odds_providers": sorted(CORE_ODDS_PROVIDERS),
        "core_context_providers": sorted(CORE_CONTEXT_PROVIDERS),
        "supplemental_odds_providers": sorted(SUPPLEMENTAL_ODDS_PROVIDERS),
        "supplemental_context_providers": sorted(SUPPLEMENTAL_CONTEXT_PROVIDERS),
    }


def install() -> dict[str, Any]:
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "starting",
        "contract": "core sources only for 2plus coverage targets",
    }
    try:
        payload.update(_install_patch())
    except Exception as exc:
        payload["status"] = "error"
        payload["error"] = f"{type(exc).__name__}: {exc}"
    _write_json(REPORT_PATH, payload)
    return payload
