from __future__ import annotations

"""Safe + strict wrapper for core day inventory builder.

v2 fixed the late Counter crash. The next successful run showed the crosswalk was
still too soft: unrelated same-day matches could be merged by a fuzzy score. This
wrapper replaces only the merge and coverage-counter stages:

- provider rows merge only when both teams match strongly, direct or swapped;
- kickoff distance is capped;
- weak fuzzy-only merges become separate canonical rows;
- next-6h/12h ready counters are recalculated after coverage enrichment.
"""

import json
import math
import re
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

UTC = timezone.utc
EXPORT_DIR = Path(".data/exports")
STRICT_REPORT_PATH = EXPORT_DIR / "latest-day-inventory-core-strict-merge-report.json"


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(value or "").lower()).strip()


def _compact(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _tokens(value: Any) -> set[str]:
    return {x for x in _norm(value).split() if len(x) >= 2 and x not in {"fc", "cf", "sc", "fk", "ac", "club", "the", "u21", "u23"}}


def _similarity(a: Any, b: Any) -> float:
    aa = _compact(a)
    bb = _compact(b)
    if not aa or not bb:
        return 0.0
    if aa == bb:
        return 1.0
    if aa in bb or bb in aa:
        shorter = min(len(aa), len(bb))
        longer = max(len(aa), len(bb))
        return max(0.82, shorter / max(1, longer))
    ta = _tokens(a)
    tb = _tokens(b)
    jacc = len(ta & tb) / max(1, len(ta | tb)) if ta or tb else 0.0
    seq = SequenceMatcher(None, aa, bb).ratio()
    return max(seq, jacc)


def _team_pair_score(existing: Any, incoming: Any) -> dict[str, Any]:
    direct_home = _similarity(existing.home_team, incoming.home_team)
    direct_away = _similarity(existing.away_team, incoming.away_team)
    swap_home = _similarity(existing.home_team, incoming.away_team)
    swap_away = _similarity(existing.away_team, incoming.home_team)
    direct_min = min(direct_home, direct_away)
    swap_min = min(swap_home, swap_away)
    if swap_min > direct_min + 0.08:
        return {"orientation": "swapped", "min": swap_min, "avg": (swap_home + swap_away) / 2.0, "home_score": swap_home, "away_score": swap_away}
    return {"orientation": "direct", "min": direct_min, "avg": (direct_home + direct_away) / 2.0, "home_score": direct_home, "away_score": direct_away}


def _hours_diff(a: datetime, b: datetime) -> float:
    try:
        return abs((a.astimezone(UTC) - b.astimezone(UTC)).total_seconds()) / 3600.0
    except Exception:
        return 9999.0


def _source_set(match: Any) -> set[str]:
    meta = dict(getattr(match, "metadata", {}) or {})
    ids = dict(meta.get("provider_source_ids") or {})
    sources = {str(x).strip() for x in str(meta.get("sources_seen") or getattr(match, "source", "")).split(",") if str(x).strip()}
    sources.update(str(k) for k in ids.keys() if str(k).strip())
    if getattr(match, "source", None):
        sources.add(str(match.source))
    return sources


def _source_ids(match: Any) -> dict[str, str]:
    meta = dict(getattr(match, "metadata", {}) or {})
    ids = dict(meta.get("provider_source_ids") or {})
    if getattr(match, "source", None) and getattr(match, "source_event_id", None):
        ids[str(match.source)] = str(match.source_event_id)
    return {str(k): str(v) for k, v in ids.items() if str(k).strip() and str(v).strip()}


def install_strict_merge(core: Any) -> None:
    def strict_merge_matches(matches_by_provider: dict[str, list[Any]], settings: Any):
        canonical: dict[str, Any] = {}
        crosswalk: dict[str, Any] = {
            "matched_rows": [],
            "unmatched_rows": [],
            "blocked_rows": [],
            "provider_rows": {k: len(v) for k, v in matches_by_provider.items()},
            "policy": {
                "min_team_similarity": 0.72,
                "min_avg_similarity": 0.80,
                "max_kickoff_diff_hours": 6.0,
                "swapped_allowed": True,
            },
        }
        ordered = ["odds_api_io", "bzzoiro", "sstats", "sportlogic"]
        for provider in ordered:
            for match in matches_by_provider.get(provider, []):
                best_key = None
                best_decision = None
                best_rank = -1.0
                for key, existing in canonical.items():
                    diff_h = _hours_diff(existing.commence_time, match.commence_time)
                    pair = _team_pair_score(existing, match)
                    rank = pair["avg"] * 100.0 - diff_h
                    if rank > best_rank:
                        best_rank = rank
                        best_key = key
                        best_decision = {"kickoff_diff_hours": round(diff_h, 3), **pair}
                allowed = False
                if best_key is not None and isinstance(best_decision, dict):
                    allowed = (
                        best_decision["kickoff_diff_hours"] <= 6.0
                        and best_decision["min"] >= 0.72
                        and best_decision["avg"] >= 0.80
                    )
                if not allowed:
                    canonical[match.match_key] = match
                    row = {"provider": provider, "match_key": match.match_key, "home": match.home_team, "away": match.away_team, "reason": "new_canonical"}
                    if best_key and best_decision:
                        row.update({"best_candidate": best_key, "blocked_decision": best_decision})
                        crosswalk["blocked_rows"].append(row)
                    else:
                        crosswalk["unmatched_rows"].append(row)
                    continue

                existing = canonical[best_key]
                meta = dict(existing.metadata or {})
                incoming = dict(match.metadata or {})
                source_ids = dict(meta.get("provider_source_ids") or {})
                source_ids.update(incoming.get("provider_source_ids") or {})
                if match.source_event_id:
                    source_ids[provider] = str(match.source_event_id)
                sources_seen = _source_set(existing) | _source_set(match) | {provider}
                merge_audit = list(meta.get("core_merge_audit") or [])
                merge_audit.append({"provider": provider, "provider_match_key": match.match_key, "decision": best_decision})
                meta.update(incoming)
                meta["provider_source_ids"] = source_ids
                meta["sources_seen"] = ",".join(sorted(sources_seen))
                meta["core_inventory"] = True
                meta["core_merge_audit"] = merge_audit[-20:]
                canonical[best_key] = core.Match(**{**asdict(existing), "metadata": meta})
                crosswalk["matched_rows"].append({
                    "provider": provider,
                    "provider_match_key": match.match_key,
                    "canonical_match_key": best_key,
                    "team_similarity_min": round(float(best_decision["min"]), 3),
                    "team_similarity_avg": round(float(best_decision["avg"]), 3),
                    "orientation": best_decision["orientation"],
                    "kickoff_diff_hours": best_decision["kickoff_diff_hours"],
                })
        return list(canonical.values()), crosswalk

    core.merge_matches = strict_merge_matches


def _parse_dt(value: Any) -> datetime | None:
    try:
        if value in (None, ""):
            return None
        text = str(value)
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def install_strict_enrich(core: Any) -> None:
    def strict_enrich_payload_coverage(payload: dict[str, Any]) -> dict[str, Any]:
        matches = payload.get("matches") if isinstance(payload.get("matches"), list) else []
        counts = Counter()
        now = datetime.now(UTC)
        for row in matches:
            if not isinstance(row, dict):
                continue
            sources = set(row.get("sources_seen") if isinstance(row.get("sources_seen"), list) else [])
            if isinstance(row.get("source_ids"), dict):
                sources.update(str(k) for k in row["source_ids"].keys())
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            if isinstance(meta.get("provider_source_ids"), dict):
                sources.update(str(k) for k in meta["provider_source_ids"].keys())
            if isinstance(meta.get("sources_seen"), str):
                sources.update(x for x in meta["sources_seen"].split(",") if x)
            coverage = dict(row.get("coverage") or {})
            has_odds = bool({"odds_api_io", "sportlogic"} & sources) or bool(meta.get("has_current_odds_provider"))
            has_context = bool({"sstats", "bzzoiro"} & sources) or bool(meta.get("bzzoiro_has_context_hint") or meta.get("sstats_has_context_hint"))
            has_xg = bool(meta.get("bzzoiro_context_fields") or meta.get("sstats_context_fields"))
            has_form = "sstats" in sources
            ready = bool(has_odds and has_context)
            coverage.update({
                "fixture_core": True,
                "odds": has_odds,
                "context": has_context,
                "xg": has_xg,
                "form": has_form,
                "ready_for_model": ready,
                "ready_for_publish": False,
            })
            row["coverage"] = coverage
            row["sources_seen"] = sorted(x for x in sources if x)
            row["priority"] = max(_as_float(row.get("priority")), float(len(sources) * 20 + (30 if has_odds else 0) + (20 if has_context else 0)))
            counts["matches_with_odds"] += int(has_odds)
            counts["matches_with_context"] += int(has_context)
            counts["matches_with_xg"] += int(has_xg)
            counts["matches_with_form"] += int(has_form)
            counts["matches_ready_for_model"] += int(ready)
            if len(sources & set(core.CORE_PROVIDERS)) >= 2:
                counts["matches_with_2plus_core_fixture_sources"] += 1
            if len(sources & set(core.CORE_PROVIDERS)) >= 3:
                counts["matches_with_3_core_fixture_sources"] += 1
            kickoff = _parse_dt(row.get("kickoff_utc"))
            if kickoff is not None:
                hours = (kickoff - now).total_seconds() / 3600.0
                if 0 <= hours <= 6:
                    counts["matches_next_6h"] += 1
                    counts["matches_next_6h_ready"] += int(ready)
                if 0 <= hours <= 12:
                    counts["matches_next_12h"] += 1
                    counts["matches_next_12h_ready"] += int(ready)
        replace_keys = [
            "matches_with_odds", "matches_with_context", "matches_with_xg", "matches_with_form",
            "matches_ready_for_model", "matches_with_2plus_core_fixture_sources", "matches_with_3_core_fixture_sources",
            "matches_next_6h", "matches_next_6h_ready", "matches_next_12h", "matches_next_12h_ready",
        ]
        payload.setdefault("counts", {})
        for key in replace_keys:
            payload["counts"][key] = int(counts.get(key, 0))
        payload["matches"] = matches
        return payload

    core.enrich_payload_coverage = strict_enrich_payload_coverage


def _as_float(value: Any) -> float:
    try:
        number = float(value)
        if math.isfinite(number):
            return number
    except Exception:
        pass
    return 0.0


def main() -> int:
    import scripts.build_day_inventory_core as core

    core.Counter = Counter
    install_strict_merge(core)
    install_strict_enrich(core)
    try:
        code = int(core.main() or 0)
    finally:
        try:
            STRICT_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
            STRICT_REPORT_PATH.write_text(json.dumps({
                "created_at_utc": datetime.now(UTC).isoformat(),
                "status": "installed_and_executed",
                "strict_merge": True,
                "strict_next_window_ready_counters": True,
            }, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        except Exception:
            pass
    return code


if __name__ == "__main__":
    raise SystemExit(main())
