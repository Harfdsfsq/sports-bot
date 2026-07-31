from __future__ import annotations

"""Documentation-driven API enrichment for HARIZON sports-bot.

The core providers already collect the minimum payload needed for predictions.
This runtime layer adds the extra endpoints from api_full_documentation_sports_bot_ru.docx
without weakening publication guards:

- Bzzoiro: odds, live, team detail, squads, standings, social.
- Football-Data: teams and scorers for matched competitions.
- odds-api.io: movements/updated snapshots for matched odds events when budget allows.

All extra payloads are saved to raw cache and attached to MatchContext/Offer metadata
as auxiliary evidence. They are not allowed to create a bet by themselves.
"""

import asyncio
import hashlib
import json
import os
import re
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

UTC = timezone.utc
PATCH_MARKER = "_harizon_api_full_data_runtime_patch_v1"
EXPORT_PATH = Path(".data/exports/latest-api-full-data-enrichment.json")
RAW_CACHE_ROOT = Path(os.getenv("API_RAW_CACHE_DIR") or ".cache/api_raw")


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on", "force"}


def _env_int(name: str, default: int, minimum: int = 0) -> int:
    try:
        return max(minimum, int(float(os.getenv(name) or default)))
    except Exception:
        return max(minimum, int(default))


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            low = str(key).lower()
            if any(token in low for token in ("key", "token", "secret", "authorization", "apikey", "api_key")):
                out[str(key)] = "***"
            else:
                out[str(key)] = _sanitize(item)
        return out
    if isinstance(value, list):
        return [_sanitize(item) for item in value[:200]]
    if isinstance(value, str):
        return value[:2000]
    return value


def _payload_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("results", "data", "matches", "teams", "scorers", "events", "items", "response"):
        value = payload.get(key)
        if isinstance(value, list):
            return [row for row in value if isinstance(row, dict)]
        if isinstance(value, dict):
            nested = _payload_rows(value)
            if nested:
                return nested
    return []


def _payload_shape(payload: Any) -> str:
    if isinstance(payload, list):
        return "list"
    if isinstance(payload, dict):
        return ",".join(sorted(str(k) for k in payload.keys())[:16])
    return type(payload).__name__


def _extract_id(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).strip()


def _nested_id(row: Any, *path: str) -> str:
    cur = row
    for part in path:
        if not isinstance(cur, dict):
            return ""
        cur = cur.get(part)
    return _extract_id(cur)


def _raw_cache_write(api: str, endpoint: str, params: dict[str, Any] | None, status: int | str, payload: Any, headers: dict[str, Any] | None = None) -> str:
    try:
        day = datetime.now(UTC).date().isoformat()
        safe_endpoint = re.sub(r"[^a-zA-Z0-9_.-]+", "_", endpoint.strip("/")) or "root"
        key_text = json.dumps({"endpoint": endpoint, "params": _sanitize(params or {})}, sort_keys=True, ensure_ascii=False)
        digest = hashlib.sha256(key_text.encode("utf-8")).hexdigest()[:18]
        path = RAW_CACHE_ROOT / api / day / f"{safe_endpoint}-{digest}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "api": api,
            "endpoint": endpoint,
            "params": _sanitize(params or {}),
            "status": status,
            "headers": _sanitize(headers or {}),
            "fetched_at_utc": datetime.now(UTC).isoformat(),
            "payload_shape": _payload_shape(payload),
            "rows_count": len(_payload_rows(payload)),
            "raw_json": _sanitize(payload),
        }
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return str(path)
    except Exception:
        return ""


async def _get_json(client: httpx.AsyncClient, api: str, url: str, *, headers: dict[str, str] | None = None, params: dict[str, Any] | None = None, stats: dict[str, Any] | None = None, endpoint: str = "") -> Any | None:
    if stats is not None:
        stats["requests"] = int(stats.get("requests") or 0) + 1
    try:
        response = await client.get(url, headers=headers or None, params=params or None)
    except Exception as exc:
        if stats is not None:
            stats["errors"] = int(stats.get("errors") or 0) + 1
            stats.setdefault("error_examples", []).append(f"{endpoint or url}: {type(exc).__name__}: {exc}"[:500])
        return None
    if stats is not None:
        stats.setdefault("http_statuses", []).append(response.status_code)
    try:
        payload = response.json()
    except Exception:
        payload = {"text_preview": response.text[:2000]}
    cache_path = _raw_cache_write(api, endpoint or url, params, response.status_code, payload, dict(response.headers))
    if stats is not None:
        stats.setdefault("raw_cache_files", []).append(cache_path)
        stats.setdefault("payload_shapes", []).append(_payload_shape(payload))
    if response.status_code != 200:
        if stats is not None:
            stats["errors"] = int(stats.get("errors") or 0) + 1
        return None
    return payload


def _write_export(section: str, payload: dict[str, Any]) -> None:
    try:
        EXPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        current: dict[str, Any] = {}
        if EXPORT_PATH.exists():
            try:
                loaded = json.loads(EXPORT_PATH.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    current = loaded
            except Exception:
                current = {}
        current["updated_at_utc"] = datetime.now(UTC).isoformat()
        current[section] = _sanitize(payload)
        EXPORT_PATH.write_text(json.dumps(current, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        return


def _bzzoiro_ids_from_contexts(contexts: dict[str, Any]) -> dict[str, set[str]]:
    ids: dict[str, set[str]] = {"events": set(), "leagues": set(), "teams": set()}
    for context in contexts.values():
        payload = getattr(context, "payload", {}) or {}
        event = payload.get("event") if isinstance(payload, dict) else None
        prediction = payload.get("prediction") if isinstance(payload, dict) else None
        if isinstance(prediction, dict) and not isinstance(event, dict):
            maybe_event = prediction.get("event")
            if isinstance(maybe_event, dict):
                event = maybe_event
        for row in [event, prediction]:
            if not isinstance(row, dict):
                continue
            event_id = _extract_id(row.get("id") or row.get("event_id") or row.get("event"))
            if event_id and row is event:
                ids["events"].add(event_id)
            league_id = _nested_id(row, "league", "id") or _extract_id(row.get("league_id"))
            if league_id:
                ids["leagues"].add(league_id)
            for key in ("home_team", "away_team", "team", "home", "away"):
                value = row.get(key)
                if isinstance(value, dict):
                    team_id = _extract_id(value.get("id") or value.get("team_id"))
                    if team_id:
                        ids["teams"].add(team_id)
        if isinstance(event, dict):
            for key in ("home_team", "away_team", "home", "away"):
                value = event.get(key)
                if isinstance(value, dict):
                    team_id = _extract_id(value.get("id") or value.get("team_id"))
                    if team_id:
                        ids["teams"].add(team_id)
    return ids


async def _enrich_bzzoiro(self: Any, contexts: dict[str, Any], stats: dict[str, Any], preview: dict[str, Any]) -> None:
    if not contexts or not _truthy(os.getenv("API_FULL_DATA_BZZOIRO_ENABLED"), True):
        return
    api_key = getattr(self, "api_key", None) or os.getenv("BZZOIRO_API_KEY")
    if not api_key:
        return
    base_url = str(getattr(self, "base_url", "https://sports.bzzoiro.com/api") or "https://sports.bzzoiro.com/api").rstrip("/")
    timeout = float(getattr(self, "timeout", 20.0) or 20.0)
    cap = _env_int("API_FULL_DATA_BZZOIRO_EXTRA_MAX_REQUESTS", 18, 0)
    if cap <= 0:
        return
    ids = _bzzoiro_ids_from_contexts(contexts)
    event_ids = list(sorted(ids["events"]))[: _env_int("API_FULL_DATA_BZZOIRO_EVENT_LIMIT", 8, 1)]
    league_ids = list(sorted(ids["leagues"]))[: _env_int("API_FULL_DATA_BZZOIRO_LEAGUE_LIMIT", 4, 1)]
    team_ids = list(sorted(ids["teams"]))[: _env_int("API_FULL_DATA_BZZOIRO_TEAM_LIMIT", 8, 1)]
    extra: dict[str, Any] = {
        "requests": 0,
        "errors": 0,
        "http_statuses": [],
        "payload_shapes": [],
        "raw_cache_files": [],
        "event_ids": event_ids,
        "league_ids": league_ids,
        "team_ids": team_ids,
        "odds_by_event": {},
        "social_by_event": {},
        "standings_by_league": {},
        "team_detail_by_id": {},
        "squad_by_team": {},
        "live_rows_count": 0,
    }
    headers = {"Authorization": f"Token {api_key}"}

    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async def spend(path: str, params: dict[str, Any] | None = None) -> Any | None:
            if int(extra.get("requests") or 0) >= cap:
                extra["budget_exhausted"] = True
                return None
            return await _get_json(client, "bzzoiro", f"{base_url}{path}", headers=headers, params=params or {}, stats=extra, endpoint=path)

        if _truthy(os.getenv("API_FULL_DATA_BZZOIRO_LIVE_ENABLED"), True):
            live_payload = await spend("/live/", {"tz": "UTC", "limit": 100})
            live_rows = _payload_rows(live_payload)
            extra["live_rows_count"] = len(live_rows)
            extra["live_sample"] = live_rows[:5]

        for event_id in event_ids:
            odds_payload = await spend("/odds/", {"event": event_id})
            rows = _payload_rows(odds_payload)
            extra["odds_by_event"][event_id] = {"rows_count": len(rows), "sample": rows[:5]}
            social_payload = await spend("/social/", {"event": event_id, "limit": 50})
            social_rows = _payload_rows(social_payload)
            extra["social_by_event"][event_id] = {"rows_count": len(social_rows), "sample": social_rows[:5]}

        for league_id in league_ids:
            standings_payload = await spend(f"/leagues/{league_id}/standings/", {})
            rows = _payload_rows(standings_payload)
            extra["standings_by_league"][league_id] = {"rows_count": len(rows), "sample": rows[:5]}

        for team_id in team_ids:
            detail_payload = await spend(f"/teams/{team_id}/", {})
            detail_rows = _payload_rows(detail_payload)
            extra["team_detail_by_id"][team_id] = {"shape": _payload_shape(detail_payload), "sample": detail_rows[:3] if detail_rows else _sanitize(detail_payload)}
            squad_payload = await spend(f"/teams/{team_id}/squad/", {})
            squad_rows = _payload_rows(squad_payload)
            extra["squad_by_team"][team_id] = {"rows_count": len(squad_rows), "sample": squad_rows[:5]}

    for context in contexts.values():
        payload = getattr(context, "payload", {})
        if isinstance(payload, dict):
            payload.setdefault("bzzoiro_full_data", {})
            payload["bzzoiro_full_data"].update({
                "extra_payloads_available": True,
                "event_ids_checked": event_ids,
                "league_ids_checked": league_ids,
                "team_ids_checked": team_ids,
            })
        details = getattr(context, "details", {})
        if isinstance(details, dict):
            details["bzzoiro_extra_odds_events_checked"] = len(event_ids)
            details["bzzoiro_extra_team_details_checked"] = len(team_ids)
            details["bzzoiro_extra_standings_checked"] = len(league_ids)
            details["bzzoiro_live_rows_count"] = extra.get("live_rows_count", 0)
            details["bzzoiro_full_data_cache_files"] = len(extra.get("raw_cache_files") or [])

    stats["api_full_data_bzzoiro"] = {
        "requests": extra.get("requests", 0),
        "errors": extra.get("errors", 0),
        "event_ids": event_ids,
        "league_ids": league_ids,
        "team_ids": team_ids,
        "budget_exhausted": bool(extra.get("budget_exhausted")),
    }
    preview["api_full_data_bzzoiro"] = {
        "odds_by_event": extra["odds_by_event"],
        "standings_by_league": extra["standings_by_league"],
        "live_rows_count": extra.get("live_rows_count", 0),
    }
    _write_export("bzzoiro", extra)


async def _enrich_football_data(self: Any, contexts: dict[str, Any], stats: dict[str, Any], preview: dict[str, Any]) -> None:
    if not contexts or not _truthy(os.getenv("API_FULL_DATA_FOOTBALL_DATA_ENABLED"), True):
        return
    api_key = getattr(getattr(self, "settings", None), "football_data_api_key", None)
    if not api_key:
        return
    base_url = str(getattr(self, "base_url", "https://api.football-data.org/v4") or "https://api.football-data.org/v4").rstrip("/")
    timeout = float(getattr(self, "timeout", 20.0) or 20.0)
    cap = _env_int("API_FULL_DATA_FOOTBALL_DATA_EXTRA_MAX_REQUESTS", 4, 0)
    if cap <= 0:
        return
    refs: set[str] = set()
    for context in contexts.values():
        payload = getattr(context, "payload", {}) or {}
        scheduled = payload.get("scheduled") if isinstance(payload, dict) else payload
        row = scheduled if isinstance(scheduled, dict) else payload if isinstance(payload, dict) else {}
        comp = row.get("competition") if isinstance(row, dict) else None
        if isinstance(comp, dict):
            ref = str(comp.get("code") or comp.get("id") or "").strip().upper()
            if ref:
                refs.add(ref)
    refs_list = sorted(refs)[: _env_int("API_FULL_DATA_FOOTBALL_DATA_COMPETITION_LIMIT", 3, 1)]
    extra: dict[str, Any] = {
        "requests": 0,
        "errors": 0,
        "http_statuses": [],
        "payload_shapes": [],
        "raw_cache_files": [],
        "competition_refs": refs_list,
        "teams_by_competition": {},
        "scorers_by_competition": {},
    }
    headers = {"X-Auth-Token": str(api_key)}
    async with httpx.AsyncClient(timeout=timeout, headers=headers, follow_redirects=True) as client:
        async def spend(path: str, params: dict[str, Any] | None = None) -> Any | None:
            if int(extra.get("requests") or 0) >= cap:
                extra["budget_exhausted"] = True
                return None
            return await _get_json(client, "football_data", f"{base_url}{path}", params=params or {}, stats=extra, endpoint=path)

        for ref in refs_list:
            teams_payload = await spend(f"/competitions/{ref}/teams", {})
            team_rows = _payload_rows(teams_payload)
            extra["teams_by_competition"][ref] = {"rows_count": len(team_rows), "sample": team_rows[:5]}
            scorers_payload = await spend(f"/competitions/{ref}/scorers", {"limit": 20})
            scorer_rows = _payload_rows(scorers_payload)
            extra["scorers_by_competition"][ref] = {"rows_count": len(scorer_rows), "sample": scorer_rows[:5]}

    for context in contexts.values():
        payload = getattr(context, "payload", {})
        if isinstance(payload, dict):
            payload.setdefault("football_data_full_data", {})
            payload["football_data_full_data"].update({"competition_refs_checked": refs_list})
        details = getattr(context, "details", {})
        if isinstance(details, dict):
            details["football_data_teams_refs_checked"] = len(refs_list)
            details["football_data_scorers_refs_checked"] = len(refs_list)
            details["football_data_full_data_cache_files"] = len(extra.get("raw_cache_files") or [])

    stats["api_full_data_football_data"] = {
        "requests": extra.get("requests", 0),
        "errors": extra.get("errors", 0),
        "competition_refs": refs_list,
        "budget_exhausted": bool(extra.get("budget_exhausted")),
    }
    preview["api_full_data_football_data"] = {
        "teams_by_competition": extra["teams_by_competition"],
        "scorers_by_competition": extra["scorers_by_competition"],
    }
    _write_export("football_data", extra)


async def _enrich_odds_api_io(self: Any, matches: list[Any], offers_by_match: dict[str, list[Any]], stats: dict[str, Any], preview: dict[str, Any]) -> None:
    if not offers_by_match or not _truthy(
        os.getenv("API_FULL_DATA_ODDS_API_IO_ENABLED"), False
    ):
        return
    accounts = self._odds_accounts() if hasattr(self, "_odds_accounts") else []
    if not accounts:
        return
    cap = _env_int("API_FULL_DATA_ODDS_API_IO_EXTRA_MAX_REQUESTS", 4, 0)
    if cap <= 0:
        return
    by_key = {getattr(match, "match_key", ""): match for match in matches or []}
    event_ids: list[str] = []
    for match_key in offers_by_match.keys():
        match = by_key.get(match_key)
        meta = getattr(match, "metadata", {}) if match is not None else {}
        event_id = _extract_id((meta or {}).get("odds_api_io_id") or getattr(match, "source_event_id", "")) if match is not None else ""
        if event_id and event_id not in event_ids:
            event_ids.append(event_id)
    event_ids = event_ids[: _env_int("API_FULL_DATA_ODDS_MOVEMENT_EVENT_LIMIT", 3, 1)]
    if not event_ids:
        return

    timeout = float(getattr(getattr(self, "settings", None), "odds_api_io_timeout_seconds", 25.0) or 25.0)
    base_url = str(getattr(self, "base_url", "https://api.odds-api.io/v3") or "https://api.odds-api.io/v3").rstrip("/")
    extra: dict[str, Any] = {
        "requests": 0,
        "errors": 0,
        "http_statuses": [],
        "payload_shapes": [],
        "raw_cache_files": [],
        "event_ids": event_ids,
        "movements_by_event": {},
        "updated_rows_count": 0,
    }
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        async def spend(path: str, account: dict[str, str], params: dict[str, Any]) -> Any | None:
            if int(extra.get("requests") or 0) >= cap:
                extra["budget_exhausted"] = True
                return None
            request_params = {"apiKey": account["api_key"], **params}
            return await _get_json(client, "odds_api_io", f"{base_url}{path}", params=request_params, stats=extra, endpoint=path)

        account = accounts[0]
        if _truthy(os.getenv("API_FULL_DATA_ODDS_UPDATED_ENABLED"), True):
            since = (datetime.now(UTC) - timedelta(hours=_env_int("API_FULL_DATA_ODDS_UPDATED_SINCE_HOURS", 6, 1))).replace(microsecond=0).isoformat().replace("+00:00", "Z")
            updated_payload = await spend("/odds/updated", account, {"since": since, "sport": "football", "bookmakers": account.get("bookmakers", "")})
            updated_rows = _payload_rows(updated_payload)
            extra["updated_rows_count"] = len(updated_rows)
            extra["updated_sample"] = updated_rows[:5]

        for event_id in event_ids:
            payload = await spend("/odds/movements", account, {"eventId": event_id, "bookmakers": account.get("bookmakers", "")})
            rows = _payload_rows(payload)
            extra["movements_by_event"][event_id] = {"rows_count": len(rows), "sample": rows[:5]}

    for match_key, offers in offers_by_match.items():
        match = by_key.get(match_key)
        meta = getattr(match, "metadata", {}) if match is not None else {}
        event_id = _extract_id((meta or {}).get("odds_api_io_id") or getattr(match, "source_event_id", "")) if match is not None else ""
        for offer in offers:
            offer_meta = getattr(offer, "metadata", None)
            if isinstance(offer_meta, dict):
                offer_meta["odds_api_io_full_data_checked"] = event_id in event_ids
                if event_id in extra["movements_by_event"]:
                    offer_meta["odds_api_io_movements_rows"] = extra["movements_by_event"][event_id]["rows_count"]
                offer_meta["odds_api_io_updated_rows_count"] = extra.get("updated_rows_count", 0)
                if getattr(offer, "source_event_id", None) in (None, "") and event_id:
                    try:
                        offer.source_event_id = event_id
                    except Exception:
                        pass

    stats["api_full_data_odds_api_io"] = {
        "requests": extra.get("requests", 0),
        "errors": extra.get("errors", 0),
        "event_ids": event_ids,
        "updated_rows_count": extra.get("updated_rows_count", 0),
        "budget_exhausted": bool(extra.get("budget_exhausted")),
    }
    preview["api_full_data_odds_api_io"] = {
        "movements_by_event": extra["movements_by_event"],
        "updated_rows_count": extra.get("updated_rows_count", 0),
    }
    _write_export("odds_api_io", extra)


def _patch_bzzoiro() -> bool:
    try:
        from app.providers import bzzoiro as module
    except Exception:
        return False
    cls = getattr(module, "BzzoiroContextProvider", None)
    if cls is None or getattr(cls, f"{PATCH_MARKER}_bzzoiro", False):
        return False
    original = getattr(cls, "fetch_context", None)
    if not callable(original):
        return False

    async def fetch_context_patched(self: Any, matches: list[Any]):
        contexts, stats, preview = await original(self, matches)
        try:
            await _enrich_bzzoiro(self, contexts, stats, preview)
        except Exception as exc:
            stats["api_full_data_bzzoiro_error"] = f"{type(exc).__name__}: {exc}"[:500]
        return contexts, stats, preview

    cls.fetch_context = fetch_context_patched
    setattr(cls, f"{PATCH_MARKER}_bzzoiro", True)
    return True


def _patch_football_data() -> bool:
    try:
        from app.providers import football_data as module
    except Exception:
        return False
    cls = getattr(module, "FootballDataContextProvider", None)
    if cls is None or getattr(cls, f"{PATCH_MARKER}_football_data", False):
        return False
    original = getattr(cls, "fetch_context", None)
    if not callable(original):
        return False

    async def fetch_context_patched(self: Any, matches: list[Any]):
        contexts, stats, preview = await original(self, matches)
        try:
            await _enrich_football_data(self, contexts, stats, preview)
        except Exception as exc:
            stats["api_full_data_football_data_error"] = f"{type(exc).__name__}: {exc}"[:500]
        return contexts, stats, preview

    cls.fetch_context = fetch_context_patched
    setattr(cls, f"{PATCH_MARKER}_football_data", True)
    return True


def _patch_odds_api_io() -> bool:
    try:
        from app.providers import odds_api_io as module
    except Exception:
        return False
    cls = getattr(module, "OddsApiIoProvider", None)
    if cls is None or getattr(cls, f"{PATCH_MARKER}_odds_api_io", False):
        return False
    original = getattr(cls, "fetch_offers", None)
    if not callable(original):
        return False

    async def fetch_offers_patched(self: Any, matches: list[Any]):
        offers_by_match, stats, preview = await original(self, matches)
        try:
            await _enrich_odds_api_io(self, matches, offers_by_match, stats, preview)
        except Exception as exc:
            stats["api_full_data_odds_api_io_error"] = f"{type(exc).__name__}: {exc}"[:500]
        return offers_by_match, stats, preview

    cls.fetch_offers = fetch_offers_patched
    setattr(cls, f"{PATCH_MARKER}_odds_api_io", True)
    return True


def install() -> bool:
    if not _truthy(os.getenv("API_FULL_DATA_RUNTIME_PATCH_ENABLED"), True):
        return False
    changed = False
    changed = _patch_bzzoiro() or changed
    changed = _patch_football_data() or changed
    changed = _patch_odds_api_io() or changed
    if changed:
        _write_export("install", {"installed_at_utc": datetime.now(UTC).isoformat(), "patch": PATCH_MARKER})
    return changed
