from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
REPORT = Path(".data/exports/latest-sportlogic-games-date-contract-patch.json")
LOADER = Path(".data/exports/latest-sportlogic-date-contract-fixture-loader.json")
_INSTALLED = False
_ORIG_FETCH_MATCHES = None
_ORIG_LOAD = None

def _i(v: Any, d: int = 0) -> int:
    try:
        if v in (None, ""):
            return d
        return int(float(str(v)))
    except Exception:
        return d

def _w(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass

def _params(self: Any, date_key: str) -> list[dict[str, Any]]:
    try:
        day = datetime.fromisoformat(str(date_key)).date()
        nxt = (datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)).date().isoformat()
    except Exception:
        nxt = str(date_key)
    pp = max(5, min(100, _i(os.getenv("SPORTLOGIC_GAMES_PER_PAGE") or os.getenv("SPORTLOGIC_PER_PAGE"), 100)))
    return [
        {"date": str(date_key), "status": "scheduled", "per_page": pp},
        {"date": str(date_key), "per_page": pp},
        {"date_from": str(date_key), "date_to": nxt, "status": "scheduled", "per_page": pp},
        {"date_from": str(date_key), "date_to": nxt, "per_page": pp},
    ]

def _date_keys_from_matches(matches: list[Any]) -> list[str]:
    out: set[str] = set()
    for m in matches or []:
        try:
            dt = getattr(m, "commence_time", None)
            if isinstance(dt, datetime):
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                out.add(dt.astimezone(UTC).date().isoformat())
        except Exception:
            pass
    if out:
        return sorted(out)[:8]
    now = datetime.now(UTC)
    return [(now + timedelta(days=x)).date().isoformat() for x in range(4)]

async def _direct_load(self: Any, dates: list[str], scope: str) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    import httpx
    stats: dict[str, Any] = {"requests": 0, "http_statuses": [], "games_query_variants": [], "date_contract_direct_loader": True}
    preview: dict[str, Any] = {"sample_fixtures": [], "errors": []}
    fixtures: list[dict[str, Any]] = []
    seen: set[str] = set()
    try:
        if _i(getattr(self, "max_requests_per_run", 0), 0) < 12:
            self.max_requests_per_run = 12
    except Exception:
        pass
    async with httpx.AsyncClient(timeout=getattr(self, "timeout", 20.0), follow_redirects=True) as client:
        for date_key in dates:
            for p in _params(self, date_key):
                try:
                    payload = await self._get_json(client, "/games", p, stats, preview)
                    rows = self._extract_list(payload)
                except Exception as exc:
                    preview.setdefault("errors", []).append({"stage": "date_contract_loader", "error": str(exc)[:180]})
                    rows = []
                stats.setdefault("games_query_variants", []).append({"date": date_key, "params": p, "rows": len(rows)})
                if not rows:
                    continue
                for row in rows:
                    if not isinstance(row, dict):
                        continue
                    gid = str(getattr(self, "_game_id", lambda r: "")(row) or row.get("id") or row.get("game_id") or "")
                    key = gid or json.dumps(row, ensure_ascii=False, sort_keys=True)[:200]
                    if key in seen:
                        continue
                    seen.add(key)
                    fixtures.append(row)
                break
    stats["fixtures_fetched"] = len(fixtures)
    preview["sample_fixtures"] = fixtures[:3]
    try:
        self._fixture_cache = fixtures
    except Exception:
        pass
    _w(LOADER, {"created_at_utc": datetime.now(UTC).isoformat(), "status": "ok", "scope": scope, "dates": dates, "fixtures_fetched": len(fixtures), "stats": stats, "sample_fixtures": fixtures[:5]})
    return fixtures, stats, preview

async def _load_patched(self: Any, matches: list[Any]):
    fixtures: list[dict[str, Any]] = []
    stats: dict[str, Any] = {}
    preview: dict[str, Any] = {"sample_fixtures": [], "errors": []}
    if callable(_ORIG_LOAD):
        try:
            fixtures, stats, preview = await _ORIG_LOAD(self, matches)
        except Exception as exc:
            stats = {"original_loader_error": str(exc)[:180]}
    if fixtures:
        return fixtures, stats, preview
    f2, s2, p2 = await _direct_load(self, _date_keys_from_matches(matches), "load_fixtures_for_matches")
    stats.update({k: v for k, v in s2.items() if k not in stats})
    stats["sportlogic_date_contract_fallback_used"] = True
    return f2, stats, p2

async def _fetch_matches_patched(self: Any):
    matches: list[Any] = []
    stats: dict[str, Any] = {}
    preview: dict[str, Any] = {"sample_fixtures": [], "sample_matches": [], "errors": []}
    if callable(_ORIG_FETCH_MATCHES):
        try:
            matches, stats, preview = await _ORIG_FETCH_MATCHES(self)
        except Exception as exc:
            stats = {"original_fetch_matches_error": str(exc)[:180]}
    if matches or _i(stats.get("fixtures_fetched")) > 0:
        return matches, stats, preview
    now = datetime.now(UTC)
    days = max(1, _i(getattr(getattr(self, "settings", None), "run_days_ahead", None), 3))
    dates = [(now + timedelta(days=x)).date().isoformat() for x in range(days + 1)]
    fixtures, s2, p2 = await _direct_load(self, dates, "fetch_matches")
    built = []
    seen = set()
    for row in fixtures:
        try:
            m = self._row_to_match(row)
        except Exception:
            m = None
        if m is None:
            continue
        try:
            dt = m.commence_time.astimezone(UTC)
            if dt < now - timedelta(hours=2) or dt > now + timedelta(days=days):
                continue
        except Exception:
            continue
        key = getattr(m, "match_key", "") or f"{getattr(m,'home_team','')}|{getattr(m,'away_team','')}"
        if key in seen:
            continue
        seen.add(key)
        built.append(m)
    try:
        built = self._prioritize_matches(built)[:max(1, _i(getattr(self, "match_limit", None), 80))]
    except Exception:
        built = built[:max(1, _i(getattr(self, "match_limit", None), 80))]
    stats.update({k: v for k, v in s2.items() if k not in stats})
    stats["fixtures_fetched"] = len(fixtures)
    stats["matches_built"] = len(built)
    stats["sportlogic_date_contract_fetch_matches_fallback_used"] = True
    preview = p2
    preview["sample_matches"] = [{"match_key": getattr(x, "match_key", ""), "home_team": getattr(x, "home_team", ""), "away_team": getattr(x, "away_team", ""), "commence_time": getattr(x, "commence_time", "").isoformat() if hasattr(getattr(x, "commence_time", None), "isoformat") else ""} for x in built[:8]]
    try:
        self._write_coverage_probe(stats, preview, built, fixtures, {})
        self._write_debug_export(stats, preview)
    except Exception:
        pass
    _w(LOADER, {"created_at_utc": datetime.now(UTC).isoformat(), "status": "ok", "scope": "fetch_matches_final", "fixtures_fetched": len(fixtures), "matches_built": len(built), "sample_matches": preview.get("sample_matches", []), "stats": stats})
    return built, stats, preview

def install() -> None:
    global _INSTALLED, _ORIG_FETCH_MATCHES, _ORIG_LOAD
    if _INSTALLED:
        return
    _INSTALLED = True
    os.environ.setdefault("SPORTLOGIC_GAMES_QUERY_CONTRACT", "date_param_first_provider_loader_v8")
    os.environ.setdefault("SPORTLOGIC_GAMES_PER_PAGE", "100")
    os.environ.setdefault("SPORTLOGIC_CONTRACT_PROBE_AUTH_MODES", "x-api-key")
    os.environ.setdefault("SPORTLOGIC_ACTIVE_ODDS_TARGETED_CONFIRMATION_ENABLED", "true")
    os.environ.setdefault("SPORTLOGIC_TARGETED_GAME_DETAIL_LIMIT", "8")
    try:
        from app.providers.sportlogic_provider import SportLogicProvider
        SportLogicProvider._game_query_params = _params
        _ORIG_FETCH_MATCHES = getattr(SportLogicProvider, "fetch_matches", None)
        _ORIG_LOAD = getattr(SportLogicProvider, "_load_fixtures_for_matches", None)
        SportLogicProvider.fetch_matches = _fetch_matches_patched
        SportLogicProvider._load_fixtures_for_matches = _load_patched
        SportLogicProvider._harizon_date_contract_patched = True
        SportLogicProvider._harizon_date_contract_provider_loader_patched = True
        status, error = "installed", ""
    except Exception as exc:
        status, error = "install_failed", str(exc)[:300]
    _w(REPORT, {"created_at_utc": datetime.now(UTC).isoformat(), "status": status, "error": error, "policy": "sportlogic_games_date_param_first_with_provider_fixture_loader_fallback_v8", "expected_next_signal": "fixtures_fetched > 0; if matched 0 then fix team/kickoff matching", "publication_safety": {"price_integrity_guard": "unchanged", "line_movement_guard": "unchanged", "timing_guard": "unchanged", "xg_quality_value_guards": "unchanged"}})
