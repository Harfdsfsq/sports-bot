from __future__ import annotations

"""Bzzoiro context-gap finalizer.

After v8 reporting, the real bottleneck is not core line coverage anymore:
SStats now counts as a core line source. The weak metric is core context 2+,
which means matches often have SStats context but no Bzzoiro context.

This finalizer wraps BzzoiroContextProvider.fetch_context and performs an extra
Bzzoiro v2 pass only for upcoming matches whose progressive plan says
`core_context_needed > 0`. It does not relax publication guards; it only tries to
fill missing Bzzoiro context/odds hints for the nearest windows.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

from app.schemas import Match, MatchContext

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
PLAN_PATH = EXPORT_DIR / "latest-progressive-coverage-plan.json"
REPORT_PATH = EXPORT_DIR / "latest-bzzoiro-context-gap-finalizer.json"


def _write_json(path: Path, payload: Any) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if not path.exists() or path.stat().st_size <= 0:
            return {}
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _truthy(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value)))
    except Exception:
        return default


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(str(value).replace(",", "."))
        if number == number and abs(number) < 1e9:
            return number
    except Exception:
        return None
    return None


def _match_key(match: Match) -> str:
    try:
        return str(match.match_key or "")
    except Exception:
        return ""


def _gap_keys() -> set[str]:
    plan = _read_json(PLAN_PATH)
    rows = plan.get("core_gap_sample") or plan.get("gap_sample") or []
    keys: set[str] = set()
    if not isinstance(rows, list):
        return keys
    for row in rows:
        if not isinstance(row, dict):
            continue
        if _to_float(row.get("hours_to_kickoff")) is not None and _to_float(row.get("hours_to_kickoff")) < 0:
            continue
        if _to_int(row.get("core_context_needed") or row.get("context_needed"), 0) <= 0:
            continue
        key = str(row.get("match_key") or "").strip()
        if key:
            keys.add(key)
    return keys


def _source_ids(match: Match) -> dict[str, Any]:
    meta = getattr(match, "metadata", None)
    if isinstance(meta, dict):
        for key in ("provider_source_ids", "source_ids", "provider_ids"):
            value = meta.get(key)
            if isinstance(value, dict):
                return value
    return {}


def _bzzoiro_id_from_match(match: Match) -> str | None:
    ids = _source_ids(match)
    for key in ("bzzoiro", "bzzoiro_v2", "bsd", "bsd_v2"):
        value = ids.get(key)
        if value not in (None, ""):
            if isinstance(value, dict):
                for nested in ("id", "event_id", "source_event_id"):
                    if value.get(nested) not in (None, ""):
                        return str(value.get(nested))
            elif isinstance(value, (list, tuple)) and value:
                return str(value[0])
            else:
                return str(value)
    raw = getattr(match, "source_event_id", None)
    if getattr(match, "source", "") == "bzzoiro" and raw not in (None, ""):
        return str(raw)
    return None


def _extract_starting_count(lineups: Any, side: str) -> int | None:
    if not isinstance(lineups, dict):
        return None
    root = lineups.get("lineups") if isinstance(lineups.get("lineups"), dict) else lineups
    team = root.get(side) if isinstance(root, dict) and isinstance(root.get(side), dict) else None
    if not isinstance(team, dict):
        return None
    players = team.get("players") or team.get("starters") or []
    if isinstance(players, list):
        return len(players) or None
    return None


def _context_from_resources(event: dict[str, Any], resources: dict[str, Any], score: float, quality: str | None) -> MatchContext:
    from app.services import windowed_core_coverage_runtime_patch as wc

    home_xg, away_xg = wc._extract_bzzoiro_xg(resources)
    if home_xg is not None:
        home_xg = round(max(0.05, min(4.5, float(home_xg))), 3)
    if away_xg is not None:
        away_xg = round(max(0.05, min(4.5, float(away_xg))), 3)
    odds_hints = wc._bzzoiro_odds_hints(resources)
    odds_payload = resources.get("odds") if isinstance(resources.get("odds"), dict) else {}
    odds = odds_payload.get("odds") if isinstance(odds_payload.get("odds"), dict) else odds_payload
    home_prob = away_prob = None
    if isinstance(odds, dict):
        home = _to_float(odds.get("home_win"))
        draw = _to_float(odds.get("draw"))
        away = _to_float(odds.get("away_win"))
        inv = [(1 / p) for p in (home, draw, away) if p and p > 1.0]
        total = sum(inv)
        if home and home > 1.0 and total > 0:
            home_prob = (1 / home) / total
        if away and away > 1.0 and total > 0:
            away_prob = (1 / away) / total
    confidence = 58.0
    if home_xg is not None or away_xg is not None:
        confidence += 5.0
    if odds_hints:
        confidence += 3.0
    if quality == "fuzzy":
        confidence -= 4.0
    confidence = max(50.0, min(72.0, confidence))
    return MatchContext(
        source="bzzoiro",
        payload={
            "event": event,
            "resources": resources,
            "odds_hints": odds_hints,
            "provider": "bzzoiro_v2_gap_pass",
        },
        expected_home=home_xg,
        expected_away=away_xg,
        home_win_probability=home_prob,
        away_win_probability=away_prob,
        home_starting=_extract_starting_count(resources.get("lineups"), "home"),
        away_starting=_extract_starting_count(resources.get("lineups"), "away"),
        confidence=confidence,
        details={
            "bzzoiro_context_gap_pass": True,
            "bzzoiro_v2_event_id": event.get("id") or event.get("event_id"),
            "bzzoiro_match_score": round(score, 3),
            "bzzoiro_match_quality": quality,
            "bzzoiro_has_stats": isinstance(resources.get("stats"), dict),
            "bzzoiro_has_metadata": isinstance(resources.get("metadata"), dict),
            "bzzoiro_has_lineups": isinstance(resources.get("lineups"), dict),
            "bzzoiro_has_odds": isinstance(resources.get("odds"), dict),
            "bzzoiro_odds_hint_count": len(odds_hints),
            "source_tokens": ["bzzoiro"],
        },
    )


async def _fetch_json(client: httpx.AsyncClient, url: str, headers: dict[str, str], stats: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
    stats["requests"] = _to_int(stats.get("requests"), 0) + 1
    response = await client.get(url, headers=headers, params=params or {})
    stats.setdefault("http_statuses", []).append(response.status_code)
    stats["last_url"] = str(response.url)
    if response.status_code != 200:
        stats["errors"] = _to_int(stats.get("errors"), 0) + 1
        stats["last_error"] = f"http_status={response.status_code}"
        stats["last_body_preview"] = response.text[:600]
        return None
    try:
        return response.json()
    except Exception as exc:
        stats["errors"] = _to_int(stats.get("errors"), 0) + 1
        stats["last_error"] = f"json:{type(exc).__name__}: {exc}"
        return None


def _results(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        for key in ("results", "events", "data", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    return []


async def _gap_pass(self: Any, matches: list[Match], existing_contexts: dict[str, MatchContext]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
    from app.services import windowed_core_coverage_runtime_patch as wc

    token = str(os.getenv("BZZOIRO_API_KEY") or getattr(getattr(self, "settings", None), "bzzoiro_api_key", "") or "").strip()
    stats: dict[str, Any] = {
        "enabled": bool(token),
        "requests": 0,
        "errors": 0,
        "target_matches": 0,
        "matched": 0,
        "contexts_added": 0,
        "contexts_already_present": 0,
        "events_fetched": 0,
        "matched_by_source_id": 0,
        "matched_by_fuzzy": 0,
        "stats_resources": 0,
        "metadata_resources": 0,
        "lineups_resources": 0,
        "odds_resources": 0,
        "stop_reason": "",
    }
    preview: dict[str, Any] = {"added": [], "unmatched": [], "target_sample": []}
    if not token or not matches:
        return {}, stats, preview
    gap_keys = _gap_keys()
    now = datetime.now(UTC)
    candidates: list[Match] = []
    for match in matches:
        if getattr(match, "sport_key", "") != "soccer":
            continue
        key = _match_key(match)
        if key in existing_contexts:
            stats["contexts_already_present"] += 1
            continue
        try:
            hours = (match.commence_time.astimezone(UTC) - now).total_seconds() / 3600.0
        except Exception:
            hours = 999999.0
        if hours < -0.05:
            continue
        if gap_keys and key not in gap_keys:
            continue
        candidates.append(match)
    candidates.sort(key=lambda m: ((m.commence_time.astimezone(UTC) - now).total_seconds(), 0 if _bzzoiro_id_from_match(m) else 1, m.league_name.lower()))
    limit = max(1, _to_int(os.getenv("BZZOIRO_CONTEXT_GAP_MATCH_LIMIT") or 48, 48))
    candidates = candidates[:limit]
    stats["target_matches"] = len(candidates)
    preview["target_sample"] = [{"match_key": _match_key(m), "home": m.home_team, "away": m.away_team, "bzzoiro_id": _bzzoiro_id_from_match(m)} for m in candidates[:20]]
    if not candidates:
        stats["stop_reason"] = "no_gap_targets"
        return {}, stats, preview

    max_requests = max(1, _to_int(os.getenv("BZZOIRO_CONTEXT_GAP_MAX_REQUESTS") or 80, 80))
    headers = {"Authorization": f"Token {token}"}
    added: dict[str, MatchContext] = {}
    events: list[dict[str, Any]] = []
    event_by_id: dict[str, dict[str, Any]] = {}

    async with httpx.AsyncClient(timeout=float(getattr(getattr(self, "settings", None), "bzzoiro_timeout_seconds", 20.0) or 20.0)) as client:
        # Fetch broad event list once. The v2 docs support date_from/date_to + limit/offset.
        min_dt = min(m.commence_time for m in candidates).astimezone(UTC)
        max_dt = max(m.commence_time for m in candidates).astimezone(UTC)
        offset = 0
        while stats["requests"] < max_requests and offset <= 800:
            payload = await _fetch_json(
                client,
                "https://sports.bzzoiro.com/api/v2/events/",
                headers,
                stats,
                params={"date_from": min_dt.date().isoformat(), "date_to": max_dt.date().isoformat(), "limit": 200, "offset": offset},
            )
            batch = _results(payload)
            if not batch:
                break
            events.extend(batch)
            for row in batch:
                row_id = row.get("id") or row.get("event_id")
                if row_id not in (None, ""):
                    event_by_id[str(row_id)] = row
            if not isinstance(payload, dict) or not payload.get("next"):
                break
            offset += 200
        stats["events_fetched"] = len(events)

        for match in candidates:
            if stats["requests"] >= max_requests:
                stats["stop_reason"] = "request_budget_exhausted"
                break
            event = None
            score = 100.0
            quality = "source_id"
            bzz_id = _bzzoiro_id_from_match(match)
            if bzz_id and bzz_id in event_by_id:
                event = event_by_id[bzz_id]
                stats["matched_by_source_id"] += 1
            elif bzz_id:
                payload = await _fetch_json(client, f"https://sports.bzzoiro.com/api/v2/events/{bzz_id}/", headers, stats)
                if isinstance(payload, dict) and (payload.get("id") or payload.get("event_id")):
                    event = payload
                    stats["matched_by_source_id"] += 1
            if event is None:
                event, score, quality = wc._match_bzzoiro_event(match, events, getattr(self, "settings", None))
                if event is not None:
                    stats["matched_by_fuzzy"] += 1
            if event is None:
                if len(preview["unmatched"]) < 20:
                    preview["unmatched"].append({"match_key": _match_key(match), "home": match.home_team, "away": match.away_team})
                continue
            event_id = event.get("id") or event.get("event_id")
            if event_id in (None, ""):
                continue
            resources: dict[str, Any] = {"event": event}
            endpoint_plan = [
                ("stats", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/stats/"),
                ("metadata", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/metadata/"),
                ("lineups", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/lineups/"),
                ("odds", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/odds/"),
            ]
            for name, url in endpoint_plan:
                if stats["requests"] >= max_requests:
                    break
                payload = await _fetch_json(client, url, headers, stats)
                if isinstance(payload, dict):
                    resources[name] = payload
                    stats[f"{name}_resources"] = _to_int(stats.get(f"{name}_resources"), 0) + 1
            context = _context_from_resources(event, resources, float(score or 0.0), quality)
            added[_match_key(match)] = context
            stats["matched"] += 1
            stats["contexts_added"] = len(added)
            if len(preview["added"]) < 20:
                preview["added"].append({
                    "match_key": _match_key(match),
                    "home": match.home_team,
                    "away": match.away_team,
                    "event_id": event_id,
                    "score": round(float(score or 0.0), 2),
                    "quality": quality,
                    "has_xg": context.expected_home is not None or context.expected_away is not None,
                    "odds_hints": context.details.get("bzzoiro_odds_hint_count"),
                })
    return added, stats, preview


def install() -> dict[str, Any]:
    payload = {"created_at_utc": datetime.now(UTC).isoformat(), "status": "starting"}
    try:
        from app.providers.bzzoiro import BzzoiroContextProvider
    except Exception as exc:
        payload.update({"status": "error", "error": f"import:{type(exc).__name__}: {exc}"})
        _write_json(REPORT_PATH, payload)
        return payload
    current = BzzoiroContextProvider.fetch_context
    if getattr(current, "_harizon_bzzoiro_context_gap_finalizer", False):
        payload.update({"status": "already_installed"})
        _write_json(REPORT_PATH, payload)
        return payload

    async def fetch_context_with_gap_pass(self, matches: list[Match]):  # type: ignore[no-untyped-def]
        contexts, stats, preview = await current(self, matches)
        contexts = dict(contexts or {})
        stats = dict(stats or {})
        preview = dict(preview or {})
        if not _truthy(os.getenv("BZZOIRO_CONTEXT_GAP_PASS_ENABLED"), True):
            return contexts, stats, preview
        try:
            added, gap_stats, gap_preview = await _gap_pass(self, matches, contexts)
            contexts.update(added)
            stats["context_gap_pass"] = gap_stats
            stats["contexts_built"] = max(_to_int(stats.get("contexts_built"), 0), len(contexts))
            preview["context_gap_pass"] = gap_preview
        except Exception as exc:
            stats["context_gap_pass"] = {"enabled": True, "error": f"{type(exc).__name__}: {exc}"}
        _write_json(REPORT_PATH, {"status": "ran", "stats": stats.get("context_gap_pass", {}), "preview": preview.get("context_gap_pass", {})})
        return contexts, stats, preview

    fetch_context_with_gap_pass._harizon_bzzoiro_context_gap_finalizer = True  # type: ignore[attr-defined]
    BzzoiroContextProvider.fetch_context = fetch_context_with_gap_pass  # type: ignore[assignment]
    payload.update({"status": "installed", "match_limit": _to_int(os.getenv("BZZOIRO_CONTEXT_GAP_MATCH_LIMIT") or 48, 48), "max_requests": _to_int(os.getenv("BZZOIRO_CONTEXT_GAP_MAX_REQUESTS") or 80, 80)})
    _write_json(REPORT_PATH, payload)
    return payload
