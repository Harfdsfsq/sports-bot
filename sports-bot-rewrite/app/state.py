from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.schemas import CandidateBet


class JsonStateStore:
    def __init__(self, state_path: str = '.data/state.json'):
        self.path = Path(state_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.path.write_text(json.dumps({'runs': [], 'published_bets': []}, ensure_ascii=False, indent=2), encoding='utf-8')

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

    @staticmethod
    def _bet_unique_key(bet: CandidateBet) -> str:
        point = '' if bet.point is None else f'|{bet.point}'
        return f'{bet.match_key}|{bet.family}|{bet.selection}{point}|{bet.commence_time.isoformat()}'
