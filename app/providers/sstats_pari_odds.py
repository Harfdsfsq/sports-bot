"""Independent prematch odds source backed by SStats Pari endpoints."""

from __future__ import annotations

import asyncio
import contextlib
import json
import os
from collections import defaultdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from app.config import Settings
from app.providers.sstats_pari_parser import (
    event_id,
    extract_list,
    extract_odds,
    league_name,
    parse_dt,
    parse_offers,
    team_name,
    total_count,
)
from app.schemas import Match, Offer
from app.utils import canonicalize_team_name, score_event_match

ROOT = Path(__file__).resolve().parents[2]
CACHE_DIR = ROOT / ".data" / "cache" / "sstats_pari"
EXPORT = ROOT / ".data" / "exports" / "latest-sstats-pari-odds.json"


def _truthy(value: Any, default: bool = False) -> bool:
    raw = str(value if value is not None else "").strip().lower()
    return default if not raw else raw in {"1", "true", "yes", "on", "force"}


def _load(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def _app_timezone() -> ZoneInfo:
    try:
        return ZoneInfo(os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow")
    except Exception:
        return ZoneInfo("Europe/Moscow")


class SStatsPariOddsProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.api_key = str(
            getattr(settings, "sstats_api_key", None) or os.getenv("SSTATS_API_KEY") or ""
        ).strip()
        self.base_url = str(os.getenv("SSTATS_BASE_URL") or "https://api.sstats.net").rstrip("/")
        self.timeout = max(3.0, float(os.getenv("SSTATS_PARI_TIMEOUT_SECONDS") or 7.0))
        self.concurrency = max(
            1, min(24, int(float(os.getenv("SSTATS_PARI_CONCURRENCY") or 12)))
        )
        self.detail_limit = max(
            0, int(float(os.getenv("SSTATS_PARI_DETAIL_MATCH_LIMIT") or 150))
        )
        self.enabled = _truthy(os.getenv("SSTATS_PARI_ODDS_ENABLED"), True)

    async def fetch_offers(
        self, matches: list[Match]
    ) -> tuple[dict[str, list[Offer]], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": self.enabled,
            "api_key_present": bool(self.api_key),
            "requests": 0,
            "list_requests": 0,
            "detail_requests": 0,
            "cache_hits": 0,
            "response_errors": 0,
            "rows_fetched": 0,
            "events_matched": 0,
            "details_fetched": 0,
            "offer_matches": 0,
            "offers_parsed": 0,
            "detail_limit": self.detail_limit,
            "concurrency": self.concurrency,
            "source": "sstats_pari",
            "independent_from_odds_api_io": True,
            "cross_midnight_matching": True,
        }
        preview: dict[str, Any] = {"matched": [], "unmatched": [], "samples": []}
        soccer = [match for match in matches if getattr(match, "sport_key", "") == "soccer"]
        if not self.enabled or not self.api_key or not soccer or self.detail_limit <= 0:
            self._export(stats, preview)
            return {}, stats, preview
        dates = sorted(
            {
                match.commence_time.astimezone(UTC).date().isoformat()
                for match in soccer
            }
            | {
                match.commence_time.astimezone(_app_timezone()).date().isoformat()
                for match in soccer
            }
        )[:4]
        async with httpx.AsyncClient(
            timeout=self.timeout,
            follow_redirects=True,
            headers={"User-Agent": "HARIZON-sstats-pari/1.0"},
        ) as client:
            events: list[dict[str, Any]] = []
            for date_key in dates:
                events.extend(await self._fetch_day(client, date_key, stats))
            stats["rows_fetched"] = len(events)
            mapping = self._match_events(soccer, events, stats, preview)
            selected = sorted(
                mapping.values(),
                key=lambda item: (item[0].commence_time, -item[2], item[0].match_key),
            )[: self.detail_limit]
            semaphore = asyncio.Semaphore(self.concurrency)

            async def fetch_one(
                item: tuple[Match, dict[str, Any], float, str],
            ) -> tuple[Match, dict[str, Any], Any]:
                match, event, _score, _quality = item
                source_id = str(event_id(event) or "").strip()
                if not source_id:
                    return match, event, None
                async with semaphore:
                    return match, event, await self._fetch_detail(
                        client, source_id, event, stats
                    )

            results = await asyncio.gather(*(fetch_one(item) for item in selected))
        offers_by_match: dict[str, list[Offer]] = defaultdict(list)
        for match, event, payload in results:
            source_id = str(event_id(event) or "")
            parsed = parse_offers(extract_odds(payload), match, source_id)
            if not parsed:
                continue
            offers_by_match[match.match_key].extend(parsed)
            stats["offer_matches"] += 1
            stats["offers_parsed"] += len(parsed)
            if len(preview["samples"]) < 8:
                preview["samples"].append(
                    {
                        "match_key": match.match_key,
                        "event_id": source_id,
                        "offers": [
                            {
                                "family": offer.family,
                                "selection": offer.selection,
                                "point": offer.point,
                                "price": offer.price,
                            }
                            for offer in parsed[:8]
                        ],
                    }
                )
        result = {key: value for key, value in offers_by_match.items() if value}
        self._export(stats, preview)
        return result, stats, preview

    def supports_match(self, match: Match) -> bool:
        return getattr(match, "sport_key", "") == "soccer"

    async def _fetch_day(
        self, client: httpx.AsyncClient, date_key: str, stats: dict[str, Any]
    ) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        offset, limit = 0, 1000
        for _ in range(3):
            payload = await self._get_json(
                client,
                "/Pari/matches",
                {"date": date_key, "offset": offset, "limit": limit},
                stats,
            )
            stats["list_requests"] += 1
            page = extract_list(payload)
            rows.extend(page)
            if (
                not page
                or len(rows) >= total_count(payload, len(rows))
                or len(page) < limit
            ):
                break
            offset += len(page)
        return rows

    async def _fetch_detail(
        self,
        client: httpx.AsyncClient,
        source_id: str,
        event: dict[str, Any],
        stats: dict[str, Any],
    ) -> Any:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        path = CACHE_DIR / f"{source_id}.json"
        version = str(event.get("lastUpdate") or event.get("last_update") or "")
        cached = _load(path)
        if isinstance(cached, dict):
            fetched = parse_dt(cached.get("fetched_at_utc"))
            if (version and cached.get("last_update") == version) or (
                fetched and datetime.now(UTC) - fetched <= timedelta(minutes=15)
            ):
                stats["cache_hits"] += 1
                return cached.get("payload")
        payload = await self._get_json(
            client, f"/Pari/match/{source_id}", {}, stats
        )
        stats["detail_requests"] += 1
        if payload is not None:
            stats["details_fetched"] += 1
            _write(
                path,
                {
                    "event_id": source_id,
                    "last_update": version,
                    "fetched_at_utc": datetime.now(UTC).isoformat(),
                    "payload": payload,
                },
            )
        return payload

    async def _get_json(
        self,
        client: httpx.AsyncClient,
        path: str,
        params: dict[str, Any],
        stats: dict[str, Any],
    ) -> Any:
        stats["requests"] += 1
        try:
            response = await client.get(
                f"{self.base_url}{path}", params={**params, "apikey": self.api_key}
            )
        except Exception as exc:
            stats["response_errors"] += 1
            stats["last_error"] = f"{type(exc).__name__}: {exc}"
            return None
        stats.setdefault("http_statuses", []).append(response.status_code)
        stats["last_body_preview"] = response.text[:800]
        if response.status_code >= 400:
            stats["response_errors"] += 1
            return None
        try:
            return response.json()
        except Exception as exc:
            stats["response_errors"] += 1
            stats["last_error"] = f"json:{type(exc).__name__}: {exc}"
            return None

    @staticmethod
    def _candidate_dates(value: datetime) -> set[str]:
        utc_value = value.astimezone(UTC)
        local_value = value.astimezone(_app_timezone())
        dates = {utc_value.date(), local_value.date()}
        return {
            (item + timedelta(days=offset)).isoformat()
            for item in dates
            for offset in (-1, 0, 1)
        }

    def _match_events(
        self,
        matches: list[Match],
        events: list[dict[str, Any]],
        stats: dict[str, Any],
        preview: dict[str, Any],
    ) -> dict[str, tuple[Match, dict[str, Any], float, str]]:
        by_date: dict[str, list[Match]] = defaultdict(list)
        for match in matches:
            for date_key in self._candidate_dates(match.commence_time):
                by_date[date_key].append(match)
        mapping: dict[str, tuple[Match, dict[str, Any], float, str]] = {}
        for event in events:
            home = team_name(
                event.get("homeTeam") or event.get("home_team") or event.get("home")
            )
            away = team_name(
                event.get("awayTeam") or event.get("away_team") or event.get("away")
            )
            start = parse_dt(
                event.get("startDate") or event.get("start_time") or event.get("date")
            )
            if not home or not away or start is None:
                continue
            candidates: list[Match] = []
            seen: set[str] = set()
            for date_key in self._candidate_dates(start):
                for match in by_date.get(date_key, []):
                    if match.match_key not in seen:
                        seen.add(match.match_key)
                        candidates.append(match)
            home_tokens = set(canonicalize_team_name(home).split())
            away_tokens = set(canonicalize_team_name(away).split())
            shortlist = [
                match
                for match in candidates
                if home_tokens.intersection(
                    canonicalize_team_name(match.home_team).split()
                )
                or away_tokens.intersection(
                    canonicalize_team_name(match.away_team).split()
                )
            ]
            if not shortlist:
                shortlist = candidates[:48]
            best: tuple[Match, float, str] | None = None
            for match in shortlist[:64]:
                try:
                    score, quality = score_event_match(
                        sport="soccer",
                        match_home=match.home_team,
                        match_away=match.away_team,
                        match_start=match.commence_time,
                        match_league=match.league_name,
                        event_home=home,
                        event_away=away,
                        event_start=start,
                        event_league=league_name(event),
                        exact_tolerance_hours=12,
                        fuzzy_tolerance_hours=24,
                    )
                except Exception:
                    continue
                if best is None or score > best[1]:
                    best = (match, float(score), str(quality or ""))
            if best is None or best[1] < 58.0:
                if len(preview["unmatched"]) < 12:
                    preview["unmatched"].append(
                        {
                            "home": home,
                            "away": away,
                            "start": start.isoformat(),
                            "best_score": best[1] if best else 0,
                        }
                    )
                continue
            current = mapping.get(best[0].match_key)
            if current is None or best[1] > current[2]:
                mapping[best[0].match_key] = (best[0], event, best[1], best[2])
                if len(preview["matched"]) < 12:
                    preview["matched"].append(
                        {
                            "match_key": best[0].match_key,
                            "event_id": event_id(event),
                            "score": round(best[1], 2),
                            "quality": best[2],
                        }
                    )
        stats["events_matched"] = len(mapping)
        return mapping

    @staticmethod
    def _export(stats: dict[str, Any], preview: dict[str, Any]) -> None:
        with contextlib.suppress(Exception):
            _write(
                EXPORT,
                {
                    "created_at_utc": datetime.now(UTC).isoformat(),
                    "stats": stats,
                    "preview": preview,
                    "publication_contract_relaxed": False,
                },
            )


__all__ = ["SStatsPariOddsProvider"]
