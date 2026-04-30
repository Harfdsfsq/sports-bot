from __future__ import annotations

import json
from dataclasses import asdict, is_dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.schemas import Match

UTC = timezone.utc


class DayInventoryStore:
    def __init__(
        self,
        base_dir: str | Path = ".data/day_inventory",
        summary_path: str | Path = ".data/exports/latest-day-inventory-summary.json",
        timezone_name: str = "Europe/Moscow",
    ) -> None:
        self.base_dir = Path(base_dir)
        self.summary_path = Path(summary_path)
        self.timezone_name = str(timezone_name or "Europe/Moscow")
        try:
            self.tzinfo = ZoneInfo(self.timezone_name)
        except Exception:
            self.tzinfo = UTC
        self.base_dir.mkdir(parents=True, exist_ok=True)
        self.summary_path.parent.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _load_json(path: Path, default: Any) -> Any:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return default

    @staticmethod
    def _write_json(path: Path, payload: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    def _date_file(self, local_date: str) -> Path:
        return self.base_dir / f"{local_date}.json"

    def _latest_file(self) -> Path:
        return self.base_dir / "latest.json"

    def _current_file(self) -> Path:
        return self.base_dir / "current.json"

    def _today_file(self) -> Path:
        return self.base_dir / "today.json"

    def local_date_for_dt(self, value: datetime) -> str:
        return value.astimezone(self.tzinfo).date().isoformat()

    def serialize_match(self, match: Match) -> dict[str, Any]:
        payload = asdict(match) if is_dataclass(match) else dict(match)
        commence_time = match.commence_time if isinstance(match.commence_time, datetime) else None
        source_ids = {str(match.source): str(match.source_event_id)} if match.source else {}
        metadata = dict(payload.get("metadata") or {}) if isinstance(payload.get("metadata"), dict) else {}
        local_date = self.local_date_for_dt(commence_time) if commence_time is not None else ""
        priority = self._priority_score(match)
        return {
            "canonical_match_id": str(match.match_key),
            "match_key": str(match.match_key),
            "loose_key": str(match.loose_key),
            "date_local": local_date,
            "kickoff_utc": commence_time.astimezone(UTC).isoformat() if commence_time is not None else None,
            "kickoff_local": commence_time.astimezone(self.tzinfo).isoformat() if commence_time is not None else None,
            "sport_key": str(match.sport_key),
            "league_name": str(match.league_name),
            "league_key": str(match.league_key),
            "home_team": str(match.home_team),
            "away_team": str(match.away_team),
            "home_team_norm": str(match.home_team_norm),
            "away_team_norm": str(match.away_team_norm),
            "tier": str(match.tier),
            "source_ids": source_ids,
            "sources_seen": [str(match.source)] if match.source else [],
            "coverage": {
                "fixture_core": True,
                "odds": False,
                "context": False,
                "weather": False,
                "news": False,
                "xg": False,
                "form": False,
                "ready_for_model": False,
                "ready_for_publish": False,
            },
            "priority": priority,
            "last_enriched_at": None,
            "next_retry_at": None,
            "refresh": {
                "last_fixture_refresh_utc": datetime.now(UTC).isoformat(),
                "last_odds_refresh_utc": None,
                "last_context_refresh_utc": None,
            },
            "metadata": metadata,
        }

    def _priority_score(self, match: Match) -> float:
        now = datetime.now(UTC)
        try:
            hours_to_kickoff = (match.commence_time.astimezone(UTC) - now).total_seconds() / 3600.0
        except Exception:
            hours_to_kickoff = 999.0
        if hours_to_kickoff <= 6:
            score = 100.0
        elif hours_to_kickoff <= 12:
            score = 85.0
        elif hours_to_kickoff <= 24:
            score = 70.0
        else:
            score = 45.0
        league_text = str(match.league_name or "").lower()
        if any(term in league_text for term in ("premier", "championship", "serie a", "la liga", "bundesliga", "ligue 1", "eredivisie", "mls")):
            score += 10.0
        if str(match.source or "").lower() == "odds_api_io":
            score += 8.0
        if str(match.tier or "").lower() == "low":
            score -= 20.0
        return round(max(0.0, min(120.0, score)), 3)

    def build_payload(
        self,
        *,
        local_date: str,
        matches: list[Match],
        source_meta: dict[str, Any] | None = None,
        existing: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        now_utc = datetime.now(UTC).isoformat()
        existing = existing if isinstance(existing, dict) else {}
        existing_matches_raw = existing.get("matches") if isinstance(existing.get("matches"), list) else []
        existing_matches = {
            str(row.get("canonical_match_id") or row.get("match_key") or ""): dict(row)
            for row in existing_matches_raw
            if isinstance(row, dict) and str(row.get("canonical_match_id") or row.get("match_key") or "").strip()
        }

        merged: dict[str, dict[str, Any]] = dict(existing_matches)
        added = 0
        updated = 0
        leagues: dict[str, int] = {}
        source_counts: dict[str, int] = {}

        for match in matches:
            row = self.serialize_match(match)
            key = str(row["canonical_match_id"])
            current = merged.get(key)
            league_name = str(row.get("league_name") or "")
            if league_name:
                leagues[league_name] = leagues.get(league_name, 0) + 1
            provider_name = str(match.source or "")
            if provider_name:
                source_counts[provider_name] = source_counts.get(provider_name, 0) + 1
            if current is None:
                merged[key] = row
                added += 1
                continue

            new_source_ids = dict(current.get("source_ids") or {})
            new_source_ids.update(row.get("source_ids") or {})
            sources_seen = sorted({*(current.get("sources_seen") or []), *(row.get("sources_seen") or [])})
            metadata = dict(current.get("metadata") or {})
            metadata.update(row.get("metadata") or {})
            coverage = dict(current.get("coverage") or {})
            coverage.update({k: bool(v) or bool(coverage.get(k)) for k, v in (row.get("coverage") or {}).items()})
            refresh = dict(current.get("refresh") or {})
            refresh["last_fixture_refresh_utc"] = now_utc
            priority = max(float(current.get("priority") or 0.0), float(row.get("priority") or 0.0))

            current.update({
                "kickoff_utc": row.get("kickoff_utc") or current.get("kickoff_utc"),
                "kickoff_local": row.get("kickoff_local") or current.get("kickoff_local"),
                "league_name": row.get("league_name") or current.get("league_name"),
                "league_key": row.get("league_key") or current.get("league_key"),
                "home_team": row.get("home_team") or current.get("home_team"),
                "away_team": row.get("away_team") or current.get("away_team"),
                "home_team_norm": row.get("home_team_norm") or current.get("home_team_norm"),
                "away_team_norm": row.get("away_team_norm") or current.get("away_team_norm"),
                "tier": row.get("tier") or current.get("tier"),
                "source_ids": new_source_ids,
                "sources_seen": sources_seen,
                "coverage": coverage,
                "priority": round(priority, 3),
                "last_enriched_at": current.get("last_enriched_at") or row.get("last_enriched_at"),
                "next_retry_at": current.get("next_retry_at") or row.get("next_retry_at"),
                "refresh": refresh,
                "metadata": metadata,
            })
            merged[key] = current
            updated += 1

        sorted_matches = sorted(
            merged.values(),
            key=lambda item: (
                str(item.get("kickoff_utc") or ""),
                str(item.get("league_name") or ""),
                str(item.get("home_team") or ""),
            ),
        )

        coverage_counts = self._coverage_counts(sorted_matches)

        payload = {
            "date_local": local_date,
            "timezone": self.timezone_name,
            "created_at_utc": str(existing.get("created_at_utc") or now_utc),
            "updated_at_utc": now_utc,
            "build_status": "ok",
            "sources": source_meta or {},
            "counts": {
                "matches_total": len(sorted_matches),
                "matches_added": added,
                "matches_updated": updated,
                "providers_seen": len(source_counts),
                "leagues_seen": len(leagues),
                **coverage_counts,
            },
            "source_match_counts": source_counts,
            "league_match_counts": dict(sorted(leagues.items(), key=lambda item: (-item[1], item[0]))[:50]),
            "matches": sorted_matches,
        }
        return payload

    def _coverage_counts(self, rows: list[dict[str, Any]]) -> dict[str, int]:
        now = datetime.now(UTC)
        counts = {
            "matches_with_odds": 0,
            "matches_with_context": 0,
            "matches_with_weather": 0,
            "matches_with_news": 0,
            "matches_with_xg": 0,
            "matches_with_form": 0,
            "matches_ready_for_model": 0,
            "matches_ready_for_publish": 0,
            "matches_next_6h": 0,
            "matches_next_6h_ready": 0,
            "matches_next_12h": 0,
            "matches_next_12h_ready": 0,
        }
        for row in rows:
            coverage = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
            if bool(coverage.get("odds")):
                counts["matches_with_odds"] += 1
            if bool(coverage.get("context")):
                counts["matches_with_context"] += 1
            if bool(coverage.get("weather")):
                counts["matches_with_weather"] += 1
            if bool(coverage.get("news")):
                counts["matches_with_news"] += 1
            if bool(coverage.get("xg")):
                counts["matches_with_xg"] += 1
            if bool(coverage.get("form")):
                counts["matches_with_form"] += 1
            if bool(coverage.get("ready_for_model")):
                counts["matches_ready_for_model"] += 1
            if bool(coverage.get("ready_for_publish")):
                counts["matches_ready_for_publish"] += 1
            kickoff = self._parse_dt(row.get("kickoff_utc"))
            if kickoff is None:
                continue
            hours = (kickoff - now).total_seconds() / 3600.0
            ready = bool(coverage.get("ready_for_model"))
            if 0 <= hours <= 6:
                counts["matches_next_6h"] += 1
                if ready:
                    counts["matches_next_6h_ready"] += 1
            if 0 <= hours <= 12:
                counts["matches_next_12h"] += 1
                if ready:
                    counts["matches_next_12h_ready"] += 1
        return counts

    @staticmethod
    def _parse_dt(value: Any) -> datetime | None:
        try:
            if value in (None, ""):
                return None
            text = str(value)
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            dt = datetime.fromisoformat(text)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt.astimezone(UTC)
        except Exception:
            return None

    def write_summary(self, summary: dict[str, Any]) -> str:
        self._write_json(self.summary_path, summary)
        return str(self.summary_path)

    def save_failure_summary(
        self,
        *,
        local_date: str,
        error_text: str,
        source_meta: dict[str, Any] | None = None,
        bootstrap_provider: str | None = None,
    ) -> str:
        payload = {
            "date_local": str(local_date),
            "updated_at_utc": datetime.now(UTC).isoformat(),
            "timezone": self.timezone_name,
            "build_status": "error",
            "bootstrap_provider": str(bootstrap_provider or ""),
            "error": str(error_text or "unknown error"),
            "counts": {
                "matches_total": 0,
                "matches_added": 0,
                "matches_updated": 0,
                "providers_seen": 0,
                "leagues_seen": 0,
            },
            "source_match_counts": {},
            "league_match_counts": {},
            "sources": source_meta or {},
        }
        return self.write_summary(payload)

    def save_inventory(self, payload: dict[str, Any]) -> dict[str, str]:
        local_date = str(payload.get("date_local") or date.today().isoformat())
        date_path = self._date_file(local_date)
        latest_path = self._latest_file()
        current_path = self._current_file()
        today_path = self._today_file()
        self._write_json(date_path, payload)
        self._write_json(latest_path, payload)
        self._write_json(current_path, payload)
        self._write_json(today_path, payload)
        summary = {
            "date_local": local_date,
            "updated_at_utc": payload.get("updated_at_utc"),
            "timezone": payload.get("timezone"),
            "build_status": payload.get("build_status") or "ok",
            "counts": dict(payload.get("counts") or {}),
            "source_match_counts": dict(payload.get("source_match_counts") or {}),
            "league_match_counts": dict(payload.get("league_match_counts") or {}),
            "sources": dict(payload.get("sources") or {}),
        }
        self._write_json(self.summary_path, summary)
        return {
            "date_path": str(date_path),
            "latest_path": str(latest_path),
            "current_path": str(current_path),
            "today_path": str(today_path),
            "summary_path": str(self.summary_path),
        }

    def load_inventory(self, local_date: str) -> dict[str, Any]:
        return self._load_json(self._date_file(local_date), {})
