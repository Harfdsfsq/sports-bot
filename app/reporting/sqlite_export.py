from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc


def _iter_run_files(history_root: Path):
    for path in sorted(history_root.glob('*/*-run.json')):
        if path.is_file():
            yield path


class ReportingSQLiteExporter:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, *, state_path: str, history_root: str) -> dict[str, Any]:
        state_file = Path(state_path)
        history_dir = Path(history_root)
        state = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {}

        conn = sqlite3.connect(self.db_path)
        try:
            self._create_schema(conn)
            runs = 0
            provider_rows = 0
            rejection_rows = 0
            forecast_rows = 0
            settlement_rows = 0
            ledger_rows = 0
            for run_path in _iter_run_files(history_dir):
                payload = json.loads(run_path.read_text(encoding='utf-8'))
                run_id = str(run_path)
                summary = dict(payload.get('summary') or {})
                conn.execute(
                    """
                    INSERT OR REPLACE INTO runs(run_id, created_at, status, matches_seen, matches_with_offers, contexts_built, candidates_before_quality, published)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        run_id,
                        str(payload.get('created_at') or ''),
                        str(summary.get('run_status') or summary.get('status') or 'ok'),
                        int(summary.get('matches_seen') or 0),
                        int(summary.get('matches_with_offers') or 0),
                        int(summary.get('contexts_built') or 0),
                        int(summary.get('candidates_before_quality') or 0),
                        int(summary.get('published') or 0),
                    ),
                )
                runs += 1
                diag = dict(((payload.get('provider_diagnostics') or {}).get('summary')) or {})
                for provider_name, provider_payload in dict(diag.get('providers') or {}).items():
                    stats = dict((provider_payload or {}).get('stats') or {})
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO provider_runs(run_id, provider_key, provider_type, matches_with_data, items_total, stats_json)
                        VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            run_id,
                            provider_name,
                            str((provider_payload or {}).get('type') or ''),
                            int((provider_payload or {}).get('matches_with_data') or 0),
                            int((provider_payload or {}).get('items_total') or 0),
                            json.dumps(stats, ensure_ascii=False),
                        ),
                    )
                    provider_rows += 1
                for reason, count in dict(summary.get('rejections') or {}).items():
                    conn.execute(
                        "INSERT OR REPLACE INTO rejections(run_id, rejection_key, rejection_count) VALUES (?, ?, ?)",
                        (run_id, str(reason), int(count or 0)),
                    )
                    rejection_rows += 1
                for row in [dict(item) for item in (payload.get('forecast_rows') or []) if isinstance(item, dict)]:
                    prediction_id = str(row.get('fingerprint') or row.get('prediction_id') or f"{run_id}:{forecast_rows}")
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO forecasts(prediction_id, run_id, match_key, league_name, family, selection, odds, adjusted_probability, confidence, publication_score, status)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            prediction_id,
                            run_id,
                            str(row.get('match_key') or ''),
                            str(row.get('league_name') or ''),
                            str(row.get('family') or ''),
                            str(row.get('selection') or ''),
                            float(row.get('odds') or 0.0),
                            float(row.get('adjusted_probability') or row.get('final_probability') or 0.0),
                            float(row.get('confidence') or 0.0),
                            float(row.get('publication_score') or 0.0),
                            str(row.get('status') or ''),
                        ),
                    )
                    forecast_rows += 1
            for collection_name in ('bets', 'shadow_bets'):
                for row in [dict(item) for item in (state.get(collection_name) or []) if isinstance(item, dict)]:
                    prediction_id = str(row.get('prediction_id') or row.get('fingerprint') or f'{collection_name}:{ledger_rows}')
                    conn.execute(
                        """
                        INSERT OR REPLACE INTO bet_ledger(prediction_id, collection_name, match_key, league_name, family, selection, odds, stake_amount, status, settled_profit)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            prediction_id,
                            collection_name,
                            str(row.get('match_key') or ''),
                            str(row.get('league_name') or ''),
                            str(row.get('family') or ''),
                            str(row.get('selection') or ''),
                            float(row.get('odds') or 0.0),
                            float(row.get('stake_amount') or 0.0),
                            str(row.get('status') or ''),
                            float(row.get('profit') or row.get('pnl') or 0.0),
                        ),
                    )
                    ledger_rows += 1
                    if str(row.get('status') or '').startswith('settled') or row.get('result') is not None:
                        conn.execute(
                            "INSERT OR REPLACE INTO settlements(prediction_id, settled_result, score, profit) VALUES (?, ?, ?, ?)",
                            (
                                prediction_id,
                                str(row.get('result') or row.get('settlement_result') or ''),
                                str(row.get('score') or row.get('final_score') or ''),
                                float(row.get('profit') or row.get('pnl') or 0.0),
                            ),
                        )
                        settlement_rows += 1
            conn.commit()
        finally:
            conn.close()
        return {
            'created_at': datetime.now(UTC).isoformat(),
            'db_path': str(self.db_path),
            'runs': runs,
            'provider_rows': provider_rows,
            'rejections': rejection_rows,
            'forecasts': forecast_rows,
            'ledger_rows': ledger_rows,
            'settlements': settlement_rows,
        }

    def _create_schema(self, conn: sqlite3.Connection) -> None:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                created_at TEXT,
                status TEXT,
                matches_seen INTEGER,
                matches_with_offers INTEGER,
                contexts_built INTEGER,
                candidates_before_quality INTEGER,
                published INTEGER
            );
            CREATE TABLE IF NOT EXISTS provider_runs (
                run_id TEXT,
                provider_key TEXT,
                provider_type TEXT,
                matches_with_data INTEGER,
                items_total INTEGER,
                stats_json TEXT,
                PRIMARY KEY (run_id, provider_key)
            );
            CREATE TABLE IF NOT EXISTS rejections (
                run_id TEXT,
                rejection_key TEXT,
                rejection_count INTEGER,
                PRIMARY KEY (run_id, rejection_key)
            );
            CREATE TABLE IF NOT EXISTS forecasts (
                prediction_id TEXT PRIMARY KEY,
                run_id TEXT,
                match_key TEXT,
                league_name TEXT,
                family TEXT,
                selection TEXT,
                odds REAL,
                adjusted_probability REAL,
                confidence REAL,
                publication_score REAL,
                status TEXT
            );
            CREATE TABLE IF NOT EXISTS bet_ledger (
                prediction_id TEXT PRIMARY KEY,
                collection_name TEXT,
                match_key TEXT,
                league_name TEXT,
                family TEXT,
                selection TEXT,
                odds REAL,
                stake_amount REAL,
                status TEXT,
                settled_profit REAL
            );
            CREATE TABLE IF NOT EXISTS settlements (
                prediction_id TEXT PRIMARY KEY,
                settled_result TEXT,
                score TEXT,
                profit REAL
            );
            """
        )
