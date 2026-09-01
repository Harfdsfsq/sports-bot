from __future__ import annotations

"""Bound the CPU cost of matching odds-api.io events to the active inventory.

The provider receives up to roughly one thousand events and previously called the
full fuzzy matcher against every inventory row. That O(events * matches) loop
used more than four minutes before the first odds request in run 29576966686.

This startup patch preserves exact/loose matching and uses cheap canonical token
and kickoff prefilters before invoking the original fuzzy scorer. Publication
and matching thresholds remain unchanged.
"""

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.utils import build_loose_match_key, build_match_key, canonicalize_team_name

UTC = timezone.utc
OUT = Path(".data/exports/latest-odds-api-io-fast-match.json")
ART = Path("artifacts/run-bot/latest-odds-api-io-fast-match.json")


def _tokens(value: Any) -> frozenset[str]:
    return frozenset(part for part in canonicalize_team_name(str(value or "")).split() if part)


def _write(payload: dict[str, Any]) -> None:
    for path in (OUT, ART):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(path.suffix + ".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            tmp.replace(path)
        except Exception:
            pass


def install() -> dict[str, Any]:
    try:
        from app.providers import odds_api_io
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}

    cls = getattr(odds_api_io, "OddsApiIoProvider", None)
    if cls is None:
        return {"status": "provider_class_missing"}
    if getattr(cls, "_harizon_fast_match_patched", False):
        return {"status": "already_patched"}

    original_match = getattr(cls, "_match_event", None)
    original_fetch_offers = getattr(cls, "fetch_offers", None)
    if not callable(original_match) or not callable(original_fetch_offers):
        return {"status": "provider_methods_missing"}

    def _build_cache(matches: list[Any]) -> dict[str, Any]:
        exact: dict[str, list[Any]] = defaultdict(list)
        loose: dict[str, list[Any]] = defaultdict(list)
        records: list[tuple[Any, frozenset[str], frozenset[str], datetime]] = []
        for match in matches:
            try:
                exact[build_match_key(match.sport_key, match.home_team, match.away_team, match.commence_time)].append(match)
                loose[build_loose_match_key(match.sport_key, match.home_team, match.away_team)].append(match)
                records.append((match, _tokens(match.home_team), _tokens(match.away_team), match.commence_time.astimezone(UTC)))
            except Exception:
                continue
        return {"matches_id": id(matches), "matches_len": len(matches), "exact": exact, "loose": loose, "records": records}

    def fast_match(self: Any, event: dict[str, Any], matches: list[Any]):
        stats = getattr(self, "_harizon_fast_match_stats", None)
        if not isinstance(stats, dict):
            stats = {"calls": 0, "exact_shortlists": 0, "loose_shortlists": 0, "fuzzy_shortlists": 0, "no_shortlist": 0, "original_candidates": 0, "shortlist_candidates": 0, "max_shortlist": 0}
            self._harizon_fast_match_stats = stats
        stats["calls"] += 1

        cache = getattr(self, "_harizon_fast_match_cache", None)
        if not isinstance(cache, dict) or cache.get("matches_id") != id(matches) or cache.get("matches_len") != len(matches):
            cache = _build_cache(matches)
            self._harizon_fast_match_cache = cache

        sport = "soccer"
        event_home = str(event.get("home") or "")
        event_away = str(event.get("away") or "")
        event_start = event.get("commence_time")
        if not isinstance(event_start, datetime):
            return original_match(self, event, matches)

        exact_key = build_match_key(sport, event_home, event_away, event_start)
        candidates = list(cache["exact"].get(exact_key) or [])
        if candidates:
            stats["exact_shortlists"] += 1
        else:
            loose_key = build_loose_match_key(sport, event_home, event_away)
            candidates = list(cache["loose"].get(loose_key) or [])
            if candidates:
                stats["loose_shortlists"] += 1

        if not candidates:
            event_home_tokens = _tokens(event_home)
            event_away_tokens = _tokens(event_away)
            fuzzy_tol = float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8)
            ranked: list[tuple[int, float, Any]] = []
            event_start_utc = event_start.astimezone(UTC)
            for match, home_tokens, away_tokens, match_start in cache["records"]:
                diff = abs((match_start - event_start_utc).total_seconds()) / 3600.0
                if diff > fuzzy_tol:
                    continue
                direct = len(home_tokens & event_home_tokens) + len(away_tokens & event_away_tokens)
                reverse = len(home_tokens & event_away_tokens) + len(away_tokens & event_home_tokens)
                overlap = max(direct, reverse)
                if overlap <= 0:
                    continue
                ranked.append((overlap, -diff, match))
            ranked.sort(key=lambda row: (row[0], row[1]), reverse=True)
            candidates = [row[2] for row in ranked[:24]]
            if candidates:
                stats["fuzzy_shortlists"] += 1
            else:
                stats["no_shortlist"] += 1
                return None

        stats["original_candidates"] += len(matches)
        stats["shortlist_candidates"] += len(candidates)
        stats["max_shortlist"] = max(int(stats.get("max_shortlist") or 0), len(candidates))
        return original_match(self, event, candidates)

    async def fetch_offers_fast(self: Any, matches: list[Any]):
        self._harizon_fast_match_cache = None
        self._harizon_fast_match_stats = {"calls": 0, "exact_shortlists": 0, "loose_shortlists": 0, "fuzzy_shortlists": 0, "no_shortlist": 0, "original_candidates": 0, "shortlist_candidates": 0, "max_shortlist": 0}
        started = time.perf_counter()
        result = await original_fetch_offers(self, matches)
        elapsed = round(time.perf_counter() - started, 3)
        stats = dict(getattr(self, "_harizon_fast_match_stats", {}) or {})
        original_total = int(stats.get("original_candidates") or 0)
        shortlist_total = int(stats.get("shortlist_candidates") or 0)
        stats.update({
            "status": "ok",
            "created_at_utc": datetime.now(UTC).isoformat(),
            "elapsed_seconds": elapsed,
            "inventory_matches": len(matches or []),
            "candidate_reduction_pct": round((1.0 - shortlist_total / original_total) * 100.0, 2) if original_total else 0.0,
            "publication_contract_relaxed": False,
        })
        _write(stats)
        return result

    cls._match_event = fast_match
    cls.fetch_offers = fetch_offers_fast
    cls._harizon_fast_match_patched = True
    _write({"status": "installed", "created_at_utc": datetime.now(UTC).isoformat(), "publication_contract_relaxed": False})
    return {"status": "installed"}


__all__ = ["install"]
