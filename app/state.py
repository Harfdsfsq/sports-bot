from __future__ import annotations

import csv
import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.schemas import CandidateBet, Match


class JsonStateStore:
    def __init__(self, state_path: str = '.data/state.json', debug_path: str = '.data/debug-last-run.json') -> None:
        self.path = Path(state_path)
        self.debug_path = Path(debug_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.debug_path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(
                json.dumps({'runs': [], 'published_bets': []}, ensure_ascii=False, indent=2),
                encoding='utf-8',
            )

    def load(self) -> dict[str, Any]:
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except Exception:
            return {'runs': [], 'published_bets': []}

    def save(self, data: dict[str, Any]) -> None:
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

    def has_published(self, bet: CandidateBet) -> bool:
        state = self.load()
        key = self._bet_unique_key(bet)
        return any(item.get('unique_key') == key for item in state.get('published_bets', []))

    def save_run(self, status: str, summary: dict[str, Any] | None = None, error_text: str | None = None) -> None:
        state = self.load()
        runs = state.setdefault('runs', [])
        runs.append(
            {
                'started_at': datetime.now(UTC).isoformat(),
                'status': status,
                'summary': summary or {},
                'error_text': error_text,
            }
        )
        state['runs'] = runs[-100:]
        self.save(state)

    def store_candidates(self, candidates: list[CandidateBet], telegram_sent: bool) -> int:
        state = self.load()
        published = state.setdefault('published_bets', [])
        existing = {item.get('unique_key') for item in published}
        count = 0
        for bet in candidates:
            unique_key = self._bet_unique_key(bet)
            if unique_key in existing:
                continue
            row = asdict(bet)
            row['commence_time'] = bet.commence_time.isoformat()
            row['unique_key'] = unique_key
            row['telegram_sent'] = telegram_sent
            row['saved_at'] = datetime.now(UTC).isoformat()
            published.append(row)
            existing.add(unique_key)
            count += 1
        state['published_bets'] = published[-1000:]
        self.save(state)
        return count

    def write_debug(self, payload: dict[str, Any]) -> None:
        self.debug_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    def export_payloads(
        self,
        export_dir: str,
        matches: list[Match],
        candidates: list[CandidateBet],
    ) -> dict[str, str]:
        root = Path(export_dir)
        day_dir = root / datetime.now(UTC).astimezone().strftime('%Y-%m-%d')
        day_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).astimezone().strftime('%H%M%S')

        matches_rows = [self._serialize_match(item) for item in matches]
        picks_rows = [self._serialize_candidate(item) for item in candidates]

        matches_json = day_dir / f'{stamp}-matches.json'
        matches_csv = day_dir / f'{stamp}-matches.csv'
        picks_json = day_dir / f'{stamp}-picks.json'
        picks_csv = day_dir / f'{stamp}-picks.csv'

        self._write_json(matches_json, matches_rows)
        self._write_json(picks_json, picks_rows)
        self._write_csv(matches_csv, matches_rows)
        self._write_csv(picks_csv, picks_rows)

        latest_matches_json = root / 'latest-matches.json'
        latest_matches_csv = root / 'latest-matches.csv'
        latest_picks_json = root / 'latest-picks.json'
        latest_picks_csv = root / 'latest-picks.csv'

        self._write_json(latest_matches_json, matches_rows)
        self._write_json(latest_picks_json, picks_rows)
        self._write_csv(latest_matches_csv, matches_rows)
        self._write_csv(latest_picks_csv, picks_rows)

        return {
            'matches_json': str(matches_json),
            'matches_csv': str(matches_csv),
            'picks_json': str(picks_json),
            'picks_csv': str(picks_csv),
            'latest_matches_json': str(latest_matches_json),
            'latest_matches_csv': str(latest_matches_csv),
            'latest_picks_json': str(latest_picks_json),
            'latest_picks_csv': str(latest_picks_csv),
        }

    @staticmethod
    def _write_json(path: Path, rows: list[dict[str, Any]]) -> None:
        path.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding='utf-8')

    @staticmethod
    def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
        if not rows:
            path.write_text('', encoding='utf-8')
            return
        with path.open('w', encoding='utf-8', newline='') as fh:
            writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)

    @staticmethod
    def _serialize_match(match: Match) -> dict[str, Any]:
        return {
            'match_key': match.match_key,
            'source': match.source,
            'source_event_id': match.source_event_id,
            'sport_key': match.sport_key,
            'league_name': match.league_name,
            'home_team': match.home_team,
            'away_team': match.away_team,
            'commence_time': match.commence_time.isoformat(),
            'tier': match.tier,
        }

    @staticmethod
    def _serialize_candidate(bet: CandidateBet) -> dict[str, Any]:
        row = asdict(bet)
        row['commence_time'] = bet.commence_time.isoformat()
        return row

    @staticmethod
    def _bet_unique_key(bet: CandidateBet) -> str:
        point = '' if bet.point is None else f'|{bet.point}'
        return f'{bet.match_key}|{bet.family}|{bet.selection}{point}|{bet.commence_time.isoformat()}'
