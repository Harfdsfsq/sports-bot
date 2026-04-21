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
