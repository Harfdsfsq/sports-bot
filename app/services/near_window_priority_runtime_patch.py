from __future__ import annotations

"""Near-window prioritization for match context enrichment.

The bot can have good whole-day odds coverage while still missing context for
matches starting soon. This runtime patch keeps publication quality guards intact
and only changes ordering/targeting: matches with odds in the near kickoff window
are pushed to the front of runner/provider context queues.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

UTC = timezone.utc
PATCH_MARKER = "_harizon_near_window_priority_patch_v1"
REPORT_PATH = Path(".data/exports/latest-near-window-priority-patch.json")
REMOVED_PROVIDERS = {"bookies_api", "api_football", "oddspapi"}


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        if raw is None or str(raw).strip() == "":
            return default
        return int(float(str(raw)))
    except Exception:
        return default


def _now() -> datetime:
    return datetime.now(UTC)


def _ensure_utc(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            text = str(value or "").strip()
            if not text:
                return None
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
        except Exception:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _match_key(match: Any) -> str:
    try:
        key = getattr(match, "match_key")
        if key:
            return str(key)
    except Exception:
        pass
    return ""


def _minutes_to_kickoff(match: Any, now_utc: datetime) -> float:
    dt = _ensure_utc(getattr(match, "commence_time", None))
    if dt is None:
        return 999999.0
    return (dt - now_utc).total_seconds() / 60.0


def _has_offers(match: Any, offers_by_match: dict[str, Any] | None) -> bool:
    if not offers_by_match:
        return False
    key = _match_key(match)
    if not key:
        return False
    offers = offers_by_match.get(key)
    try:
        return len(offers or []) > 0
    except Exception:
        return bool(offers)


def _book_count(match: Any, offers_by_match: dict[str, Any] | None) -> int:
    if not offers_by_match:
        return 0
    offers = offers_by_match.get(_match_key(match)) or []
    books: set[str] = set()
    try:
        for offer in offers:
            bookmaker = getattr(offer, "bookmaker", None)
            if bookmaker is None and isinstance(offer, dict):
                bookmaker = offer.get("bookmaker")
            if bookmaker:
                books.add(str(bookmaker).strip().lower())
    except Exception:
        return 1 if offers else 0
    return len(books)


def _near_rank(match: Any, now_utc: datetime, offers_by_match: dict[str, Any] | None = None) -> tuple[int, float, int, str]:
    minutes = _minutes_to_kickoff(match, now_utc)
    min_lead = _env_int("NEAR_WINDOW_PRIORITY_MIN_LEAD_MINUTES", _env_int("MIN_KICKOFF_LEAD_MINUTES", 25))
    near_hours = _env_int("NEAR_WINDOW_CONTEXT_HOURS", _env_int("DAY_INVENTORY_NEAR_WINDOW_HOURS", 12))
    near_max = near_hours * 60
    final_max = _env_int("CANDIDATE_RECHECK_FINAL_WINDOW_MINUTES", 90)
    has_offers = _has_offers(match, offers_by_match)
    books = _book_count(match, offers_by_match)
    if min_lead <= minutes <= final_max and has_offers:
        bucket = 0
    elif min_lead <= minutes <= near_max and has_offers:
        bucket = 1
    elif min_lead <= minutes <= near_max:
        bucket = 2
    elif has_offers:
        bucket = 3
    else:
        bucket = 4
    # More books should win ties, then earlier kickoff.
    return (bucket, minutes, -books, _match_key(match))


def _dedupe_keep_order(matches: Iterable[Any]) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for match in matches:
        key = _match_key(match) or f"id:{id(match)}"
        if key in seen:
            continue
        seen.add(key)
        out.append(match)
    return out


def _prioritize(matches: Iterable[Any], now_utc: datetime, offers_by_match: dict[str, Any] | None = None, *, limit: int | None = None) -> list[Any]:
    items = _dedupe_keep_order(matches)
    items.sort(key=lambda match: _near_rank(match, now_utc, offers_by_match))
    if limit is not None and limit >= 0:
        return items[:limit]
    return items


def _near_matches(matches: Iterable[Any], now_utc: datetime, offers_by_match: dict[str, Any] | None = None) -> list[Any]:
    min_lead = _env_int("NEAR_WINDOW_PRIORITY_MIN_LEAD_MINUTES", _env_int("MIN_KICKOFF_LEAD_MINUTES", 25))
    near_hours = _env_int("NEAR_WINDOW_CONTEXT_HOURS", _env_int("DAY_INVENTORY_NEAR_WINDOW_HOURS", 12))
    near_max = near_hours * 60
    rows = []
    for match in matches:
        minutes = _minutes_to_kickoff(match, now_utc)
        if min_lead <= minutes <= near_max:
            rows.append(match)
    return _prioritize(rows, now_utc, offers_by_match)


def _write_report(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _patch_runner() -> bool:
    try:
        from app.services import runner as runner_module
    except Exception:
        return False
    cls = getattr(runner_module, "PredictionRunner", None)
    if cls is None or getattr(cls, PATCH_MARKER, False):
        return False

    original_filter_matches = getattr(cls, "_filter_matches", None)
    if callable(original_filter_matches):
        def filter_matches_patched(self, matches: Any, now_utc: datetime):
            filtered, meta = original_filter_matches(self, matches, now_utc)
            if not _truthy(os.getenv("NEAR_WINDOW_CONTEXT_PRIORITY_ENABLED"), True):
                return filtered, meta
            ordered = _prioritize(filtered or [], now_utc, None)
            try:
                if isinstance(meta, dict):
                    meta = dict(meta)
                    meta["near_window_priority_filter_patch"] = True
                    meta["near_window_priority_first_keys"] = [_match_key(item) for item in ordered[:10]]
            except Exception:
                pass
            return ordered, meta
        cls._filter_matches = filter_matches_patched

    original_select_context = getattr(cls, "_select_context_enrichment_matches", None)
    if callable(original_select_context):
        def select_context_patched(self, matches: Any, offers_by_match: dict[str, Any], now_utc: datetime, market_signals: dict[str, Any] | None = None):
            selected, meta = original_select_context(self, matches, offers_by_match, now_utc, market_signals)
            if not _truthy(os.getenv("NEAR_WINDOW_CONTEXT_PRIORITY_ENABLED"), True):
                return selected, meta
            near = _near_matches(matches or [], now_utc, offers_by_match)
            min_near = _env_int("NEAR_WINDOW_CONTEXT_MIN_MATCHES", 48)
            overall_limit = _env_int("CONTEXT_ENRICHMENT_MATCH_LIMIT", 220)
            combined = _dedupe_keep_order(list(near[:max(0, min_near)]) + list(selected or []) + list(near) + list(matches or []))
            combined = _prioritize(combined, now_utc, offers_by_match, limit=overall_limit)
            near_keys = {_match_key(item) for item in near}
            selected_near = sum(1 for item in combined if _match_key(item) in near_keys)
            patched_meta = dict(meta or {}) if isinstance(meta, dict) else {"original_meta": meta}
            patched_meta.update({
                "near_window_priority_patch": True,
                "near_window_matches_available": len(near),
                "near_window_matches_selected": selected_near,
                "near_window_context_min_matches": min_near,
                "context_target_count_after_patch": len(combined),
                "near_window_first_keys": [_match_key(item) for item in combined[:12]],
            })
            _write_report({
                "created_at_utc": _now().isoformat(),
                "stage": "select_context_enrichment_matches",
                "near_window_matches_available": len(near),
                "near_window_matches_selected": selected_near,
                "context_target_count_after_patch": len(combined),
                "first_matches": [
                    {
                        "match_key": _match_key(item),
                        "home_team": getattr(item, "home_team", ""),
                        "away_team": getattr(item, "away_team", ""),
                        "league_name": getattr(item, "league_name", ""),
                        "minutes_to_kickoff": round(_minutes_to_kickoff(item, now_utc), 2),
                        "books": _book_count(item, offers_by_match),
                    }
                    for item in combined[:20]
                ],
            })
            return combined, patched_meta
        cls._select_context_enrichment_matches = select_context_patched

    original_select_provider = getattr(cls, "_select_provider_context_matches", None)
    if callable(original_select_provider):
        def select_provider_context_patched(self, context_target_matches: Any, provider_key: str, fallback_matches: Any = None, offers_by_match: dict[str, Any] | None = None):
            selected = original_select_provider(self, context_target_matches, provider_key, fallback_matches=fallback_matches, offers_by_match=offers_by_match)
            if not _truthy(os.getenv("NEAR_WINDOW_CONTEXT_PRIORITY_ENABLED"), True):
                return selected
            key = str(provider_key or "").strip().lower()
            if key in REMOVED_PROVIDERS:
                return []
            now_utc = _now()
            fallback = list(fallback_matches or [])
            near = _near_matches(fallback, now_utc, offers_by_match)
            limit_name = f"{key.upper()}_CONTEXT_MATCH_LIMIT"
            provider_limit = _env_int(limit_name, len(selected or []) or _env_int("CONTEXT_ENRICHMENT_MATCH_LIMIT", 220))
            provider_min = _env_int("NEAR_WINDOW_PROVIDER_CONTEXT_MIN_MATCHES", min(36, provider_limit if provider_limit > 0 else 36))
            combined = _dedupe_keep_order(list(near[:max(0, provider_min)]) + list(selected or []) + list(near))
            return _prioritize(combined, now_utc, offers_by_match, limit=provider_limit if provider_limit > 0 else None)
        cls._select_provider_context_matches = select_provider_context_patched

    setattr(cls, PATCH_MARKER, True)
    return True


def install() -> bool:
    if not _truthy(os.getenv("NEAR_WINDOW_CONTEXT_PRIORITY_ENABLED"), True):
        return False
    return _patch_runner()
