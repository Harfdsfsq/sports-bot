from __future__ import annotations

import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc


class TrainingDatasetExporter:
    def __init__(self, output_path: str) -> None:
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

    def export(self, *, state_path: str) -> dict[str, Any]:
        state_file = Path(state_path)
        state = json.loads(state_file.read_text(encoding='utf-8')) if state_file.exists() else {}
        rows: list[dict[str, Any]] = []
        for collection_name in ('bets', 'shadow_bets', 'published_candidates'):
            for item in [dict(row) for row in (state.get(collection_name) or []) if isinstance(row, dict)]:
                rows.append({
                    'collection': collection_name,
                    'prediction_id': str(item.get('prediction_id') or item.get('fingerprint') or ''),
                    'match_key': str(item.get('match_key') or ''),
                    'league_name': str(item.get('league_name') or ''),
                    'family': str(item.get('family') or ''),
                    'selection': str(item.get('selection') or ''),
                    'odds': float(item.get('odds') or 0.0),
                    'market_probability': float(item.get('market_probability') or item.get('consensus_probability') or 0.0),
                    'model_probability': float(item.get('model_probability') or 0.0),
                    'adjusted_probability': float(item.get('adjusted_probability') or item.get('final_probability') or 0.0),
                    'confidence': float(item.get('confidence') or 0.0),
                    'books_count': int(item.get('books_count') or 0),
                    'sources_count': int(item.get('sources_count') or 0),
                    'publication_score': float(item.get('publication_score') or 0.0),
                    'expected_home': item.get('expected_home'),
                    'expected_away': item.get('expected_away'),
                    'status': str(item.get('status') or ''),
                    'profit': float(item.get('profit') or item.get('pnl') or 0.0),
                    'result': str(item.get('result') or item.get('settlement_result') or ''),
                })
        fieldnames = list(rows[0].keys()) if rows else [
            'collection','prediction_id','match_key','league_name','family','selection','odds','market_probability','model_probability','adjusted_probability','confidence','books_count','sources_count','publication_score','expected_home','expected_away','status','profit','result'
        ]
        with self.output_path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        jsonl_path = self.output_path.with_suffix('.jsonl')
        with jsonl_path.open('w', encoding='utf-8') as handle:
            for row in rows:
                handle.write(json.dumps(row, ensure_ascii=False) + '\n')
        settled = sum(1 for row in rows if str(row.get('result') or '').strip())
        return {
            'created_at': datetime.now(UTC).isoformat(),
            'csv_path': str(self.output_path),
            'jsonl_path': str(jsonl_path),
            'rows': len(rows),
            'settled_rows': settled,
            'pending_rows': len(rows) - settled,
        }
