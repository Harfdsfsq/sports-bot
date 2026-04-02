from __future__ import annotations

import csv
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings


SHEET_HEADERS = [
    "Вид спорта",
    "Дата матча UTC",
    "Лига",
    "Матч",
    "Рынок",
    "Исход",
    "Линия",
    "Коэффициент",
    "БК",
    "Источник коэффициента",
    "Вероятность модели %",
    "Скорр. вероятность %",
    "Импл. вероятность %",
    "Edge %",
    "EV %",
    "Confidence",
    "Книг",
    "Источников",
    "Режим модели",
    "Score публикации",
    "xG / модель",
    "Факторы",
]

MATCH_HEADERS = [
    "match_key",
    "sport",
    "league",
    "home",
    "away",
    "commence_time_utc",
]


class SheetExportService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sheet_id = os.getenv("SHEET_ID") or getattr(settings, "sheet_id", None)
        self.webhook_url = (os.getenv("GOOGLE_SHEETS_WEBHOOK_URL") or "").strip()
        self.webhook_token = (os.getenv("GOOGLE_SHEETS_WEBHOOK_TOKEN") or "").strip()
        default_dir = Path(getattr(settings, "state_path", ".data/state.json")).parent
        self.json_path = Path(os.getenv("SHEET_EXPORT_JSON_PATH") or default_dir / "sheet-export.json")
        self.csv_path = Path(os.getenv("SHEET_EXPORT_CSV_PATH") or default_dir / "sheet-export.csv")
        self.summary_json_path = Path(os.getenv("SHEET_RUN_SUMMARY_PATH") or default_dir / "sheet-run-summary.json")
        self.picks_csv_path = Path(os.getenv("SHEET_PICKS_CSV_PATH") or default_dir / "sheet-picks.csv")
        self.matches_csv_path = Path(os.getenv("SHEET_MATCHES_CSV_PATH") or default_dir / "sheet-matches.csv")

    def write(self, candidates: list[Any], *, matches: list[Any] | None = None, summary: dict[str, Any] | None = None) -> dict[str, Any]:
        rows = [self._row_for_candidate(item) for item in candidates]
        match_rows = [self._row_for_match(item) for item in (matches or [])]
        self.json_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "created_at": datetime.now(UTC).isoformat(),
            "sheet_id": self.sheet_id,
            "headers": SHEET_HEADERS,
            "rows": rows,
            "match_headers": MATCH_HEADERS,
            "matches": match_rows,
            "summary": summary or {},
        }
        self.json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_csv(self.csv_path, SHEET_HEADERS, rows)
        self._write_csv(self.picks_csv_path, SHEET_HEADERS, rows)
        self._write_csv(self.matches_csv_path, MATCH_HEADERS, match_rows)
        self.summary_json_path.write_text(json.dumps(summary or {}, ensure_ascii=False, indent=2), encoding="utf-8")

        webhook_result = self._push_webhook(payload)
        return {
            "rows": len(rows),
            "json_path": str(self.json_path),
            "csv_path": str(self.csv_path),
            "summary_json_path": str(self.summary_json_path),
            "picks_csv_path": str(self.picks_csv_path),
            "matches_csv_path": str(self.matches_csv_path),
            "sheet_id_present": bool(self.sheet_id),
            "apps_script_sync_required": not bool(self.webhook_url),
            "webhook_configured": bool(self.webhook_url),
            "webhook_synced": webhook_result.get("ok", False),
            "webhook_status": webhook_result.get("status"),
            "webhook_error": webhook_result.get("error"),
        }

    def _push_webhook(self, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.webhook_url:
            return {"ok": False, "status": None, "error": None}
        body = dict(payload)
        if self.webhook_token:
            body["token"] = self.webhook_token
        try:
            with httpx.Client(timeout=20.0) as client:
                response = client.post(self.webhook_url, json=body)
            ok = 200 <= response.status_code < 300
            return {
                "ok": ok,
                "status": response.status_code,
                "error": None if ok else response.text[:300],
            }
        except Exception as exc:
            return {"ok": False, "status": None, "error": f"{type(exc).__name__}: {exc}"}

    def _row_for_candidate(self, bet: Any) -> dict[str, Any]:
        point = getattr(bet, "point", None)
        xg = ""
        if getattr(bet, "expected_home", None) is not None or getattr(bet, "expected_away", None) is not None:
            xg = f"{self._fmt(getattr(bet, 'expected_home', None))} : {self._fmt(getattr(bet, 'expected_away', None))}"
        mode = ""
        if isinstance(getattr(bet, "details", None), dict):
            mode = str((bet.details or {}).get("mode") or "")
        factors = "; ".join(str(x) for x in (getattr(bet, "reasons", None) or [])[:6])
        target_book = ""
        source_summary = getattr(bet, "source_summary", None) or {}
        if isinstance(source_summary, dict):
            target_book = str(source_summary.get("target_bookmaker") or "")
        return {
            "Вид спорта": getattr(bet, "sport_key", ""),
            "Дата матча UTC": getattr(getattr(bet, "commence_time", None), "strftime", lambda _f: "")("%d.%m.%Y %H:%M") if getattr(bet, "commence_time", None) else "",
            "Лига": getattr(bet, "league_name", ""),
            "Матч": f"{getattr(bet, 'home_team', '')} - {getattr(bet, 'away_team', '')}",
            "Рынок": getattr(bet, "family", ""),
            "Исход": getattr(bet, "selection", ""),
            "Линия": "" if point is None else point,
            "Коэффициент": self._fmt(getattr(bet, "odds", None)),
            "БК": target_book,
            "Источник коэффициента": ", ".join(source_summary.get("sources", [])[:4]) if isinstance(source_summary, dict) else "",
            "Вероятность модели %": self._pct(getattr(bet, "model_probability", None)),
            "Скорр. вероятность %": self._pct(getattr(bet, "adjusted_probability", None)),
            "Импл. вероятность %": self._pct(getattr(bet, "implied_probability", None)),
            "Edge %": self._fmt(getattr(bet, "edge_pct", None)),
            "EV %": self._fmt(getattr(bet, "ev_pct", None)),
            "Confidence": self._fmt(getattr(bet, "confidence", None)),
            "Книг": getattr(bet, "books_count", ""),
            "Источников": getattr(bet, "sources_count", ""),
            "Режим модели": mode or getattr(bet, "model_mode", ""),
            "Score публикации": self._fmt(getattr(bet, "publication_score", None)),
            "xG / модель": xg,
            "Факторы": factors,
        }

    def _row_for_match(self, match: Any) -> dict[str, Any]:
        commence_time = getattr(match, "commence_time", None)
        return {
            "match_key": getattr(match, "match_key", ""),
            "sport": getattr(match, "sport_key", ""),
            "league": getattr(match, "league_name", ""),
            "home": getattr(match, "home_team", ""),
            "away": getattr(match, "away_team", ""),
            "commence_time_utc": commence_time.isoformat() if commence_time else "",
        }

    @staticmethod
    def _write_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows([[row.get(col, "") for col in headers] for row in rows])

    @staticmethod
    def _fmt(value: Any) -> str:
        try:
            if value is None or value == "":
                return ""
            return f"{float(value):.2f}"
        except Exception:
            return str(value or "")

    @staticmethod
    def _pct(value: Any) -> str:
        try:
            if value is None or value == "":
                return ""
            value = float(value)
            if value <= 1.0:
                value *= 100.0
            return f"{value:.2f}"
        except Exception:
            return ""
