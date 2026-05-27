from __future__ import annotations

"""Bzzoiro context-gap finalizer.

The v8 reports showed a precise bottleneck: core odds 2+ is healthy, while core
context 2+ stays low because many matches have SStats context but no Bzzoiro
context. The first gap pass used only Bzzoiro v2 /events/ and fetched too small a
universe for matching. This version uses both:
- v1 /events/ and /predictions/ for broad daily coverage;
- v2 /events/ + subresources for stats/metadata/lineups/odds hints.

It does not relax publication guards. It only increases Bzzoiro context coverage
for upcoming progressive gaps.
"""

import json
import os
import re
import unicodedata
from difflib import SequenceMatcher
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

from app.schemas import Match, MatchContext

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
PLAN_PATH = EXPORT_DIR / "latest-progressive-coverage-plan.json"
RUNTIME_REPORT_PATH = EXPORT_DIR / "latest-bzzoiro-context-gap-finalizer.json"
INSTALL_REPORT_PATH = EXPORT_DIR / "latest-bzzoiro-context-gap-finalizer-install.json"


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


def _has_bzzoiro_context(ctx: MatchContext | None) -> bool:
    if ctx is None:
        return False
    if str(getattr(ctx, "source", "") or "").lower() == "bzzoiro":
        return True
    details = getattr(ctx, "details", None)
    if isinstance(details, dict):
        tokens = details.get("source_tokens") or details.get("context_sources") or []
        if isinstance(tokens, str):
            tokens = [tokens]
        if any(str(x).strip().lower() == "bzzoiro" for x in tokens if x is not None):
            return True
    payload = getattr(ctx, "payload", None)
    if isinstance(payload, dict):
        provider = str(payload.get("provider") or payload.get("source") or "").lower()
        if "bzzoiro" in provider:
            return True
    return False


def _gap_keys() -> set[str]:
    plan = _read_json(PLAN_PATH)
    rows = plan.get("core_gap_sample") or plan.get("gap_sample") or []
    keys: set[str] = set()
    if not isinstance(rows, list):
        return keys
    for row in rows:
        if not isinstance(row, dict):
            continue
        hours = _to_float(row.get("hours_to_kickoff"))
        if hours is not None and hours < 0:
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
    for key in ("bzzoiro", "bzzoiro_v1", "bzzoiro_v2", "bsd", "bsd_v2"):
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
    if str(getattr(match, "source", "")).lower() in {"bzzoiro", "bsd"} and raw not in (None, ""):
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
            "provider": "bzzoiro_context_gap_pass",
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





def _norm_text(value: Any) -> str:
    text = unicodedata.normalize("NFKD", str(value or "")).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    stop = {
        "fc", "cf", "sc", "fk", "ac", "cd", "club", "de", "la", "the", "team",
        "women", "u19", "u20", "u21", "u23", "ii", "2", "b",
    }
    tokens = [tok for tok in text.split() if tok and tok not in stop]
    return " ".join(tokens)


def _event_name(row: dict[str, Any], side: str) -> str:
    prefixes = [side, f"{side}_team", f"{side}Team"]
    for key in prefixes + (["домашняя команда"] if side == "home" else []):
        value = row.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, dict):
            for nested in ("name", "team_name", "short_name", "display_name", "title"):
                if value.get(nested):
                    return str(value.get(nested)).strip()
    for key in (f"{side}_team_name", f"{side}_name", f"{side}Name"):
        if row.get(key):
            return str(row.get(key)).strip()
    return ""


def _event_start(row: dict[str, Any]) -> datetime | None:
    values = [
        row.get("event_date"), row.get("start_time"), row.get("commence_time"),
        row.get("kickoff"), row.get("utcDate"), row.get("date_time"), row.get("datetime"),
    ]
    event = row.get("event")
    if isinstance(event, dict):
        values.extend([event.get("event_date"), event.get("start_time"), event.get("commence_time")])
    for value in values:
        if value in (None, ""):
            continue
        try:
            if isinstance(value, (int, float)) or str(value).isdigit():
                raw = float(value)
                if raw > 10_000_000_000:
                    raw /= 1000.0
                return datetime.fromtimestamp(raw, tz=UTC)
            text = str(value).strip().replace(" ", "T")
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            if "T" in text and "+" not in text and text.count("-") >= 2:
                text += "+00:00"
            parsed = datetime.fromisoformat(text)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        except Exception:
            continue
    return None


def _token_similarity(a: str, b: str) -> float:
    na, nb = _norm_text(a), _norm_text(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 100.0
    sa, sb = set(na.split()), set(nb.split())
    overlap = len(sa & sb) / max(1, len(sa | sb))
    seq = SequenceMatcher(None, na, nb).ratio()
    # Token overlap is more reliable for club names with prefixes/suffixes.
    return 100.0 * max(overlap, seq * 0.92)


def _relaxed_event_score(match: Match, row: dict[str, Any]) -> tuple[float, str | None]:
    event = row.get("event") if isinstance(row.get("event"), dict) else row
    if not isinstance(event, dict):
        return 0.0, None
    home = _event_name(event, "home")
    away = _event_name(event, "away")
    if not home or not away:
        return 0.0, None
    direct = (_token_similarity(match.home_team, home) + _token_similarity(match.away_team, away)) / 2.0
    swapped = (_token_similarity(match.home_team, away) + _token_similarity(match.away_team, home)) / 2.0
    name_score = max(direct, swapped - 8.0)
    start = _event_start(event)
    hours_delta = 999.0
    time_score = 45.0
    if start is not None:
        try:
            hours_delta = abs((match.commence_time.astimezone(UTC) - start).total_seconds()) / 3600.0
            if hours_delta <= 0.25:
                time_score = 100.0
            elif hours_delta <= 1.0:
                time_score = 92.0
            elif hours_delta <= 3.0:
                time_score = 82.0
            elif hours_delta <= 8.0:
                time_score = 68.0
            elif hours_delta <= 18.0:
                time_score = 55.0
            else:
                time_score = 10.0
        except Exception:
            pass
    league_score = 0.0
    league = str(event.get("league_name") or event.get("league") or "")
    if isinstance(event.get("league"), dict):
        league = str(event["league"].get("name") or "")
    if league and match.league_name:
        league_score = _token_similarity(match.league_name, league)
    score = name_score * 0.70 + time_score * 0.25 + league_score * 0.05
    quality = "relaxed_exact" if score >= 82 else "relaxed_fuzzy" if score >= 58 else None
    return score, quality


def _best_relaxed_event(match: Match, rows: list[dict[str, Any]], used_ids: set[str] | None = None) -> tuple[dict[str, Any] | None, float, str | None]:
    min_score = _to_float(os.getenv("BZZOIRO_CONTEXT_GAP_RELAXED_MIN_SCORE")) or 54.0
    best: dict[str, Any] | None = None
    best_score = 0.0
    best_quality: str | None = None
    for row in rows:
        if not isinstance(row, dict):
            continue
        event = row.get("event") if isinstance(row.get("event"), dict) else row
        event_id = str((event or {}).get("id") or (event or {}).get("event_id") or row.get("id") or "")
        if used_ids and event_id and event_id in used_ids:
            continue
        score, quality = _relaxed_event_score(match, row)
        if score > best_score:
            best, best_score, best_quality = row, score, quality
    if best is not None and best_score >= min_score:
        return best, best_score, best_quality
    return None, best_score, None


def _inventory_target_date() -> str:
    explicit = str(os.getenv("DAY_INVENTORY_TARGET_DATE") or "").strip()
    if explicit:
        return explicit
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
        return datetime.now(UTC).astimezone(tz).date().isoformat()
    except Exception:
        return datetime.now(UTC).date().isoformat()


def _listish(value: Any) -> list[Any]:
    if isinstance(value, list):
        return value
    if isinstance(value, (tuple, set)):
        return list(value)
    if isinstance(value, dict):
        return list(value.keys())
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in value.replace(";", ",").replace("|", ",").split(",") if x.strip()]
    return []


def _annotate_day_inventory_from_contexts(contexts: dict[str, MatchContext]) -> dict[str, Any]:
    day = _inventory_target_date()
    path = ROOT / ".data" / "day_inventory" / f"{day}.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"status": "missing_inventory", "date_local": day, "path": str(path)}
    matches = payload.get("matches") if isinstance(payload, dict) else None
    if not isinstance(matches, list):
        return {"status": "bad_inventory_shape", "date_local": day, "path": str(path)}
    by_key = {str(row.get("match_key") or row.get("canonical_match_id") or "").strip(): row for row in matches if isinstance(row, dict)}
    updated = 0
    with_2_context = 0
    now_s = datetime.now(UTC).isoformat()
    for match_key, ctx in (contexts or {}).items():
        row = by_key.get(str(match_key or "").strip())
        if not row:
            continue
        existing = [str(x).strip() for x in _listish(row.get("context_sources")) if str(x).strip()]
        if "bzzoiro" not in {x.lower() for x in existing}:
            existing.append("bzzoiro")
        merged = sorted(set(existing), key=lambda x: x.lower())
        row["context_sources"] = merged
        cov = row.setdefault("coverage", {})
        if isinstance(cov, dict):
            cov["context"] = True
            cov["context_sources_count"] = len(merged)
        md = row.setdefault("metadata", {})
        if isinstance(md, dict):
            md["context_sources"] = merged
            md["context_sources_count"] = max(_to_int(md.get("context_sources_count"), 0), len(merged))
            md["bzzoiro_context_gap_annotated_at_utc"] = now_s
            details = getattr(ctx, "details", None)
            if isinstance(details, dict):
                md["bzzoiro_context_gap_details"] = {k: details.get(k) for k in ("bzzoiro_v2_event_id", "bzzoiro_match_quality", "bzzoiro_has_stats", "bzzoiro_has_metadata", "bzzoiro_has_lineups") if k in details}
        updated += 1
        if len(merged) >= 2:
            with_2_context += 1
    if updated:
        payload["updated_at_utc"] = now_s
        payload.setdefault("sources", {})
        if isinstance(payload.get("sources"), dict):
            payload["sources"]["runtime_bzzoiro_context_annotation"] = {
                "updated_at_utc": now_s,
                "updated_matches": updated,
                "matches_with_2plus_runtime_context_sources": with_2_context,
            }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"status": "ok", "date_local": day, "updated_matches": updated, "matches_with_2plus_runtime_context_sources": with_2_context}


async def _fetch_json(client: httpx.AsyncClient, url: str, headers: dict[str, str], stats: dict[str, Any], params: dict[str, Any] | None = None) -> Any:
    stats["requests"] = _to_int(stats.get("requests"), 0) + 1
    response = await client.get(url, headers=headers, params=params or {})
    stats.setdefault("http_statuses", []).append(response.status_code)
    stats["last_url"] = str(response.url)
    if response.status_code != 200:
        stats["errors"] = _to_int(stats.get("errors"), 0) + 1
        stats["last_error"] = f"http_status={response.status_code}"
        stats["last_body_preview"] = response.text[:800]
        return None
    try:
        return response.json()
    except Exception as exc:
        stats["errors"] = _to_int(stats.get("errors"), 0) + 1
        stats["last_error"] = f"json:{type(exc).__name__}: {exc}"
        stats["last_body_preview"] = response.text[:800]
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


async def _fetch_paginated(
    client: httpx.AsyncClient,
    base_url: str,
    path: str,
    headers: dict[str, str],
    params: dict[str, Any],
    stats: dict[str, Any],
    *,
    mode: str,
    max_pages: int,
    max_requests: int,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    if mode == "page":
        for page in range(1, max_pages + 1):
            if _to_int(stats.get("requests"), 0) >= max_requests:
                break
            payload = await _fetch_json(client, f"{base_url}{path}", headers, stats, {**params, "page": page})
            batch = _results(payload)
            if not batch:
                break
            for row in batch:
                sig = str(row.get("id") or row.get("uuid") or row.get("api_id") or row.get("event_id") or row)
                if sig in seen:
                    continue
                seen.add(sig)
                rows.append(row)
            if not isinstance(payload, dict) or not payload.get("next"):
                break
    else:
        offset = 0
        while _to_int(stats.get("requests"), 0) < max_requests and offset <= 1200:
            payload = await _fetch_json(client, f"{base_url}{path}", headers, stats, {**params, "limit": 200, "offset": offset})
            batch = _results(payload)
            if not batch:
                break
            for row in batch:
                sig = str(row.get("id") or row.get("uuid") or row.get("api_id") or row.get("event_id") or row)
                if sig in seen:
                    continue
                seen.add(sig)
                rows.append(row)
            if not isinstance(payload, dict) or not payload.get("next"):
                break
            offset += 200
    return rows


async def _fetch_v2_resources(
    client: httpx.AsyncClient,
    headers: dict[str, str],
    event_id: Any,
    stats: dict[str, Any],
    max_requests: int,
) -> dict[str, Any]:
    resources: dict[str, Any] = {}
    if event_id in (None, ""):
        return resources
    for name, url in [
        ("stats", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/stats/"),
        ("metadata", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/metadata/"),
        ("lineups", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/lineups/"),
        ("odds", f"https://sports.bzzoiro.com/api/v2/events/{event_id}/odds/"),
    ]:
        if _to_int(stats.get("requests"), 0) >= max_requests:
            break
        payload = await _fetch_json(client, url, headers, stats)
        if isinstance(payload, dict):
            resources[name] = payload
            stats[f"{name}_resources"] = _to_int(stats.get(f"{name}_resources"), 0) + 1
    return resources


def _merge_context(base: MatchContext | None, resources_context: MatchContext | None) -> MatchContext | None:
    if base is None:
        return resources_context
    if resources_context is None:
        return base
    payload = dict(base.payload or {})
    payload["gap_resources_context"] = resources_context.payload
    details = dict(base.details or {})
    details.update(resources_context.details or {})
    return MatchContext(
        source="bzzoiro",
        payload=payload,
        expected_home=base.expected_home if base.expected_home is not None else resources_context.expected_home,
        expected_away=base.expected_away if base.expected_away is not None else resources_context.expected_away,
        home_win_probability=base.home_win_probability if base.home_win_probability is not None else resources_context.home_win_probability,
        away_win_probability=base.away_win_probability if base.away_win_probability is not None else resources_context.away_win_probability,
        home_starting=base.home_starting if base.home_starting is not None else resources_context.home_starting,
        away_starting=base.away_starting if base.away_starting is not None else resources_context.away_starting,
        confidence=max(float(base.confidence or 0), float(resources_context.confidence or 0)),
        profits=dict(base.profits or {}),
        details=details,
    )


async def _gap_pass(self: Any, matches: list[Match], existing_contexts: dict[str, MatchContext]) -> tuple[dict[str, MatchContext], dict[str, Any], dict[str, Any]]:
    from app.services import windowed_core_coverage_runtime_patch as wc

    token = str(os.getenv("BZZOIRO_API_KEY") or getattr(getattr(self, "settings", None), "bzzoiro_api_key", "") or "").strip()
    ignore_plan = _truthy(os.getenv("BZZOIRO_CONTEXT_GAP_IGNORE_PLAN"), True)
    stats: dict[str, Any] = {
        "enabled": bool(token),
        "requests": 0,
        "errors": 0,
        "target_matches": 0,
        "matched": 0,
        "contexts_added": 0,
        "contexts_already_present": 0,
        "v1_events_fetched": 0,
        "v1_predictions_fetched": 0,
        "v2_events_fetched": 0,
        "matched_by_source_id": 0,
        "matched_by_v1_prediction": 0,
        "matched_by_v1_event": 0,
        "matched_by_v2_fuzzy": 0,
        "matched_by_relaxed_event": 0,
        "matched_by_relaxed_prediction": 0,
        "relaxed_match_enabled": True,
        "ignore_plan": ignore_plan,
        "stats_resources": 0,
        "metadata_resources": 0,
        "lineups_resources": 0,
        "odds_resources": 0,
        "stop_reason": "",
    }
    preview: dict[str, Any] = {"added": [], "unmatched": [], "target_sample": []}
    if not token or not matches:
        return {}, stats, preview

    gap_keys = set() if ignore_plan else _gap_keys()
    now = datetime.now(UTC)
    candidates: list[Match] = []
    for match in matches:
        if getattr(match, "sport_key", "") != "soccer":
            continue
        key = _match_key(match)
        if key in existing_contexts:
            stats["contexts_already_present"] += 1
            # Existing SStats/ClubElo context is not enough for A-tier.  Keep the
            # match in the Bzzoiro gap pass unless it already has a Bzzoiro
            # context; this is how we build 2+ independent context sources.
            if _has_bzzoiro_context(existing_contexts.get(key)):
                stats["already_has_bzzoiro_context"] = _to_int(stats.get("already_has_bzzoiro_context"), 0) + 1
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
    
    def _priority(match: Match) -> tuple[int, int, float, str]:
        try:
            hours = (match.commence_time.astimezone(UTC) - now).total_seconds() / 3600.0
        except Exception:
            hours = 999999.0
        # Fill near kickoff first because those are the only matches that can publish soon.
        window = 0 if 0 <= hours <= 4 else 1 if 0 <= hours <= 12 else 2 if hours >= 0 else 3
        return (window, 0 if _bzzoiro_id_from_match(match) else 1, abs(hours), match.league_name.lower())

    candidates.sort(key=_priority)
    limit = max(1, _to_int(os.getenv("BZZOIRO_CONTEXT_GAP_MATCH_LIMIT") or 240, 240))
    candidates = candidates[:limit]
    stats["target_matches"] = len(candidates)
    preview["target_sample"] = [{"match_key": _match_key(m), "home": m.home_team, "away": m.away_team, "bzzoiro_id": _bzzoiro_id_from_match(m)} for m in candidates[:25]]
    if not candidates:
        stats["stop_reason"] = "no_gap_targets"
        return {}, stats, preview

    max_requests = max(1, _to_int(os.getenv("BZZOIRO_CONTEXT_GAP_MAX_REQUESTS") or 360, 360))
    headers = {"Authorization": f"Token {token}"}
    added: dict[str, MatchContext] = {}
    min_dt = min(m.commence_time for m in candidates).astimezone(UTC)
    max_dt = max(m.commence_time for m in candidates).astimezone(UTC)
    query_to_date = (max_dt.date() + timedelta(days=1)).isoformat()

    async with httpx.AsyncClient(timeout=float(getattr(getattr(self, "settings", None), "bzzoiro_timeout_seconds", 20.0) or 20.0)) as client:
        v1_base = str(getattr(self, "base_url", "https://sports.bzzoiro.com/api") or "https://sports.bzzoiro.com/api").rstrip("/")
        v1_params = {"date_from": min_dt.date().isoformat(), "date_to": query_to_date, "tz": "UTC"}
        v1_events = await _fetch_paginated(client, v1_base, "/events/", headers, v1_params, stats, mode="page", max_pages=12, max_requests=max_requests)
        stats["v1_events_fetched"] = len(v1_events)
        v1_predictions = await _fetch_paginated(client, v1_base, "/predictions/", headers, {**v1_params, "upcoming": "true"}, stats, mode="page", max_pages=12, max_requests=max_requests)
        if not v1_predictions and _to_int(stats.get("requests"), 0) < max_requests:
            v1_predictions = await _fetch_paginated(client, v1_base, "/predictions/", headers, v1_params, stats, mode="page", max_pages=12, max_requests=max_requests)
        stats["v1_predictions_fetched"] = len(v1_predictions)

        v2_events = await _fetch_paginated(client, "https://sports.bzzoiro.com", "/api/v2/events/", headers, {"date_from": min_dt.date().isoformat(), "date_to": query_to_date}, stats, mode="offset", max_pages=12, max_requests=max_requests)
        stats["v2_events_fetched"] = len(v2_events)
        v2_by_id: dict[str, dict[str, Any]] = {}
        for row in v2_events:
            row_id = row.get("id") or row.get("event_id")
            if row_id not in (None, ""):
                v2_by_id[str(row_id)] = row

        used_prediction_ids: set[str] = set()
        for match in candidates:
            if _to_int(stats.get("requests"), 0) >= max_requests:
                stats["stop_reason"] = "request_budget_exhausted"
                break
            context: MatchContext | None = None
            event_for_resources: dict[str, Any] | None = None
            score = 0.0
            quality: str | None = None

            # 1) Broad v1 prediction coverage. This is the richest context path.
            try:
                pred, pred_quality, pred_score, _ = self._match_prediction(match, v1_predictions, used_prediction_ids)
            except Exception:
                pred, pred_quality, pred_score = None, None, 0.0
            if pred is not None:
                pred_id = None
                try:
                    pred_id = self._prediction_identity(pred)
                except Exception:
                    pred_id = None
                if pred_id:
                    used_prediction_ids.add(pred_id)
                try:
                    event_for_resources = self._prediction_event(pred, v1_events)
                except Exception:
                    event_for_resources = pred.get("event") if isinstance(pred.get("event"), dict) else None
                try:
                    context = self._prediction_to_context(pred, event_for_resources, pred_quality)
                    stats["matched_by_v1_prediction"] += 1
                    score = float(pred_score or 0.0)
                    quality = pred_quality
                except Exception:
                    context = None

            # 2) v1 event fallback: still useful because event has odds/basic context.
            if context is None:
                try:
                    event, event_quality, event_score, _ = self._match_event(match, v1_events)
                except Exception:
                    event, event_quality, event_score = None, None, 0.0
                if event is not None:
                    event_for_resources = event
                    try:
                        context = self._event_to_context(event, event_quality)
                    except Exception:
                        context = None
                    if context is not None:
                        stats["matched_by_v1_event"] += 1
                        score = float(event_score or 0.0)
                        quality = event_quality

            # 3) Relaxed Bzzoiro event/prediction fallback.  The upstream API
            # uses slightly different team labels than odds-api.io/SStats, so
            # strict provider matchers often leave gap targets at matched=0.
            if context is None:
                relaxed_pred, relaxed_score, relaxed_quality = _best_relaxed_event(match, v1_predictions, used_prediction_ids)
                if relaxed_pred is not None:
                    try:
                        pred_id = self._prediction_identity(relaxed_pred)
                    except Exception:
                        pred_id = None
                    if pred_id:
                        used_prediction_ids.add(pred_id)
                    try:
                        event_for_resources = self._prediction_event(relaxed_pred, v1_events)
                    except Exception:
                        event_for_resources = relaxed_pred.get("event") if isinstance(relaxed_pred.get("event"), dict) else None
                    try:
                        context = self._prediction_to_context(relaxed_pred, event_for_resources, relaxed_quality or "relaxed")
                        stats["matched_by_relaxed_prediction"] += 1
                        score = float(relaxed_score or 0.0)
                        quality = relaxed_quality
                    except Exception:
                        context = None
            if context is None:
                relaxed_event, relaxed_score, relaxed_quality = _best_relaxed_event(match, v1_events)
                if relaxed_event is not None:
                    event_for_resources = relaxed_event
                    try:
                        context = self._event_to_context(relaxed_event, relaxed_quality or "relaxed")
                    except Exception:
                        context = None
                    if context is not None:
                        stats["matched_by_relaxed_event"] += 1
                        score = float(relaxed_score or 0.0)
                        quality = relaxed_quality

            # 4) v2 direct/fuzzy fallback.
            if context is None:
                event = None
                bzz_id = _bzzoiro_id_from_match(match)
                if bzz_id and bzz_id in v2_by_id:
                    event = v2_by_id[bzz_id]
                    score = 100.0
                    quality = "source_id"
                    stats["matched_by_source_id"] += 1
                elif bzz_id and _to_int(stats.get("requests"), 0) < max_requests:
                    payload = await _fetch_json(client, f"https://sports.bzzoiro.com/api/v2/events/{bzz_id}/", headers, stats)
                    if isinstance(payload, dict) and (payload.get("id") or payload.get("event_id")):
                        event = payload
                        score = 100.0
                        quality = "source_id"
                        stats["matched_by_source_id"] += 1
                if event is None:
                    event, score, quality = wc._match_bzzoiro_event(match, v2_events, getattr(self, "settings", None))
                    if event is not None:
                        stats["matched_by_v2_fuzzy"] += 1
                if event is None:
                    event, relaxed_score, relaxed_quality = _best_relaxed_event(match, v2_events)
                    if event is not None:
                        score = relaxed_score
                        quality = relaxed_quality
                        stats["matched_by_relaxed_event"] += 1
                if event is not None:
                    event_for_resources = event
                    event_id = event.get("id") or event.get("event_id")
                    resources = {"event": event}
                    resources.update(await _fetch_v2_resources(client, headers, event_id, stats, max_requests))
                    context = _context_from_resources(event, resources, float(score or 0.0), quality)

            # 4) If v1 gave a context and we can cheaply enrich it through v2 ids, merge.
            if context is not None and event_for_resources is not None and _to_int(stats.get("requests"), 0) < max_requests:
                event_id = event_for_resources.get("id") or event_for_resources.get("event_id")
                if event_id not in (None, ""):
                    resources = {"event": event_for_resources}
                    resources.update(await _fetch_v2_resources(client, headers, event_id, stats, max_requests))
                    if len(resources) > 1:
                        resources_context = _context_from_resources(event_for_resources, resources, float(score or 0.0), quality)
                        context = _merge_context(context, resources_context)

            if context is None:
                if len(preview["unmatched"]) < 25:
                    preview["unmatched"].append({"match_key": _match_key(match), "home": match.home_team, "away": match.away_team})
                continue
            existing = existing_contexts.get(_match_key(match))
            if existing is not None:
                context = _merge_context(existing, context) or context
            added[_match_key(match)] = context
            stats["matched"] += 1
            stats["contexts_added"] = len(added)
            if len(preview["added"]) < 25:
                preview["added"].append({
                    "match_key": _match_key(match),
                    "home": match.home_team,
                    "away": match.away_team,
                    "score": round(float(score or 0.0), 2),
                    "quality": quality,
                    "has_xg": context.expected_home is not None or context.expected_away is not None,
                    "odds_hints": (context.details or {}).get("bzzoiro_odds_hint_count"),
                    "context_confidence": round(float(context.confidence or 0.0), 2),
                })
    return added, stats, preview


def install() -> dict[str, Any]:
    payload = {"created_at_utc": datetime.now(UTC).isoformat(), "status": "starting"}
    try:
        from app.providers.bzzoiro import BzzoiroContextProvider
    except Exception as exc:
        payload.update({"status": "error", "error": f"import:{type(exc).__name__}: {exc}"})
        _write_json(INSTALL_REPORT_PATH, payload)
        return payload
    current = BzzoiroContextProvider.fetch_context
    if getattr(current, "_harizon_bzzoiro_context_gap_finalizer", False):
        payload.update({"status": "already_installed"})
        _write_json(INSTALL_REPORT_PATH, payload)
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
            gap_stats["day_inventory_context_annotation"] = _annotate_day_inventory_from_contexts(added)
            stats["context_gap_pass"] = gap_stats
            stats["contexts_built"] = max(_to_int(stats.get("contexts_built"), 0), len(contexts))
            preview["context_gap_pass"] = gap_preview
        except Exception as exc:
            stats["context_gap_pass"] = {"enabled": True, "error": f"{type(exc).__name__}: {exc}"}
            preview["context_gap_pass"] = {"error": f"{type(exc).__name__}: {exc}"}
        _write_json(RUNTIME_REPORT_PATH, {"status": "ran", "created_at_utc": datetime.now(UTC).isoformat(), "stats": stats.get("context_gap_pass", {}), "preview": preview.get("context_gap_pass", {})})
        return contexts, stats, preview

    fetch_context_with_gap_pass._harizon_bzzoiro_context_gap_finalizer = True  # type: ignore[attr-defined]
    BzzoiroContextProvider.fetch_context = fetch_context_with_gap_pass  # type: ignore[assignment]
    payload.update({
        "status": "installed",
        "match_limit": _to_int(os.getenv("BZZOIRO_CONTEXT_GAP_MATCH_LIMIT") or 240, 240),
        "max_requests": _to_int(os.getenv("BZZOIRO_CONTEXT_GAP_MAX_REQUESTS") or 360, 360),
        "ignore_plan_default": True,
        "runtime_report": str(RUNTIME_REPORT_PATH),
    })
    _write_json(INSTALL_REPORT_PATH, payload)
    return payload
