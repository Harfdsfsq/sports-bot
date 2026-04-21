from __future__ import annotations

import json
import os
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw in (None, ""):
        return default
    try:
        return int(float(str(raw).strip()))
    except Exception:
        return default


def _parse_dt(value: Any) -> datetime:
    text = str(value or "").strip()
    if not text:
        return datetime.now(UTC)
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except Exception:
        return datetime.now(UTC)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, datetime):
        return value.astimezone(UTC).isoformat()
    if isinstance(value, Path):
        return str(value)
    if is_dataclass(value):
        return _json_safe(asdict(value))
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    return str(value)


class _DailyCoverageCache:
    def __init__(self, settings: Any) -> None:
        self.settings = settings
        self.enabled = _env_flag("DAILY_COVERAGE_CACHE_ENABLED", True)
        root_default = Path(getattr(settings, "state_path", ".data/state.json")).resolve().parent / "provider_cache" / "daily-coverage"
        self.root = Path(os.getenv("DAILY_COVERAGE_CACHE_PATH") or root_default)
        self.root.mkdir(parents=True, exist_ok=True)
        self.today = datetime.now(UTC).strftime("%Y-%m-%d")
        self.path = self.root / f"{self.today}.json"
        self.payload = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.enabled:
            return {"version": 1, "day": self.today, "providers": {}}
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if str(payload.get("day") or "") != self.today:
            payload = {}
        payload.setdefault("version", 1)
        payload["day"] = self.today
        payload.setdefault("providers", {})
        return payload

    def _save(self) -> None:
        if not self.enabled:
            return
        self.path.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def _provider_bucket(self, provider_name: str) -> dict[str, Any]:
        providers = self.payload.setdefault("providers", {})
        bucket = providers.setdefault(str(provider_name), {})
        return bucket

    def ttl_minutes(self, provider_name: str, method_name: str, *, empty: bool = False) -> int:
        provider = str(provider_name or "").strip().lower()
        method = str(method_name or "").strip().lower()
        if method == "fetch_matches":
            return _env_int("DAILY_COVERAGE_EMPTY_MATCHES_TTL_MINUTES" if empty else "DAILY_COVERAGE_MATCH_TTL_MINUTES", 60 if empty else 120)
        if method == "fetch_offers":
            if empty:
                return _env_int("DAILY_COVERAGE_EMPTY_OFFERS_TTL_MINUTES", 90)
            if provider in {"oddspapi", "allsportsapi"}:
                return _env_int("DAILY_COVERAGE_BACKUP_OFFERS_TTL_MINUTES", 240)
            return _env_int("DAILY_COVERAGE_OFFERS_TTL_MINUTES", 75)
        if method == "fetch_context":
            if empty:
                return _env_int("DAILY_COVERAGE_EMPTY_CONTEXT_TTL_MINUTES", 180)
            if provider in {"newsapi", "gnews"}:
                return _env_int("DAILY_COVERAGE_NEWS_CONTEXT_TTL_MINUTES", 120)
            if provider in {"api_football", "futrixmetrics"}:
                return _env_int("DAILY_COVERAGE_PREMIUM_CONTEXT_TTL_MINUTES", 300)
            return _env_int("DAILY_COVERAGE_CONTEXT_TTL_MINUTES", 360)
        return 60

    def _is_fresh(self, fetched_at: str | None, ttl_minutes: int) -> bool:
        if not fetched_at:
            return False
        try:
            fetched_dt = _parse_dt(fetched_at)
        except Exception:
            return False
        return datetime.now(UTC) - fetched_dt <= timedelta(minutes=max(1, int(ttl_minutes or 1)))

    def get_full(self, provider_name: str, method_name: str) -> tuple[Any | None, dict[str, Any]]:
        if not self.enabled:
            return None, {"enabled": False, "cache_hit": False}
        bucket = self._provider_bucket(provider_name).get(method_name) or {}
        ttl_minutes = self.ttl_minutes(provider_name, method_name, empty=bool(bucket.get("empty")))
        if not self._is_fresh(bucket.get("fetched_at"), ttl_minutes):
            return None, {"enabled": True, "cache_hit": False}
        return self._deserialize_full(method_name, bucket.get("data")), {
            "enabled": True,
            "cache_hit": True,
            "ttl_minutes": ttl_minutes,
            "fetched_at": bucket.get("fetched_at"),
            "empty": bool(bucket.get("empty")),
        }

    def put_full(self, provider_name: str, method_name: str, data: Any) -> None:
        if not self.enabled:
            return
        bucket = self._provider_bucket(provider_name)
        bucket[method_name] = {
            "fetched_at": datetime.now(UTC).isoformat(),
            "empty": self._is_empty_data(method_name, data),
            "data": self._serialize_full(method_name, data),
        }
        self._save()

    def get_incremental(self, provider_name: str, method_name: str, matches: list[Any]) -> tuple[Any, list[Any], dict[str, Any]]:
        if not self.enabled:
            return self._empty_data(method_name), list(matches), {"enabled": False, "hits": 0, "misses": len(matches)}
        entries = (self._provider_bucket(provider_name).get(method_name) or {}).get("entries") or {}
        data = self._empty_data(method_name)
        missing: list[Any] = []
        hits = 0
        stale = 0
        empty_hits = 0
        for match in matches:
            match_key = str(getattr(match, "match_key", "") or "")
            entry = entries.get(match_key)
            if not isinstance(entry, dict):
                missing.append(match)
                continue
            ttl_minutes = self.ttl_minutes(provider_name, method_name, empty=bool(entry.get("empty")))
            if not self._is_fresh(entry.get("fetched_at"), ttl_minutes):
                stale += 1
                missing.append(match)
                continue
            hits += 1
            if bool(entry.get("empty")):
                empty_hits += 1
                continue
            self._merge_match_payload(data, method_name, match_key, entry.get("data"))
        return data, missing, {
            "enabled": True,
            "hits": hits,
            "misses": len(missing),
            "stale": stale,
            "empty_hits": empty_hits,
            "requested": len(matches),
        }

    def put_incremental(self, provider_name: str, method_name: str, requested_matches: list[Any], data: Any) -> None:
        if not self.enabled:
            return
        provider_bucket = self._provider_bucket(provider_name)
        method_bucket = provider_bucket.setdefault(method_name, {})
        entries = method_bucket.setdefault("entries", {})
        now_text = datetime.now(UTC).isoformat()
        data_map = self._data_by_match_key(method_name, data)
        for match in requested_matches:
            match_key = str(getattr(match, "match_key", "") or "")
            payload = data_map.get(match_key)
            entries[match_key] = {
                "fetched_at": now_text,
                "empty": payload is None,
                "data": payload,
            }
        method_bucket["updated_at"] = now_text
        self._save()

    def _serialize_full(self, method_name: str, data: Any) -> Any:
        if method_name == "fetch_matches":
            return [_serialize_match(item) for item in (data or [])]
        return _json_safe(data)

    def _deserialize_full(self, method_name: str, data: Any) -> Any:
        if method_name == "fetch_matches":
            return [_deserialize_match(item) for item in (data or []) if isinstance(item, dict)]
        return data

    def _empty_data(self, method_name: str) -> Any:
        if method_name == "fetch_offers":
            return {}
        if method_name == "fetch_context":
            return {}
        if method_name == "fetch_matches":
            return []
        return {}

    def _is_empty_data(self, method_name: str, data: Any) -> bool:
        if method_name == "fetch_matches":
            return not bool(data)
        if method_name in {"fetch_offers", "fetch_context"}:
            return not bool(data)
        return not bool(data)

    def _data_by_match_key(self, method_name: str, data: Any) -> dict[str, Any]:
        if method_name == "fetch_offers":
            output: dict[str, Any] = {}
            for match_key, offers in (data or {}).items():
                if offers:
                    output[str(match_key)] = [_serialize_offer(item) for item in offers]
            return output
        if method_name == "fetch_context":
            output = {}
            for match_key, context in (data or {}).items():
                if context is not None:
                    output[str(match_key)] = _serialize_context(context)
            return output
        return {}

    def _merge_match_payload(self, target: Any, method_name: str, match_key: str, payload: Any) -> None:
        if method_name == "fetch_offers" and isinstance(target, dict) and isinstance(payload, list):
            target[str(match_key)] = [_deserialize_offer(item) for item in payload if isinstance(item, dict)]
            return
        if method_name == "fetch_context" and isinstance(target, dict) and isinstance(payload, dict):
            target[str(match_key)] = _deserialize_context(payload)

    @staticmethod
    def merge_incremental_data(method_name: str, cached_data: Any, fresh_data: Any) -> Any:
        if method_name in {"fetch_offers", "fetch_context"}:
            merged = {}
            merged.update(cached_data or {})
            merged.update(fresh_data or {})
            return merged
        return fresh_data


def _serialize_match(match: Any) -> dict[str, Any]:
    return {
        "source": getattr(match, "source", ""),
        "source_event_id": getattr(match, "source_event_id", ""),
        "sport_key": getattr(match, "sport_key", ""),
        "league_name": getattr(match, "league_name", ""),
        "home_team": getattr(match, "home_team", ""),
        "away_team": getattr(match, "away_team", ""),
        "commence_time": getattr(match, "commence_time").astimezone(UTC).isoformat() if getattr(match, "commence_time", None) is not None else "",
        "home_team_norm": getattr(match, "home_team_norm", ""),
        "away_team_norm": getattr(match, "away_team_norm", ""),
        "league_key": getattr(match, "league_key", ""),
        "tier": getattr(match, "tier", "mid"),
        "metadata": _json_safe(getattr(match, "metadata", {})),
    }


def _deserialize_match(row: dict[str, Any]) -> Any:
    from app.schemas import Match

    return Match(
        source=str(row.get("source") or ""),
        source_event_id=str(row.get("source_event_id") or ""),
        sport_key=str(row.get("sport_key") or "soccer"),  # type: ignore[arg-type]
        league_name=str(row.get("league_name") or ""),
        home_team=str(row.get("home_team") or ""),
        away_team=str(row.get("away_team") or ""),
        commence_time=_parse_dt(row.get("commence_time")),
        home_team_norm=str(row.get("home_team_norm") or ""),
        away_team_norm=str(row.get("away_team_norm") or ""),
        league_key=str(row.get("league_key") or ""),
        tier=str(row.get("tier") or "mid"),
        metadata=dict(row.get("metadata") or {}),
    )


def _serialize_offer(offer: Any) -> dict[str, Any]:
    return {
        "source": getattr(offer, "source", ""),
        "bookmaker": getattr(offer, "bookmaker", ""),
        "family": getattr(offer, "family", ""),
        "selection": getattr(offer, "selection", ""),
        "price": getattr(offer, "price", 0.0),
        "point": getattr(offer, "point", None),
        "team_side": getattr(offer, "team_side", None),
        "market_name": getattr(offer, "market_name", ""),
        "market_key": getattr(offer, "market_key", ""),
        "market_subtype": getattr(offer, "market_subtype", ""),
        "source_event_id": getattr(offer, "source_event_id", None),
        "metadata": _json_safe(getattr(offer, "metadata", {})),
    }


def _deserialize_offer(row: dict[str, Any]) -> Any:
    from app.schemas import Offer

    return Offer(
        source=str(row.get("source") or ""),
        bookmaker=str(row.get("bookmaker") or ""),
        family=str(row.get("family") or "h2h"),  # type: ignore[arg-type]
        selection=str(row.get("selection") or ""),
        price=float(row.get("price") or 0.0),
        point=float(row["point"]) if row.get("point") not in (None, "") else None,
        team_side=str(row.get("team_side")) if row.get("team_side") not in (None, "") else None,
        market_name=str(row.get("market_name") or ""),
        market_key=str(row.get("market_key") or ""),
        market_subtype=str(row.get("market_subtype") or ""),
        source_event_id=str(row.get("source_event_id")) if row.get("source_event_id") not in (None, "") else None,
        metadata=dict(row.get("metadata") or {}),
    )


def _serialize_context(context: Any) -> dict[str, Any]:
    return {
        "source": getattr(context, "source", ""),
        "payload": _json_safe(getattr(context, "payload", {})),
        "expected_home": getattr(context, "expected_home", None),
        "expected_away": getattr(context, "expected_away", None),
        "home_win_probability": getattr(context, "home_win_probability", None),
        "away_win_probability": getattr(context, "away_win_probability", None),
        "home_starting": getattr(context, "home_starting", None),
        "away_starting": getattr(context, "away_starting", None),
        "confidence": getattr(context, "confidence", 58.0),
        "profits": _json_safe(getattr(context, "profits", {})),
        "details": _json_safe(getattr(context, "details", {})),
    }


def _deserialize_context(row: dict[str, Any]) -> Any:
    from app.schemas import MatchContext

    return MatchContext(
        source=str(row.get("source") or ""),
        payload=dict(row.get("payload") or {}),
        expected_home=float(row["expected_home"]) if row.get("expected_home") not in (None, "") else None,
        expected_away=float(row["expected_away"]) if row.get("expected_away") not in (None, "") else None,
        home_win_probability=float(row["home_win_probability"]) if row.get("home_win_probability") not in (None, "") else None,
        away_win_probability=float(row["away_win_probability"]) if row.get("away_win_probability") not in (None, "") else None,
        home_starting=int(float(row["home_starting"])) if row.get("home_starting") not in (None, "") else None,
        away_starting=int(float(row["away_starting"])) if row.get("away_starting") not in (None, "") else None,
        confidence=float(row.get("confidence") or 58.0),
        profits=dict(row.get("profits") or {}),
        details=dict(row.get("details") or {}),
    )


def _ensure_cache(self: Any) -> _DailyCoverageCache:
    cache = getattr(self, "_daily_coverage_cache", None)
    if cache is None:
        cache = _DailyCoverageCache(self.settings)
        setattr(self, "_daily_coverage_cache", cache)
    return cache


def _merge_cache_stats(stats: dict[str, Any], meta: dict[str, Any]) -> dict[str, Any]:
    merged = dict(stats or {})
    merged["cache_enabled"] = bool(meta.get("enabled"))
    merged["cache_hits"] = int(meta.get("hits") or 0)
    merged["cache_misses"] = int(meta.get("misses") or 0)
    merged["cache_empty_hits"] = int(meta.get("empty_hits") or 0)
    merged["cache_stale"] = int(meta.get("stale") or 0)
    merged["requests_saved"] = int(meta.get("hits") or 0)
    if bool(meta.get("cache_hit")):
        merged["cache_hit"] = True
    return merged


def _patch_runner() -> None:
    try:
        from app.services.runner import PredictionRunner
    except Exception:
        return

    if getattr(PredictionRunner, "_daily_coverage_cache_patched", False):
        return

    original_fetch_provider = PredictionRunner._fetch_provider

    async def patched_fetch_provider(self: Any, provider: Any | None, method_name: str, *args: Any, empty_data: Any):
        if provider is None or method_name not in {"fetch_matches", "fetch_offers", "fetch_context"}:
            return await original_fetch_provider(self, provider, method_name, *args, empty_data=empty_data)

        cache = _ensure_cache(self)
        provider_name = self._provider_name(provider)
        if not cache.enabled:
            return await original_fetch_provider(self, provider, method_name, *args, empty_data=empty_data)

        if method_name == "fetch_matches":
            cached_data, meta = cache.get_full(provider_name, method_name)
            if meta.get("cache_hit"):
                stats = {
                    "enabled": True,
                    "cache_hit": True,
                    "cache_hits": 1,
                    "cache_misses": 0,
                    "requests_saved": 1,
                    "fetched_at": meta.get("fetched_at"),
                }
                return cached_data or [], stats, {"cache": meta}
            data, stats, preview = await original_fetch_provider(self, provider, method_name, *args, empty_data=empty_data)
            cache.put_full(provider_name, method_name, data)
            return data, _merge_cache_stats(stats, {"enabled": True, "hits": 0, "misses": 1}), preview

        if not args or not isinstance(args[0], list):
            return await original_fetch_provider(self, provider, method_name, *args, empty_data=empty_data)

        requested_matches = list(args[0] or [])
        cached_data, missing_matches, meta = cache.get_incremental(provider_name, method_name, requested_matches)
        if not missing_matches:
            stats = {
                "enabled": True,
                "cache_hit": True,
                "cache_hits": meta.get("hits", 0),
                "cache_misses": 0,
                "cache_empty_hits": meta.get("empty_hits", 0),
                "cache_stale": meta.get("stale", 0),
                "requests_saved": meta.get("hits", 0),
            }
            return cached_data, stats, {"cache": meta}

        fresh_args = (missing_matches, *args[1:])
        fresh_data, stats, preview = await original_fetch_provider(self, provider, method_name, *fresh_args, empty_data=empty_data)
        cache.put_incremental(provider_name, method_name, missing_matches, fresh_data)
        merged_data = _DailyCoverageCache.merge_incremental_data(method_name, cached_data, fresh_data)
        return merged_data, _merge_cache_stats(stats, meta), preview

    PredictionRunner._fetch_provider = patched_fetch_provider
    PredictionRunner._daily_coverage_cache_patched = True


def _apply_runtime_overrides() -> None:
    os.environ.setdefault("ENABLE_CONTEXT_STAGING", "true")
    os.environ.setdefault("CONTEXT_ENRICHMENT_REQUIRES_OFFERS", "false")
    os.environ.setdefault("CONTEXT_ENRICHMENT_MATCH_LIMIT", "220")
    os.environ.setdefault("BZZOIRO_CONTEXT_MATCH_LIMIT", "220")
    os.environ.setdefault("ESPN_CONTEXT_MATCH_LIMIT", "160")
    os.environ.setdefault("THESPORTSDB_CONTEXT_MATCH_LIMIT", "220")
    os.environ.setdefault("FOOTBALL_DATA_CONTEXT_MATCH_LIMIT", "220")
    os.environ.setdefault("OPENFOOTBALL_CONTEXT_MATCH_LIMIT", "240")
    os.environ.setdefault("OPENLIGADB_CONTEXT_MATCH_LIMIT", "140")
    os.environ.setdefault("API_FOOTBALL_CONTEXT_MATCH_LIMIT", "24")
    os.environ.setdefault("API_FOOTBALL_PREDICTIONS_LIMIT", "14")
    os.environ.setdefault("NEWSAPI_CONTEXT_MATCH_LIMIT", "10")
    os.environ.setdefault("GNEWS_CONTEXT_MATCH_LIMIT", "8")
    os.environ.setdefault("ODDSPAPI_MATCH_LIMIT", "10")
    os.environ.setdefault("ODDSPAPI_TOURNAMENT_LIMIT", "4")
    os.environ.setdefault("ALLSPORTSAPI_MATCH_LIMIT", "14")
    os.environ.setdefault("MIN_SOURCES_PUBLISH", "2")
    os.environ.setdefault("DAILY_COVERAGE_CACHE_ENABLED", "true")
    os.environ.setdefault("DAILY_COVERAGE_MATCH_TTL_MINUTES", "120")
    os.environ.setdefault("DAILY_COVERAGE_OFFERS_TTL_MINUTES", "75")
    os.environ.setdefault("DAILY_COVERAGE_BACKUP_OFFERS_TTL_MINUTES", "240")
    os.environ.setdefault("DAILY_COVERAGE_CONTEXT_TTL_MINUTES", "360")
    os.environ.setdefault("DAILY_COVERAGE_PREMIUM_CONTEXT_TTL_MINUTES", "300")
    os.environ.setdefault("DAILY_COVERAGE_NEWS_CONTEXT_TTL_MINUTES", "120")
    os.environ.setdefault("DAILY_COVERAGE_EMPTY_MATCHES_TTL_MINUTES", "60")
    os.environ.setdefault("DAILY_COVERAGE_EMPTY_OFFERS_TTL_MINUTES", "90")
    os.environ.setdefault("DAILY_COVERAGE_EMPTY_CONTEXT_TTL_MINUTES", "180")


_apply_runtime_overrides()
_patch_runner()



def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _serialize_candidate_row(candidate: Any) -> dict[str, Any]:
    row = _json_safe(candidate)
    if not isinstance(row, dict):
        row = {}
    if "commence_time" in row and row.get("commence_time") not in (None, ""):
        row["commence_time"] = str(row.get("commence_time"))
    return row


def _deserialize_candidate_row(row: dict[str, Any]) -> Any:
    from app.schemas import CandidateBet

    data = dict(row or {})
    return CandidateBet(
        match_key=str(data.get("match_key") or ""),
        sport_key=str(data.get("sport_key") or "soccer"),  # type: ignore[arg-type]
        league_name=str(data.get("league_name") or ""),
        home_team=str(data.get("home_team") or ""),
        away_team=str(data.get("away_team") or ""),
        commence_time=_parse_dt(data.get("commence_time")),
        family=str(data.get("family") or "h2h"),  # type: ignore[arg-type]
        selection=str(data.get("selection") or ""),
        selection_key=str(data.get("selection_key") or ""),
        odds=_safe_float(data.get("odds"), 0.0),
        fair_odds=_safe_float(data.get("fair_odds"), 0.0),
        implied_probability=_safe_float(data.get("implied_probability"), 0.0),
        market_probability=_safe_float(data.get("market_probability"), 0.0),
        consensus_probability=_safe_float(data.get("consensus_probability"), 0.0),
        model_probability=_safe_float(data.get("model_probability"), 0.0),
        final_probability=_safe_float(data.get("final_probability"), 0.0),
        adjusted_probability=_safe_float(data.get("adjusted_probability"), 0.0),
        edge_pct=_safe_float(data.get("edge_pct"), 0.0),
        ev_pct=_safe_float(data.get("ev_pct"), 0.0),
        confidence=_safe_float(data.get("confidence"), 0.0),
        books_count=int(_safe_float(data.get("books_count"), 0.0)),
        sources_count=int(_safe_float(data.get("sources_count"), 0.0)),
        model_mode=str(data.get("model_mode") or "market_only"),
        point=_safe_float(data.get("point"), None) if data.get("point") not in (None, "") else None,
        expected_home=_safe_float(data.get("expected_home"), None) if data.get("expected_home") not in (None, "") else None,
        expected_away=_safe_float(data.get("expected_away"), None) if data.get("expected_away") not in (None, "") else None,
        reasons=[str(item) for item in (data.get("reasons") or []) if str(item)],
        source_summary=dict(data.get("source_summary") or {}),
        bookmaker=str(data.get("bookmaker")) if data.get("bookmaker") not in (None, "") else None,
        diagnostics=dict(data.get("diagnostics") or {}),
        analysis=dict(data.get("analysis") or {}),
        publication_score=_safe_float(data.get("publication_score"), 0.0),
        source_event_id=str(data.get("source_event_id")) if data.get("source_event_id") not in (None, "") else None,
        team_side=str(data.get("team_side")) if data.get("team_side") not in (None, "") else None,
        stake_amount=_safe_float(data.get("stake_amount"), 0.0),
        stake_pct=_safe_float(data.get("stake_pct"), 0.0),
        bankroll_snapshot=_safe_float(data.get("bankroll_snapshot"), 0.0),
        bankroll_currency=str(data.get("bankroll_currency") or "u"),
        risk_label=str(data.get("risk_label") or "standard"),
        already_used=bool(data.get("already_used")),
    )


class _DayShortlistTracker:
    def __init__(self, state_path: str | Path, export_dir: str | Path) -> None:
        self.enabled = _env_flag("DAY_SHORTLIST_ENABLED", True)
        self.state_root = Path(state_path).resolve().parent
        self.export_root = Path(export_dir)
        default_root = self.state_root / "provider_cache" / "day-shortlist"
        self.root = Path(os.getenv("DAY_SHORTLIST_PATH") or default_root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.today = datetime.now(UTC).strftime("%Y-%m-%d")
        self.path = self.root / f"{self.today}.json"
        self.payload = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.enabled:
            return {"version": 1, "day": self.today, "runs": [], "matches": {}, "candidates": {}, "last_compare": {}}
        try:
            payload = json.loads(self.path.read_text(encoding='utf-8'))
        except Exception:
            payload = {}
        if not isinstance(payload, dict) or str(payload.get('day') or '') != self.today:
            payload = {}
        payload.setdefault('version', 1)
        payload['day'] = self.today
        payload.setdefault('runs', [])
        payload.setdefault('matches', {})
        payload.setdefault('candidates', {})
        payload.setdefault('last_compare', {})
        return payload

    def _save(self) -> None:
        if not self.enabled:
            return
        self.path.write_text(json.dumps(self.payload, ensure_ascii=False, indent=2), encoding='utf-8')

    def update_from_run_payload(self, payload: dict[str, Any], archive_path: str | None = None) -> dict[str, Any]:
        if not self.enabled:
            return {"enabled": False}
        created_at = str(payload.get('created_at') or datetime.now(UTC).isoformat())
        run_key = archive_path or created_at
        before_top = self.top_candidate_keys()

        runs = [item for item in (self.payload.get('runs') or []) if isinstance(item, dict)]
        runs.append({
            'created_at': created_at,
            'archive_path': archive_path or '',
            'matches_seen': int(((payload.get('summary') or {}).get('matches_seen')) or 0),
            'contexts_built': int(((payload.get('summary') or {}).get('contexts_built')) or 0),
            'published': int(((payload.get('summary') or {}).get('published')) or 0),
        })
        self.payload['runs'] = runs[-24:]

        self._update_match_ledger(payload, created_at, run_key)
        self._update_candidate_ledger(payload, created_at, run_key)
        compare = self._build_compare(before_top, self.top_candidate_keys())
        self.payload['last_compare'] = compare
        self._save()
        self._export_daily_views(compare)
        return {
            'enabled': True,
            'day': self.today,
            'matches_tracked': len(self.payload.get('matches') or {}),
            'candidates_tracked': len(self.payload.get('candidates') or {}),
            'compare': compare,
        }

    def top_candidate_keys(self) -> list[str]:
        top = self._top_candidates(max_items=max(0, _env_int('DAY_SHORTLIST_MAX_ITEMS', 40)))
        return [str(item.get('fingerprint') or '') for item in top if str(item.get('fingerprint') or '')]

    def _update_match_ledger(self, payload: dict[str, Any], created_at: str, run_key: str) -> None:
        matches_bucket = self.payload.setdefault('matches', {})
        diag_matches = [item for item in (((payload.get('provider_diagnostics') or {}).get('matches')) or []) if isinstance(item, dict)]
        if not diag_matches:
            diag_matches = [item for item in (payload.get('matches') or []) if isinstance(item, dict)]
        for row in diag_matches:
            match_key = str(row.get('match_key') or '')
            if not match_key:
                continue
            entry = dict(matches_bucket.get(match_key) or {})
            entry.setdefault('match_key', match_key)
            entry.setdefault('league_name', str(row.get('league_name') or ''))
            entry.setdefault('home_team', str(row.get('home_team') or ''))
            entry.setdefault('away_team', str(row.get('away_team') or ''))
            entry.setdefault('commence_time', str(row.get('commence_time') or ''))
            entry['first_seen_at'] = str(entry.get('first_seen_at') or created_at)
            entry['last_seen_at'] = created_at
            entry['seen_runs'] = int(entry.get('seen_runs') or 0) + 1
            offer_sources = row.get('offer_sources') or []
            context_sources = row.get('context_sources') or []
            entry['best_offer_sources_count'] = max(int(entry.get('best_offer_sources_count') or 0), len(offer_sources))
            entry['best_context_sources_count'] = max(int(entry.get('best_context_sources_count') or 0), len(context_sources))
            entry['last_offer_sources'] = [str(item) for item in offer_sources if str(item)]
            entry['last_context_sources'] = [str(item) for item in context_sources if str(item)]
            entry['raw_candidate_count_max'] = max(int(entry.get('raw_candidate_count_max') or 0), int(row.get('raw_candidate_count') or 0))
            entry['published_candidate_count_max'] = max(int(entry.get('published_candidate_count_max') or 0), int(row.get('published_candidate_count') or 0))
            entry['last_run_key'] = run_key
            matches_bucket[match_key] = entry

    def _update_candidate_ledger(self, payload: dict[str, Any], created_at: str, run_key: str) -> None:
        candidates_bucket = self.payload.setdefault('candidates', {})
        source_sets = [
            ('published', [item for item in (payload.get('candidates') or []) if isinstance(item, dict)]),
            ('zero_stake', [item for item in (payload.get('candidates_zero_stake') or []) if isinstance(item, dict)]),
            ('shadow', [item for item in (payload.get('shadow_candidates') or []) if isinstance(item, dict)]),
        ]
        keep_per_match = max(1, _env_int('DAY_SHORTLIST_KEEP_PER_MATCH', 3))
        for bucket_name, rows in source_sets:
            for row in rows:
                fingerprint = str(row.get('fingerprint') or row.get('prediction_id') or '')
                if not fingerprint:
                    continue
                entry = dict(candidates_bucket.get(fingerprint) or {})
                rank = self._rank_tuple(row, bucket_name)
                old_rank = tuple(entry.get('best_rank') or ())
                entry.setdefault('fingerprint', fingerprint)
                entry.setdefault('match_key', str(row.get('match_key') or ''))
                entry.setdefault('league_name', str(row.get('league_name') or ''))
                entry.setdefault('home_team', str(row.get('home_team') or ''))
                entry.setdefault('away_team', str(row.get('away_team') or ''))
                entry.setdefault('family', str(row.get('family') or ''))
                entry.setdefault('selection', str(row.get('selection') or ''))
                entry.setdefault('selection_key', str(row.get('selection_key') or ''))
                entry.setdefault('commence_time', str(row.get('commence_time') or ''))
                entry['first_seen_at'] = str(entry.get('first_seen_at') or created_at)
                entry['last_seen_at'] = created_at
                entry['seen_runs'] = int(entry.get('seen_runs') or 0) + 1
                entry['source_bucket'] = bucket_name if rank >= old_rank else str(entry.get('source_bucket') or bucket_name)
                entry['best_rank'] = list(rank) if rank >= old_rank else list(old_rank)
                entry['best_publication_score'] = max(_safe_float(entry.get('best_publication_score')), _safe_float(row.get('publication_score')))
                entry['best_ev_pct'] = max(_safe_float(entry.get('best_ev_pct')), _safe_float(row.get('ev_pct')))
                entry['best_edge_pct'] = max(_safe_float(entry.get('best_edge_pct')), _safe_float(row.get('edge_pct')))
                entry['best_confidence'] = max(_safe_float(entry.get('best_confidence')), _safe_float(row.get('confidence')))
                entry['best_books_count'] = max(int(_safe_float(entry.get('best_books_count'))), int(_safe_float(row.get('books_count'))))
                entry['best_sources_count'] = max(int(_safe_float(entry.get('best_sources_count'))), int(_safe_float(row.get('sources_count'))))
                entry['best_odds'] = _safe_float(row.get('odds')) or _safe_float(entry.get('best_odds'))
                entry['latest_candidate'] = _serialize_candidate_row(row)
                entry['history'] = list((entry.get('history') or []))[-7:] + [{
                    'run_key': run_key,
                    'seen_at': created_at,
                    'bucket': bucket_name,
                    'publication_score': _safe_float(row.get('publication_score')),
                    'ev_pct': _safe_float(row.get('ev_pct')),
                    'edge_pct': _safe_float(row.get('edge_pct')),
                    'confidence': _safe_float(row.get('confidence')),
                    'books_count': int(_safe_float(row.get('books_count'))),
                    'sources_count': int(_safe_float(row.get('sources_count'))),
                }]
                candidates_bucket[fingerprint] = entry
        self._trim_per_match(keep_per_match)

    def _trim_per_match(self, keep_per_match: int) -> None:
        by_match: dict[str, list[dict[str, Any]]] = {}
        for entry in [dict(item) for item in (self.payload.get('candidates') or {}).values() if isinstance(item, dict)]:
            by_match.setdefault(str(entry.get('match_key') or ''), []).append(entry)
        keep: set[str] = set()
        for match_key, entries in by_match.items():
            entries.sort(key=lambda item: tuple(item.get('best_rank') or [0, 0.0, 0.0, 0.0]), reverse=True)
            for entry in entries[:keep_per_match]:
                keep.add(str(entry.get('fingerprint') or ''))
        self.payload['candidates'] = {
            key: value
            for key, value in (self.payload.get('candidates') or {}).items()
            if str(key) in keep
        }

    def _rank_tuple(self, row: dict[str, Any], bucket_name: str) -> tuple[int, float, float, float]:
        bucket_priority = {'published': 3, 'zero_stake': 2, 'shadow': 1}.get(str(bucket_name or ''), 0)
        return (
            bucket_priority,
            _safe_float(row.get('publication_score')),
            _safe_float(row.get('ev_pct')),
            _safe_float(row.get('confidence')),
        )

    def _top_candidates(self, max_items: int = 40) -> list[dict[str, Any]]:
        rows = [dict(item) for item in (self.payload.get('candidates') or {}).values() if isinstance(item, dict)]
        rows = [item for item in rows if self._candidate_still_live(item)]
        rows.sort(key=lambda item: tuple(item.get('best_rank') or [0, 0.0, 0.0, 0.0]), reverse=True)
        return rows[:max_items]

    def _candidate_still_live(self, entry: dict[str, Any]) -> bool:
        commence_raw = str(entry.get('commence_time') or '')
        if not commence_raw:
            return False
        try:
            commence = _parse_dt(commence_raw)
        except Exception:
            return False
        max_age_hours = max(1, _env_int('DAY_SHORTLIST_CARRYOVER_MAX_AGE_HOURS', 12))
        seen_at = _parse_dt(entry.get('last_seen_at'))
        if datetime.now(UTC) - seen_at > timedelta(hours=max_age_hours):
            return False
        min_lead = max(10, _env_int('MIN_KICKOFF_LEAD_MINUTES', 30))
        return commence > datetime.now(UTC) + timedelta(minutes=min_lead)

    def _build_compare(self, before: list[str], after: list[str]) -> dict[str, Any]:
        before_set = set(before)
        after_set = set(after)
        return {
            'new': [item for item in after if item not in before_set],
            'dropped': [item for item in before if item not in after_set],
            'stable': [item for item in after if item in before_set],
            'before_count': len(before),
            'after_count': len(after),
        }

    def _export_daily_views(self, compare: dict[str, Any]) -> None:
        self.export_root.mkdir(parents=True, exist_ok=True)
        shortlist = [self._short_row(item) for item in self._top_candidates(max_items=max(0, _env_int('DAY_SHORTLIST_MAX_ITEMS', 40)))]
        latest_shortlist = self.export_root / 'latest-day-shortlist.json'
        latest_csv = self.export_root / 'latest-day-shortlist.csv'
        compare_path = self.export_root / 'latest-day-shortlist-compare.json'
        coverage_path = self.export_root / 'latest-day-coverage-summary.json'
        latest_shortlist.write_text(json.dumps(shortlist, ensure_ascii=False, indent=2), encoding='utf-8')
        compare_path.write_text(json.dumps(compare, ensure_ascii=False, indent=2), encoding='utf-8')
        coverage_summary = {
            'day': self.today,
            'runs_recorded': len(self.payload.get('runs') or []),
            'matches_tracked': len(self.payload.get('matches') or {}),
            'candidates_tracked': len(self.payload.get('candidates') or {}),
            'top_shortlist_count': len(shortlist),
        }
        coverage_path.write_text(json.dumps(coverage_summary, ensure_ascii=False, indent=2), encoding='utf-8')
        self._write_csv(latest_csv, shortlist)

    def _short_row(self, entry: dict[str, Any]) -> dict[str, Any]:
        return {
            'fingerprint': str(entry.get('fingerprint') or ''),
            'match_key': str(entry.get('match_key') or ''),
            'league_name': str(entry.get('league_name') or ''),
            'home_team': str(entry.get('home_team') or ''),
            'away_team': str(entry.get('away_team') or ''),
            'commence_time': str(entry.get('commence_time') or ''),
            'family': str(entry.get('family') or ''),
            'selection': str(entry.get('selection') or ''),
            'source_bucket': str(entry.get('source_bucket') or ''),
            'best_publication_score': round(_safe_float(entry.get('best_publication_score')), 3),
            'best_ev_pct': round(_safe_float(entry.get('best_ev_pct')), 3),
            'best_edge_pct': round(_safe_float(entry.get('best_edge_pct')), 3),
            'best_confidence': round(_safe_float(entry.get('best_confidence')), 2),
            'best_books_count': int(_safe_float(entry.get('best_books_count'))),
            'best_sources_count': int(_safe_float(entry.get('best_sources_count'))),
            'best_odds': round(_safe_float(entry.get('best_odds')), 3),
            'first_seen_at': str(entry.get('first_seen_at') or ''),
            'last_seen_at': str(entry.get('last_seen_at') or ''),
            'seen_runs': int(entry.get('seen_runs') or 0),
        }

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        import csv

        headers: list[str] = []
        for row in rows:
            for key in row.keys():
                if key not in headers:
                    headers.append(key)
        with path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow(row)

    def load_carryover_candidates(self, runner: Any, limit: int) -> list[Any]:
        if not _env_flag('DAY_SHORTLIST_CARRYOVER_ENABLED', True):
            return []
        allowed_buckets = {'published', 'zero_stake'}
        if _env_flag('DAY_SHORTLIST_CARRYOVER_INCLUDE_SHADOW', False):
            allowed_buckets.add('shadow')
        blocked: set[str] = set()
        try:
            state = getattr(runner, 'state', None)
            if state is not None:
                for collection_name in ('bets', 'shadow_bets', 'published_candidates'):
                    for row in (getattr(state, '_state', {}).get(collection_name) or []):
                        if isinstance(row, dict):
                            fp = str(row.get('fingerprint') or row.get('prediction_id') or '')
                            if fp:
                                blocked.add(fp)
        except Exception:
            pass
        output: list[Any] = []
        seen_matches: set[str] = set()
        for entry in self._top_candidates(max_items=max(limit * 4, 10)):
            fp = str(entry.get('fingerprint') or '')
            if not fp or fp in blocked:
                continue
            if str(entry.get('source_bucket') or '') not in allowed_buckets:
                continue
            candidate_row = dict(entry.get('latest_candidate') or {})
            if not candidate_row:
                continue
            try:
                candidate = _deserialize_candidate_row(candidate_row)
            except Exception:
                continue
            if str(candidate.match_key or '') in seen_matches:
                continue
            candidate.source_summary = dict(getattr(candidate, 'source_summary', {}) or {})
            candidate.source_summary['day_shortlist_carryover'] = True
            candidate.reasons = list(getattr(candidate, 'reasons', []) or [])
            if 'carryover=day_shortlist' not in candidate.reasons:
                candidate.reasons.append('carryover=day_shortlist')
            output.append(candidate)
            seen_matches.add(str(candidate.match_key or ''))
            if len(output) >= limit:
                break
        return output


def _patch_state_and_shortlist() -> None:
    from app.state import JsonStateStore

    if getattr(JsonStateStore, '_day_shortlist_patched', False):
        return

    original_archive_run_payload = JsonStateStore.archive_run_payload

    def patched_archive_run_payload(self: Any, payload: dict[str, Any], settings: Any | None = None) -> dict[str, Any]:
        result = original_archive_run_payload(self, payload, settings=settings)
        try:
            tracker = _DayShortlistTracker(getattr(self, 'state_path', '.data/state.json'), getattr(settings, 'storage_export_dir', '.data/exports') if settings is not None else '.data/exports')
            tracker_result = tracker.update_from_run_payload(payload, archive_path=str((result or {}).get('run_archive_path') or ''))
            if isinstance(result, dict):
                result['day_shortlist'] = tracker_result
        except Exception:
            pass
        return result

    JsonStateStore.archive_run_payload = patched_archive_run_payload
    JsonStateStore._day_shortlist_patched = True


def _patch_day_shortlist_selection() -> None:
    from app.services.runner import PredictionRunner

    if getattr(PredictionRunner, '_day_shortlist_selection_patched', False):
        return

    original_select_publishable = PredictionRunner._select_publishable_candidates

    def patched_select_publishable(self: Any, candidates: list[Any]) -> list[Any]:
        selected = list(original_select_publishable(self, candidates) or [])
        limit = max(1, int(getattr(self.settings, 'max_picks_per_run', 2) or 2))
        if len(selected) >= limit:
            return selected
        try:
            tracker = _DayShortlistTracker(getattr(self.settings, 'state_path', '.data/state.json'), getattr(self.settings, 'storage_export_dir', '.data/exports'))
            carryovers = tracker.load_carryover_candidates(self, limit=max(0, limit - len(selected)))
        except Exception:
            carryovers = []
        if not carryovers:
            return selected
        seen_matches = {str(getattr(item, 'match_key', '') or '') for item in selected}
        seen_fingerprints = {
            '|'.join([
                str(getattr(item, 'match_key', '') or ''),
                str(getattr(item, 'family', '') or ''),
                str(getattr(item, 'selection_key', '') or ''),
                str(getattr(item, 'team_side', '') or ''),
                '' if getattr(item, 'point', None) in (None, '') else f"{float(getattr(item, 'point')):g}",
                getattr(item, 'commence_time').astimezone(UTC).isoformat() if getattr(item, 'commence_time', None) is not None else '',
            ])
            for item in selected
        }
        for candidate in carryovers:
            fingerprint = '|'.join([
                str(getattr(candidate, 'match_key', '') or ''),
                str(getattr(candidate, 'family', '') or ''),
                str(getattr(candidate, 'selection_key', '') or ''),
                str(getattr(candidate, 'team_side', '') or ''),
                '' if getattr(candidate, 'point', None) in (None, '') else f"{float(getattr(candidate, 'point')):g}",
                getattr(candidate, 'commence_time').astimezone(UTC).isoformat() if getattr(candidate, 'commence_time', None) is not None else '',
            ])
            if fingerprint in seen_fingerprints or str(getattr(candidate, 'match_key', '') or '') in seen_matches:
                continue
            selected.append(candidate)
            seen_fingerprints.add(fingerprint)
            seen_matches.add(str(getattr(candidate, 'match_key', '') or ''))
            if len(selected) >= limit:
                break
        return selected

    PredictionRunner._select_publishable_candidates = patched_select_publishable
    PredictionRunner._day_shortlist_selection_patched = True


def _apply_day_shortlist_overrides() -> None:
    os.environ.setdefault('DAY_SHORTLIST_ENABLED', 'true')
    os.environ.setdefault('DAY_SHORTLIST_MAX_ITEMS', '40')
    os.environ.setdefault('DAY_SHORTLIST_KEEP_PER_MATCH', '3')
    os.environ.setdefault('DAY_SHORTLIST_CARRYOVER_ENABLED', 'true')
    os.environ.setdefault('DAY_SHORTLIST_CARRYOVER_INCLUDE_SHADOW', 'false')
    os.environ.setdefault('DAY_SHORTLIST_CARRYOVER_MAX_AGE_HOURS', '12')


_apply_day_shortlist_overrides()
_patch_state_and_shortlist()
_patch_day_shortlist_selection()
