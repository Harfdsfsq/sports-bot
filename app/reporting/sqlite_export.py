from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


class ReportingSQLiteExporter:
    def __init__(self, db_path: str):
        self.db_path = Path(db_path)

    def export(self, *, state_path: str, history_root: str) -> dict[str, Any]:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path)
        try:
            self._create_schema(conn)
            counts = {"runs": 0, "provider_runs": 0, "rejections": 0, "forecasts": 0, "bet_ledger": 0, "settlements": 0}
            run_files = sorted(Path(history_root).rglob("*-run.json"))
            for table in ["runs", "provider_runs", "rejections", "forecasts", "bet_ledger", "settlements"]:
                conn.execute(f"DELETE FROM {table}")
            for path in run_files:
                payload = json.loads(path.read_text(encoding="utf-8"))
                run_id = f"{path.parent.name}-{path.stem.replace('-run', '')}"
                summary = dict(payload.get("summary") or {})
                conn.execute(
                    "INSERT INTO runs (run_id, created_at, status, matches_seen, matches_with_offers, contexts_built, candidates_before_quality, candidates_raw, candidates_publishable, published, telegram_messages_sent, summary_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        run_id, payload.get("created_at"), payload.get("status") or summary.get("status") or "completed",
                        int(summary.get("matches_seen") or 0), int(summary.get("matches_with_offers") or 0), int(summary.get("contexts_built") or 0),
                        int(summary.get("candidates_before_quality") or 0), int(summary.get("candidates_raw") or 0), int(summary.get("candidates_publishable") or 0),
                        int(summary.get("published") or summary.get("published_to_telegram") or 0), int(summary.get("telegram_messages_sent") or 0), json.dumps(summary, ensure_ascii=False),
                    ),
                )
                counts["runs"] += 1
                providers = (((payload.get("provider_diagnostics") or {}).get("summary") or {}).get("providers") or {})
                for provider_key, provider_info in providers.items():
                    stats = dict(provider_info.get("stats") or {})
                    conn.execute(
                        "INSERT INTO provider_runs (run_id, provider_key, provider_type, matches_with_data, items_total, enabled, response_errors, rate_limited, stats_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (run_id, str(provider_key), str(provider_info.get("type") or ""), int(provider_info.get("matches_with_data") or 0), int(provider_info.get("items_total") or 0), 1 if bool(stats.get("enabled", True)) else 0, int(stats.get("response_errors") or 0), 1 if bool(stats.get("rate_limited")) else 0, json.dumps(provider_info, ensure_ascii=False)),
                    )
                    counts["provider_runs"] += 1
                for rejection_key, rejection_count in dict(summary.get("rejections") or {}).items():
                    conn.execute("INSERT INTO rejections (run_id, rejection_key, rejection_count) VALUES (?, ?, ?)", (run_id, str(rejection_key), int(rejection_count or 0)))
                    counts["rejections"] += 1
                for forecast in payload.get("forecast_rows") or []:
                    conn.execute(
                        "INSERT INTO forecasts (run_id, match_key, family, selection_key, point, market_probability, model_probability, adjusted_probability, confidence, publication_score, quality_status, forecast_status, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (run_id, str(forecast.get("match_key") or ""), str(forecast.get("family") or ""), str(forecast.get("selection_key") or ""), self._float(forecast.get("point")), self._float(forecast.get("market_probability")), self._float(forecast.get("model_probability")), self._float(forecast.get("adjusted_probability")), self._float(forecast.get("confidence")), self._float(forecast.get("publication_score")), str(forecast.get("quality_status") or ""), str(forecast.get("forecast_status") or ""), json.dumps(forecast, ensure_ascii=False)),
                    )
                    counts["forecasts"] += 1
                for entry in payload.get("bet_ledger") or []:
                    conn.execute(
                        "INSERT INTO bet_ledger (run_id, prediction_id, match_key, league_name, home_team, away_team, family, selection_key, point, odds, stake_amount, status, result, pnl, roi_pct, final_score, payload_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                        (run_id, str(entry.get("prediction_id") or ""), str(entry.get("match_key") or ""), str(entry.get("league_name") or ""), str(entry.get("home_team") or ""), str(entry.get("away_team") or ""), str(entry.get("family") or ""), str(entry.get("selection_key") or entry.get("selection") or ""), self._float(entry.get("point")), self._float(entry.get("odds")), self._float(entry.get("stake_amount")), str(entry.get("status") or ""), str(entry.get("result") or ""), self._float(entry.get("pnl")), self._float(entry.get("roi_pct")), str(entry.get("final_score") or ""), json.dumps(entry, ensure_ascii=False)),
                    )
                    counts["bet_ledger"] += 1
                    if str(entry.get("result") or entry.get("status") or "") in {"won", "lost", "push", "void", "half_won", "half_lost"}:
                        conn.execute("INSERT INTO settlements (run_id, prediction_id, result, pnl, final_score, payload_json) VALUES (?, ?, ?, ?, ?, ?)", (run_id, str(entry.get("prediction_id") or ""), str(entry.get("result") or entry.get("status") or ""), self._float(entry.get("pnl")), str(entry.get("final_score") or ""), json.dumps(entry, ensure_ascii=False)))
                        counts["settlements"] += 1
            state = json.loads(Path(state_path).read_text(encoding="utf-8"))
            summary = {
                "database_path": str(self.db_path),
                "history_root": str(history_root),
                "state_path": str(state_path),
                "runs_exported": counts["runs"],
                "provider_rows": counts["provider_runs"],
                "rejections_exported": counts["rejections"],
                "forecasts_exported": counts["forecasts"],
                "bet_ledger_exported": counts["bet_ledger"],
                "settlements_exported": counts["settlements"],
                "state_bets": len(state.get("bets") or []),
                "state_shadow_bets": len(state.get("shadow_bets") or []),
            }
            conn.commit()
            return summary
        finally:
            conn.close()

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, created_at TEXT, status TEXT, matches_seen INTEGER, matches_with_offers INTEGER, contexts_built INTEGER, candidates_before_quality INTEGER, candidates_raw INTEGER, candidates_publishable INTEGER, published INTEGER, telegram_messages_sent INTEGER, summary_json TEXT);
            CREATE TABLE IF NOT EXISTS provider_runs (run_id TEXT, provider_key TEXT, provider_type TEXT, matches_with_data INTEGER, items_total INTEGER, enabled INTEGER, response_errors INTEGER, rate_limited INTEGER, stats_json TEXT);
            CREATE TABLE IF NOT EXISTS rejections (run_id TEXT, rejection_key TEXT, rejection_count INTEGER);
            CREATE TABLE IF NOT EXISTS forecasts (run_id TEXT, match_key TEXT, family TEXT, selection_key TEXT, point REAL, market_probability REAL, model_probability REAL, adjusted_probability REAL, confidence REAL, publication_score REAL, quality_status TEXT, forecast_status TEXT, payload_json TEXT);
            CREATE TABLE IF NOT EXISTS bet_ledger (run_id TEXT, prediction_id TEXT, match_key TEXT, league_name TEXT, home_team TEXT, away_team TEXT, family TEXT, selection_key TEXT, point REAL, odds REAL, stake_amount REAL, status TEXT, result TEXT, pnl REAL, roi_pct REAL, final_score TEXT, payload_json TEXT);
            CREATE TABLE IF NOT EXISTS settlements (run_id TEXT, prediction_id TEXT, result TEXT, pnl REAL, final_score TEXT, payload_json TEXT);
            """
        )

    @staticmethod
    def _float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except Exception:
            return None
