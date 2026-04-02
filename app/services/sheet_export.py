from __future__ import annotations

import csv
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


class SheetExportService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.sheet_id = os.getenv("SHEET_ID") or getattr(settings, "sheet_id", None)
        default_dir = Path(getattr(settings, "state_path", ".data/state.json")).parent
        self.json_path = Path(os.getenv("SHEET_EXPORT_JSON_PATH") or default_dir / "sheet-export.json")
        self.csv_path = Path(os.getenv("SHEET_EXPORT_CSV_PATH") or default_dir / "sheet-export.csv")

    def write(self, candidates: list[Any]) -> dict[str, Any]:
        rows = [self._row_for_candidate(item) for item in candidates]
        self.json_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "created_at": datetime.now(UTC).isoformat(),
            "sheet_id": self.sheet_id,
            "headers": SHEET_HEADERS,
            "rows": rows,
        }
        self.json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        with self.csv_path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(SHEET_HEADERS)
            writer.writerows([[row.get(col, "") for col in SHEET_HEADERS] for row in rows])
        return {
            "rows": len(rows),
            "json_path": str(self.json_path),
            "csv_path": str(self.csv_path),
            "sheet_id_present": bool(self.sheet_id),
            "apps_script_sync_required": True,
        }

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
