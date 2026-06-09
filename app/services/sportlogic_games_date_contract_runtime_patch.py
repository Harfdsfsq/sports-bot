from __future__ import annotations

"""SportLogic date-contract patch v9: limit-safe fixture loader.

What we learned from diagnostics:
* `/games?date=YYYY-MM-DD&status=scheduled&per_page=100` returns rows.
* The free SportLogic plan has a daily cap; the previous probes exceeded it.
* When the daily-limit marker is open, SportLogic must stay disabled until UTC
  reset.  This patch must not bypass `sportlogic_daily_limit_guard`.

This patch keeps all publication guards unchanged and only makes SportLogic
usable in a low-request mode after the limit resets:
* one proven /games?date request per run by default;
* no legacy date_from/date_to fan-out unless explicitly enabled;
* direct fallback fixture loader if the original provider still returns zero;
* diagnostics show whether fixtures were loaded, matched, or skipped by the
  daily-limit circuit.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
ROOT = Path(__file__).resolve().parents[2]
REPORT = ROOT / ".data" / "exports" / "latest-sportlogic-games-date-contract-patch.json"
LOADER = ROOT / ".data" / "exports" / "latest-sportlogic-date-contract-fixture-loader.json"
DAILY_REPORT = ROOT / ".data" / "exports" / "latest-sportlogic-daily-limit-guard.json"

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


def _truthy(v: Any, default: bool = False) -> bool:
    if v in (None, ""):
        return default
    return str(v).strip().lower() in {"1", "true", "yes", "on", "force"}


def _w(path: Path, payload: dict[str, Any]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _read_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists() and path.stat().st_size > 0:
            data = json.loads(path.read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _daily_circuit_open() -> bool:
    if str(os.getenv("SPORTLOGIC_DAILY_CIRCUIT_OPEN") or "").lower() in {"1", "true", "yes", "on"}:
        return True
    report = _read_json(DAILY_REPORT)
    if report.get("status") == "open" and report.get("date_utc") == datetime.now(UTC).date().isoformat():
        return True
    return False


def _params(self: Any, date_key: str) -> list[dict[str, Any]]:
    pp = max(5, min(100, _i(os.getenv("SPORTLOGIC_GAMES_PER_PAGE") or os.getenv("SPORTLOGIC_PER_PAGE"), 100)))
    variants: list[dict[str, Any]] = [
        {"date": str(date_key), "status": "scheduled", "per_page": pp},
    ]
    if _truthy(os.getenv("SPORTLOGIC_DATE_CONTRACT_TRY_NO_STATUS"), False):
        variants.append({"date": str(date_key), "per_page": pp})
    if _truthy(os.getenv("SPORTLOGIC_KEEP_LEGACY_DATE_FROM_FALLBACKS"), False):
        try:
            day = datetime.fromisoformat(str(date_key)).date()
            nxt = (datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)).date().isoformat()
        except Exception:
            nxt = str(date_key)
        variants.extend([
            {"date_from": str(date_key), "date_to": nxt, "status": "scheduled", "per_page": pp},
            {"date_from": str(date_key), "date_to": nxt, "per_page": pp},
        ])
    return variants


def _date_keys_from_matches(matches: list[Any]) -> list[str]:
    counts: dict[str, int] = {}
    for m in matches or []:
        try:
            dt = getattr(m, "commence_time", None)
            if isinstance(dt, datetime):
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=UTC)
                key = dt.astimezone(UTC).date().isoformat()
                counts[key] = counts.get(key, 0) + 1
        except Exception:
            pass
    max_dates = max(1, _i(os.getenv("SPORTLOGIC_DATE_CONTRACT_MAX_DATES"), 1))
    if counts:
        return [k for k, _ in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:max_dates]]
    now = datetime.now(UTC)
    return [(now + timedelta(days=x)).date().isoformat() for x in range(max_dates)]


async def _direct_load(self: Any, dates: list[str], scope: str) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, Any]]:
    import httpx

    if _daily_circuit_open():
        payload = {
            "created_at_utc": datetime.now(UTC).isoformat(),
            "status": "skipped",
            "scope": scope,
            "reason": "sportlogic_daily_circuit_open",
            "fixtures_fetched": 0,
        }
        _w(LOADER, payload)
        return [], {"requests": 0, "fixtures_fetched": 0, "diagnosis": "sportlogic_daily_circuit_open"}, {"sample_fixtures": [], "errors": []}

    stats: dict[str, Any] = {
        "requests": 0,
        "http_statuses": [],
        "games_query_variants": [],
        "date_contract_direct_loader": True,
        "date_contract_limit_safe": True,
    }
    preview: dict[str, Any] = {"sample_fixtures": [], "errors": []}
    fixtures: list[dict[str, Any]] = []
    seen: set[str] = set()

    # Cap SportLogic at a tiny per-run budget.  This avoids exhausting the free
    # 500/day quota during manual testing and frequent scheduled runs.
    safe_budget = max(1, _i(os.getenv("SPORTLOGIC_LIMIT_SAFE_MAX_REQUESTS_PER_RUN"), 2))
    try:
        current = _i(getattr(self, "max_requests_per_run", 0), 0)
        if current <= 0 or current > safe_budget:
            self.max_requests_per_run = safe_budget
    except Exception:
        pass

    async with httpx.AsyncClient(timeout=getattr(self, "timeout", 20.0), follow_redirects=True) as client:
        for date_key in dates[: max(1, _i(os.getenv("SPORTLOGIC_DATE_CONTRACT_MAX_DATES"), 1))]:
            for p in _params(self, date_key):
                try:
                    if hasattr(self, "_budget_left") and not self._budget_left():
                        stats["budget_exhausted"] = True
                        break
                except Exception:
                    pass
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
    _w(LOADER, {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "ok",
        "scope": scope,
        "dates": dates,
        "fixtures_fetched": len(fixtures),
        "stats": stats,
        "sample_fixtures": fixtures[:5],
    })
    return fixtures, stats, preview


async def _load_patched(self: Any, matches: list[Any]):
    fixtures: list[dict[str, Any]] = []
    stats: dict[str, Any] = {}
    preview: dict[str, Any] = {"sample_fixtures": [], "errors": []}

    if callable(_ORIG_LOAD) and not _daily_circuit_open():
        try:
            fixtures, stats, preview = await _ORIG_LOAD(self, matches)
        except Exception as exc:
            stats = {"original_loader_error": str(exc)[:180]}

    if fixtures:
        return fixtures, stats, preview

    f2, s2, p2 = await _direct_load(self, _date_keys_from_matches(matches), "load_fixtures_for_matches")
    for k, v in s2.items():
        stats.setdefault(k, v)
    stats["sportlogic_date_contract_fallback_used"] = True
    return f2, stats, p2


async def _fetch_matches_patched(self: Any):
    matches: list[Any] = []
    stats: dict[str, Any] = {}
    preview: dict[str, Any] = {"sample_fixtures": [], "sample_matches": [], "errors": []}

    if callable(_ORIG_FETCH_MATCHES) and not _daily_circuit_open():
        try:
            matches, stats, preview = await _ORIG_FETCH_MATCHES(self)
        except Exception as exc:
            stats = {"original_fetch_matches_error": str(exc)[:180]}

    if matches or _i(stats.get("fixtures_fetched")) > 0:
        return matches, stats, preview

    dates = _date_keys_from_matches([])
    fixtures, s2, p2 = await _direct_load(self, dates, "fetch_matches")
    built = []
    seen = set()
    now = datetime.now(UTC)
    days = max(1, _i(getattr(getattr(self, "settings", None), "run_days_ahead", None), 3))
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
    preview["sample_matches"] = [
        {
            "match_key": getattr(x, "match_key", ""),
            "home_team": getattr(x, "home_team", ""),
            "away_team": getattr(x, "away_team", ""),
            "commence_time": getattr(x, "commence_time", "").isoformat() if hasattr(getattr(x, "commence_time", None), "isoformat") else "",
        }
        for x in built[:8]
    ]
    try:
        self._write_coverage_probe(stats, preview, built, fixtures, {})
        self._write_debug_export(stats, preview)
    except Exception:
        pass
    _w(LOADER, {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "ok",
        "scope": "fetch_matches_final",
        "fixtures_fetched": len(fixtures),
        "matches_built": len(built),
        "sample_matches": preview.get("sample_matches", []),
        "stats": stats,
    })
    return built, stats, preview


def install() -> None:
    global _INSTALLED, _ORIG_FETCH_MATCHES, _ORIG_LOAD
    if _INSTALLED:
        return
    _INSTALLED = True

    os.environ.setdefault("SPORTLOGIC_GAMES_QUERY_CONTRACT", "date_param_first_provider_loader_limit_safe_v9")
    os.environ.setdefault("SPORTLOGIC_GAMES_PER_PAGE", "100")
    os.environ.setdefault("SPORTLOGIC_PER_PAGE", "100")
    os.environ.setdefault("SPORTLOGIC_CONTRACT_PROBE_AUTH_MODES", "x-api-key")
    os.environ.setdefault("SPORTLOGIC_CONTRACT_PROBE_MAX_ATTEMPTS", "1")
    os.environ.setdefault("SPORTLOGIC_LIMIT_SAFE_MAX_REQUESTS_PER_RUN", "2")
    os.environ.setdefault("SPORTLOGIC_DATE_CONTRACT_MAX_DATES", "1")
    os.environ.setdefault("SPORTLOGIC_KEEP_LEGACY_DATE_FROM_FALLBACKS", "false")
    os.environ.setdefault("SPORTLOGIC_DATE_CONTRACT_TRY_NO_STATUS", "false")
    # Keep odds discovery off until fixtures/matching are proven in Telegram.
    os.environ.setdefault("SPORTLOGIC_ACTIVE_ODDS_TARGETED_CONFIRMATION_ENABLED", "false")
    os.environ.setdefault("SPORTLOGIC_TARGETED_GAME_DETAIL_LIMIT", "0")

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

    _w(REPORT, {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": status,
        "error": error,
        "policy": "sportlogic_games_date_param_first_with_provider_fixture_loader_limit_safe_v9",
        "daily_circuit_open": _daily_circuit_open(),
        "expected_next_signal": "after UTC reset: fixtures_fetched > 0 with <=2 SportLogic requests/run; if matched 0, fix matching next",
        "publication_safety": {
            "price_integrity_guard": "unchanged",
            "line_movement_guard": "unchanged",
            "timing_guard": "unchanged",
            "xg_quality_value_guards": "unchanged",
        },
    })
