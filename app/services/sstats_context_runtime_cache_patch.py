from __future__ import annotations

import asyncio
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.schemas import MatchContext

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / ".data" / "cache" / "day_inventory" / "sstats_games"
_INSTALLED = False
_ORIGINAL_FETCH_WINDOW = None


def _cache_path(window_from: str, window_to: str) -> Path:
    return CACHE_DIR / f"{window_from}_{window_to}.json"


def _read_cache(path: Path) -> tuple[datetime | None, list[dict[str, Any]]]:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None, []
    if not isinstance(payload, dict):
        return None, []
    try:
        fetched = datetime.fromisoformat(str(payload.get("fetched_at_utc") or "").replace("Z", "+00:00"))
        if fetched.tzinfo is None:
            fetched = fetched.replace(tzinfo=UTC)
        fetched = fetched.astimezone(UTC)
    except Exception:
        fetched = None
    rows = payload.get("rows")
    return fetched, [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _write_cache(path: Path, rows: list[dict[str, Any]]) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(
                {"fetched_at_utc": datetime.now(UTC).isoformat(), "rows": rows},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
    except Exception:
        return


def _fresh_enough(fetched: datetime | None, window_to: str) -> bool:
    if fetched is None:
        return False
    try:
        closed = datetime.fromisoformat(window_to).date() < datetime.now(UTC).date()
    except Exception:
        closed = False
    minutes = 24 * 60 if closed else 15
    return datetime.now(UTC) - fetched <= timedelta(minutes=minutes)


async def _fetch_rows(
    self: Any,
    client: Any,
    from_date: str,
    to_date: str,
    stats: dict[str, Any],
) -> list[dict[str, Any]]:
    assert callable(_ORIGINAL_FETCH_WINDOW)
    chunk_days = max(1, int(getattr(self.settings, "sstats_request_chunk_days", 7) or 7))
    windows = list(self._date_windows(from_date, to_date, chunk_days))
    concurrency = max(1, min(4, int(float(os.getenv("SSTATS_HISTORY_WINDOW_CONCURRENCY") or 3))))
    deadline = max(5.0, float(os.getenv("SSTATS_HISTORY_WINDOW_DEADLINE_SECONDS") or 22.0))
    semaphore = asyncio.Semaphore(concurrency)
    stats["history_window_concurrency"] = concurrency
    stats["history_window_deadline_seconds"] = deadline
    stats.setdefault("history_window_cache_hits", 0)
    stats.setdefault("history_window_stale_cache_hits", 0)
    stats.setdefault("history_window_timeouts", 0)

    async def fetch_window(window_from: str, window_to: str) -> list[dict[str, Any]]:
        path = _cache_path(window_from, window_to)
        fetched, cached = _read_cache(path)
        if cached and _fresh_enough(fetched, window_to):
            stats["history_window_cache_hits"] += 1
            return cached
        async with semaphore:
            stats["chunk_windows_requested"] = int(stats.get("chunk_windows_requested", 0) or 0) + 1
            try:
                rows = await asyncio.wait_for(
                    _ORIGINAL_FETCH_WINDOW(self, client, window_from, window_to, stats),
                    timeout=deadline,
                )
            except TimeoutError:
                stats["history_window_timeouts"] += 1
                if cached:
                    stats["history_window_stale_cache_hits"] += 1
                    return cached
                return []
        if rows:
            _write_cache(path, rows)
        return list(rows or cached or [])

    batches = await asyncio.gather(*(fetch_window(start, end) for start, end in windows))
    rows: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    for batch in batches:
        for row in batch:
            signature = (
                row.get("id"),
                row.get("flashId"),
                row.get("date"),
                self._extract_team_name(row, "home"),
                self._extract_team_name(row, "away"),
            )
            if signature in seen:
                continue
            seen.add(signature)
            rows.append(row)
    stats["history_rows_after_cache_merge"] = len(rows)
    return rows


async def _skip_internal_bzzoiro(
    self: Any,
    client: Any,
    matches: list[Any],
) -> tuple[dict[str, MatchContext], dict[str, Any], list[dict[str, Any]]]:
    del self, client, matches
    return {}, {
        "requests": 0,
        "response_errors": 0,
        "events_fetched": 0,
        "contexts_built": 0,
        "matched_exact": 0,
        "matched_loose": 0,
        "matched_fuzzy": 0,
        "unmatched_rows": 0,
        "skipped_reason": "separate_bzzoiro_provider_is_active",
    }, []


def install() -> dict[str, Any]:
    global _INSTALLED, _ORIGINAL_FETCH_WINDOW
    if _INSTALLED:
        return {"status": "already_installed"}
    from app.providers.sstats import SStatsContextProvider

    _ORIGINAL_FETCH_WINDOW = SStatsContextProvider._fetch_rows_window
    SStatsContextProvider._fetch_rows = _fetch_rows
    SStatsContextProvider._fetch_bzzoiro_contexts = _skip_internal_bzzoiro
    _INSTALLED = True
    return {
        "status": "installed",
        "persistent_cache": str(CACHE_DIR),
        "parallel_history_windows": True,
        "window_deadline_seconds": 22.0,
        "internal_bzzoiro_disabled": True,
        "separate_bzzoiro_provider_remains_enabled": True,
        "publication_contract_relaxed": False,
    }


__all__ = ["install"]
