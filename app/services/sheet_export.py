from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
UTC = timezone.utc
from pathlib import Path
from typing import Any

import httpx

from app.config import Settings
from app.utils import russian_market_name, russian_selection


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
    "Источники коэффициента",
    "Вероятность модели %",
    "Скорр. вероятность %",
    "Импл. вероятность %",
    "Gap к рынку п.п.",
    "Edge %",
    "EV %",
    "Confidence",
    "Value tier",
    "Книг",
    "Источников",
    "Режим модели",
    "Derived market signal",
    "Score публикации",
    "xG / модель",
    "Причина guard/quality",
    "Факторы",
]

MATCH_HEADERS = [
    "match_key",
    "sport",
    "league",
    "home",
    "away",
    "commence_time_utc",
    "forecast_status",
    "forecast_family",
    "forecast_selection",
    "forecast_line",
    "forecast_odds",
    "forecast_bookmaker",
    "forecast_odds_source",
    "forecast_model_probability_pct",
    "forecast_adjusted_probability_pct",
    "forecast_market_probability_pct",
    "forecast_probability_gap_pct",
    "forecast_edge_pct",
    "forecast_ev_pct",
    "forecast_confidence",
    "forecast_value_tier",
    "forecast_books_count",
    "forecast_sources_count",
    "forecast_model_mode",
    "forecast_market_signal_derived",
    "forecast_shadow_tracked",
    "forecast_guard_reason",
    "forecast_publication_score",
    "forecast_expected_home",
    "forecast_expected_away",
    "forecast_total_xg",
    "forecast_context_source",
    "forecast_context_confidence",
    "forecast_market_movement",
    "forecast_quality_status",
    "forecast_quality_score",
    "forecast_quality_reasons",
    "forecast_quality_calibration",
    "forecast_reasons",
    "forecast_analysis_points",
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
        self.bet_ledger_csv_path = Path(os.getenv("SHEET_BET_LEDGER_CSV_PATH") or default_dir / "sheet-bet-ledger.csv")
        self.daily_report_csv_path = Path(os.getenv("SHEET_DAILY_REPORT_CSV_PATH") or default_dir / "sheet-daily-report.csv")
        self.daily_summary_csv_path = Path(os.getenv("SHEET_DAILY_SUMMARY_CSV_PATH") or default_dir / "sheet-daily-summary.csv")
        self.learning_csv_path = Path(os.getenv("SHEET_LEARNING_CSV_PATH") or default_dir / "sheet-learning.csv")
        self.guard_report_json_path = Path(os.getenv("SHEET_GUARD_REPORT_JSON_PATH") or default_dir / "guard-report.json")
        self.guard_report_csv_path = Path(os.getenv("SHEET_GUARD_REPORT_CSV_PATH") or default_dir / "guard-report-funnel.csv")

    def write(
        self,
        candidates: list[Any],
        *,
        matches: list[Any] | None = None,
        forecast_rows: list[dict[str, Any]] | None = None,
        bet_rows: list[dict[str, Any]] | None = None,
        daily_report: dict[str, Any] | None = None,
        quality_report: dict[str, Any] | None = None,
        summary: dict[str, Any] | None = None,
        guard_report: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rows = [self._row_for_candidate(item) for item in candidates]
        forecasts_by_match = self._forecast_rows_by_match(forecast_rows or [])
        match_rows = [self._row_for_match(item, forecasts_by_match.get(getattr(item, "match_key", ""))) for item in (matches or [])]
        ledger_rows = [dict(item) for item in (bet_rows or []) if isinstance(item, dict)]
        daily_rows = [dict(item) for item in ((daily_report or {}).get("rows") or []) if isinstance(item, dict)]
        daily_summary_rows = [dict((daily_report or {}).get("summary") or {})] if daily_report else []
        learning_rows = self._learning_rows(quality_report or {})
        guard_report = dict(guard_report or {})
        self.json_path.parent.mkdir(parents=True, exist_ok=True)

        payload = {
            "created_at": datetime.now(UTC).isoformat(),
            "sheet_id": self.sheet_id,
            "headers": SHEET_HEADERS,
            "rows": rows,
            "match_headers": MATCH_HEADERS,
            "matches": match_rows,
            "bet_ledger": ledger_rows,
            "daily_report": daily_report or {},
            "quality_report": quality_report or {},
            "guard_report": guard_report,
            "learning_rows": learning_rows,
            "summary": summary or {},
        }
        self.json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        self._write_csv(self.csv_path, SHEET_HEADERS, rows)
        self._write_csv(self.picks_csv_path, SHEET_HEADERS, rows)
        self._write_csv(self.matches_csv_path, MATCH_HEADERS, match_rows)
        self._write_dynamic_csv(self.bet_ledger_csv_path, ledger_rows)
        if daily_report:
            self._write_dynamic_csv(self.daily_report_csv_path, daily_rows)
            self._write_dynamic_csv(self.daily_summary_csv_path, daily_summary_rows)
        self._write_dynamic_csv(self.learning_csv_path, learning_rows)
        self.summary_json_path.write_text(json.dumps(summary or {}, ensure_ascii=False, indent=2), encoding="utf-8")
        if guard_report:
            self.guard_report_json_path.write_text(json.dumps(guard_report, ensure_ascii=False, indent=2), encoding='utf-8')
            self._write_dynamic_csv(self.guard_report_csv_path, list(guard_report.get('funnel') or []))

        webhook_result = self._push_webhook(payload)
        return {
            "rows": len(rows),
            "json_path": str(self.json_path),
            "csv_path": str(self.csv_path),
            "summary_json_path": str(self.summary_json_path),
            "picks_csv_path": str(self.picks_csv_path),
            "matches_csv_path": str(self.matches_csv_path),
            "bet_ledger_csv_path": str(self.bet_ledger_csv_path),
            "daily_report_csv_path": str(self.daily_report_csv_path) if daily_report else "",
            "daily_summary_csv_path": str(self.daily_summary_csv_path) if daily_report else "",
            "learning_csv_path": str(self.learning_csv_path),
            "guard_report_json_path": str(self.guard_report_json_path) if guard_report else "",
            "guard_report_csv_path": str(self.guard_report_csv_path) if guard_report else "",
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
        family = str(getattr(bet, "family", "") or "")
        selection = str(getattr(bet, "selection", "") or "")
        source_summary = getattr(bet, "source_summary", None) or {}
        analysis = getattr(bet, "analysis", None) or {}

        selected_bookmaker = ""
        odds_sources = ""
        if isinstance(source_summary, dict):
            selected_bookmaker = str(
                source_summary.get("selected_bookmaker")
                or source_summary.get("target_bookmaker")
                or ""
            )
            odds_sources = ", ".join(str(item) for item in (source_summary.get("sources") or [])[:4])

        factors_list: list[str] = []
        if isinstance(analysis, dict):
            factors_list.extend(
                str(item).strip()
                for item in (analysis.get("summary_points") or [])[:4]
                if str(item).strip()
            )
        if not factors_list:
            factors_list.extend(
                str(item).strip()
                for item in (getattr(bet, "reasons", None) or [])[:6]
                if str(item).strip()
            )

        xg = ""
        if getattr(bet, "expected_home", None) is not None or getattr(bet, "expected_away", None) is not None:
            xg = f"{self._fmt(getattr(bet, 'expected_home', None))} : {self._fmt(getattr(bet, 'expected_away', None))}"

        market_prob = self._to_probability_pct(getattr(bet, 'market_probability', None))
        adjusted_prob = self._to_probability_pct(getattr(bet, 'adjusted_probability', None))
        gap_to_market = ''
        if market_prob is not None and adjusted_prob is not None:
            gap_to_market = self._fmt(adjusted_prob - market_prob)

        value_tier = self._value_tier(
            edge_pct=getattr(bet, 'edge_pct', None),
            ev_pct=getattr(bet, 'ev_pct', None),
            confidence=getattr(bet, 'confidence', None),
        )

        guard_reason = ''
        if isinstance(source_summary, dict):
            reasons = source_summary.get('quality_reasons') or []
            if reasons:
                guard_reason = str(reasons[0])

        return {
            "Вид спорта": getattr(bet, "sport_key", ""),
            "Дата матча UTC": getattr(getattr(bet, "commence_time", None), "strftime", lambda _f: "")("%d.%m.%Y %H:%M")
            if getattr(bet, "commence_time", None)
            else "",
            "Лига": getattr(bet, "league_name", ""),
            "Матч": f"{getattr(bet, 'home_team', '')} - {getattr(bet, 'away_team', '')}",
            "Рынок": russian_market_name(family),
            "Исход": russian_selection(family, selection, point),
            "Линия": "" if point is None else point,
            "Коэффициент": self._fmt(getattr(bet, "odds", None)),
            "БК": selected_bookmaker,
            "Источники коэффициента": odds_sources,
            "Вероятность модели %": self._pct(getattr(bet, "model_probability", None)),
            "Скорр. вероятность %": self._pct(getattr(bet, "adjusted_probability", None)),
            "Импл. вероятность %": self._pct(getattr(bet, "implied_probability", None)),
            "Gap к рынку п.п.": gap_to_market,
            "Edge %": self._fmt(getattr(bet, "edge_pct", None)),
            "EV %": self._fmt(getattr(bet, "ev_pct", None)),
            "Confidence": self._fmt(getattr(bet, "confidence", None)),
            "Value tier": value_tier,
            "Книг": getattr(bet, "books_count", ""),
            "Источников": getattr(bet, "sources_count", ""),
            "Режим модели": getattr(bet, "model_mode", ""),
            "Derived market signal": "yes" if bool((source_summary or {}).get('market_signal_derived')) else "no",
            "Score публикации": self._fmt(getattr(bet, "publication_score", None)),
            "xG / модель": xg,
            "Причина guard/quality": guard_reason,
            "Факторы": "; ".join(factors_list[:6]),
        }

    def _row_for_match(self, match: Any, forecast: dict[str, Any] | None = None) -> dict[str, Any]:
        commence_time = getattr(match, "commence_time", None)
        row = {
            "match_key": getattr(match, "match_key", ""),
            "sport": getattr(match, "sport_key", ""),
            "league": getattr(match, "league_name", ""),
            "home": getattr(match, "home_team", ""),
            "away": getattr(match, "away_team", ""),
            "commence_time_utc": commence_time.isoformat() if commence_time else "",
        }
        row.update(self._forecast_columns(forecast))
        return row

    @staticmethod
    def _learning_rows(quality_report: dict[str, Any]) -> list[dict[str, Any]]:
        learning = dict((quality_report or {}).get("learning") or {})
        rows: list[dict[str, Any]] = []
        for polarity, key in (("positive", "positive_segments"), ("negative", "negative_segments")):
            for item in (learning.get(key) or []):
                if not isinstance(item, dict):
                    continue
                rows.append({
                    "polarity": polarity,
                    "segment": item.get("segment") or "",
                    "count": item.get("count") or "",
                    "calibration_delta_probability": item.get("calibration_delta_probability") if item.get("calibration_delta_probability") is not None else "",
                    "roi_pct": item.get("roi_pct") if item.get("roi_pct") is not None else "",
                    "hit_rate_pct": item.get("hit_rate_pct") if item.get("hit_rate_pct") is not None else "",
                    "avg_predicted_probability": item.get("avg_predicted_probability") if item.get("avg_predicted_probability") is not None else "",
                })
        return rows

    @staticmethod
    def _forecast_rows_by_match(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        ranked: dict[str, dict[str, Any]] = {}
        for row in rows:
            if not isinstance(row, dict):
                continue
            match_key = str(row.get("match_key") or "")
            if not match_key:
                continue
            existing = ranked.get(match_key)
            if existing is None or SheetExportService._forecast_rank(row) > SheetExportService._forecast_rank(existing):
                ranked[match_key] = row
        return ranked

    @staticmethod
    def _forecast_rank(row: dict[str, Any]) -> tuple[int, float, float]:
        status = str(row.get("forecast_status") or row.get("model_filter_status") or "")
        priority = {
            "published": 5,
            "publishable_dry_run": 4,
            "publishable": 4,
            "passed": 3,
            "reused_already_in_state": 3,
            "zero_stake": 2,
            "rejected_by_model_filters": 1,
        }.get(status, 0)
        try:
            score = float(row.get("publication_score") or 0.0)
        except Exception:
            score = 0.0
        try:
            ev = float(row.get("ev_pct") or 0.0)
        except Exception:
            ev = 0.0
        return (priority, score, ev)

    @staticmethod
    def _forecast_columns(forecast: dict[str, Any] | None) -> dict[str, Any]:
        row = {key: "" for key in MATCH_HEADERS if key.startswith("forecast_")}
        if not forecast:
            return row

        market_prob = SheetExportService._to_probability_pct(forecast.get("market_probability"))
        adjusted_prob = SheetExportService._to_probability_pct(forecast.get("adjusted_probability"))
        probability_gap = ''
        if market_prob is not None and adjusted_prob is not None:
            probability_gap = SheetExportService._fmt(adjusted_prob - market_prob)

        value_tier = SheetExportService._value_tier(
            edge_pct=forecast.get('edge_pct'),
            ev_pct=forecast.get('ev_pct'),
            confidence=forecast.get('confidence'),
        )
        quality_reasons = list(forecast.get("quality_reasons") or [])
        guard_reason = str(quality_reasons[0]) if quality_reasons else ''

        row.update({
            "forecast_status": forecast.get("forecast_status") or forecast.get("model_filter_status") or "",
            "forecast_family": forecast.get("family") or "",
            "forecast_selection": forecast.get("selection") or "",
            "forecast_line": forecast.get("point") if forecast.get("point") is not None else "",
            "forecast_odds": SheetExportService._fmt(forecast.get("odds")),
            "forecast_bookmaker": forecast.get("selected_bookmaker") or "",
            "forecast_odds_source": forecast.get("selected_source") or "",
            "forecast_model_probability_pct": SheetExportService._pct(forecast.get("model_probability")),
            "forecast_adjusted_probability_pct": SheetExportService._pct(forecast.get("adjusted_probability")),
            "forecast_market_probability_pct": SheetExportService._pct(forecast.get("market_probability")),
            "forecast_probability_gap_pct": probability_gap,
            "forecast_edge_pct": SheetExportService._fmt(forecast.get("edge_pct")),
            "forecast_ev_pct": SheetExportService._fmt(forecast.get("ev_pct")),
            "forecast_confidence": SheetExportService._fmt(forecast.get("confidence")),
            "forecast_value_tier": value_tier,
            "forecast_books_count": forecast.get("books_count") or "",
            "forecast_sources_count": forecast.get("sources_count") or "",
            "forecast_model_mode": forecast.get("model_mode") or "",
            "forecast_market_signal_derived": bool(forecast.get("market_signal_derived")),
            "forecast_shadow_tracked": bool(forecast.get("shadow_tracked")),
            "forecast_guard_reason": guard_reason,
            "forecast_publication_score": SheetExportService._fmt(forecast.get("publication_score")),
            "forecast_expected_home": SheetExportService._fmt(forecast.get("expected_home")),
            "forecast_expected_away": SheetExportService._fmt(forecast.get("expected_away")),
            "forecast_total_xg": SheetExportService._fmt(forecast.get("total_xg")),
            "forecast_context_source": forecast.get("context_source") or "",
            "forecast_context_confidence": SheetExportService._fmt(forecast.get("context_confidence")),
            "forecast_market_movement": forecast.get("market_movement") or "",
            "forecast_quality_status": forecast.get("quality_status") or "",
            "forecast_quality_score": SheetExportService._fmt(forecast.get("quality_score")),
            "forecast_quality_reasons": "; ".join(str(item) for item in quality_reasons[:8]),
            "forecast_quality_calibration": json.dumps(forecast.get("quality_calibration") or {}, ensure_ascii=False, sort_keys=True),
            "forecast_reasons": "; ".join(str(item) for item in (forecast.get("reasons") or [])[:8]),
            "forecast_analysis_points": "; ".join(str(item) for item in (forecast.get("analysis_points") or [])[:4]),
        })
        return row

    @staticmethod
    def _write_csv(path: Path, headers: list[str], rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(headers)
            writer.writerows([[row.get(col, "") for col in headers] for row in rows])

    @staticmethod
    def _write_dynamic_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        headers: list[str] = []
        for row in rows:
            for key in row.keys():
                if key not in headers:
                    headers.append(key)
        with path.open("w", newline="", encoding="utf-8") as handle:
            if not headers:
                handle.write("")
                return
            writer = csv.DictWriter(handle, fieldnames=headers)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: SheetExportService._csv_value(row.get(key)) for key in headers})

    @staticmethod
    def _csv_value(value: Any) -> Any:
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False, sort_keys=True)
        return value

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

    @staticmethod
    def _to_probability_pct(value: Any) -> float | None:
        try:
            if value is None or value == '':
                return None
            value = float(value)
            if value <= 1.0:
                value *= 100.0
            return value
        except Exception:
            return None

    @staticmethod
    def _value_tier(edge_pct: Any, ev_pct: Any, confidence: Any) -> str:
        try:
            edge = float(edge_pct or 0.0)
            ev = float(ev_pct or 0.0)
            conf = float(confidence or 0.0)
        except Exception:
            return 'unknown'
        if edge >= 3.0 and ev >= 2.0 and conf >= 60.0:
            return 'A'
        if edge >= 2.0 and ev >= 1.2 and conf >= 55.0:
            return 'B'
        if edge >= 1.0 and ev >= 0.7 and conf >= 50.0:
            return 'C'
        return 'D'
