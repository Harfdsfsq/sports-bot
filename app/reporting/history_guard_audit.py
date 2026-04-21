from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class GuardAuditExample:
    run_file: str
    league_name: str
    home_team: str
    away_team: str
    family: str
    selection: str
    odds: float
    confidence: float
    edge_pct: float
    ev_pct: float
    books_count: int
    sources_count: int
    risk_tags: list[str]


class HistoryGuardAuditService:
    """Audit archived run payloads for risky publish patterns.

    This is a direct answer to the PDF recommendation to build stronger historical
    regression checks around `.data/history/runs` and publication decisions.
    """

    def __init__(self, output_path: str) -> None:
        self.output_path = str(output_path)

    def build(self, *, history_root: str) -> dict[str, Any]:
        root = Path(history_root)
        run_files = sorted(root.glob('*/*-run.json')) if root.exists() else []
        summary = {
            'history_root': str(root),
            'run_files_seen': len(run_files),
            'runs_with_published_candidates': 0,
            'published_candidates_seen': 0,
            'risky_counts': {},
            'segment_counts': {},
            'examples': [],
        }
        risky_counts: Counter[str] = Counter()
        segment_counts: Counter[str] = Counter()
        examples: list[GuardAuditExample] = []

        for run_file in run_files:
            payload = self._load_json(run_file)
            if not isinstance(payload, dict):
                continue
            candidates = [row for row in (payload.get('candidates') or []) if isinstance(row, dict)]
            if not candidates:
                continue
            summary['runs_with_published_candidates'] += 1
            summary['published_candidates_seen'] += len(candidates)
            for row in candidates:
                tags = self._risk_tags(row)
                league_bucket = self._league_bucket(row)
                family = str(row.get('family') or '')
                segment_counts[f'{league_bucket}|{family}'] += 1
                for tag in tags:
                    risky_counts[tag] += 1
                if tags and len(examples) < 25:
                    examples.append(
                        GuardAuditExample(
                            run_file=str(run_file),
                            league_name=str(row.get('league_name') or ''),
                            home_team=str(row.get('home_team') or ''),
                            away_team=str(row.get('away_team') or ''),
                            family=family,
                            selection=str(row.get('selection') or ''),
                            odds=self._float(row.get('odds')),
                            confidence=self._float(row.get('confidence')),
                            edge_pct=self._float(row.get('edge_pct')),
                            ev_pct=self._float(row.get('ev_pct')),
                            books_count=self._int(row.get('books_count')),
                            sources_count=self._int(row.get('sources_count')),
                            risk_tags=tags,
                        )
                    )

        summary['risky_counts'] = dict(risky_counts)
        summary['segment_counts'] = dict(segment_counts)
        summary['examples'] = [example.__dict__ for example in examples]
        self._write_json(summary)
        return summary

    @staticmethod
    def _load_json(path: Path) -> Any:
        try:
            return json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            return None

    def _write_json(self, payload: dict[str, Any]) -> None:
        out_path = Path(self.output_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')

    @staticmethod
    def _int(value: Any) -> int:
        try:
            return int(float(value or 0))
        except Exception:
            return 0

    @staticmethod
    def _float(value: Any) -> float:
        try:
            return float(value or 0.0)
        except Exception:
            return 0.0

    @staticmethod
    def _league_bucket(row: dict[str, Any]) -> str:
        source_summary = dict(row.get('source_summary') or {})
        league_name = str(row.get('league_name') or '').lower()
        tier = str(source_summary.get('match_tier') or '').lower()
        if tier == 'low':
            return 'low'
        preferred_terms = (
            'champions league', 'europa league', 'conference league', 'premier league', 'la liga',
            'serie a', 'bundesliga', 'ligue 1', 'eredivisie', 'primeira liga', 'championship',
            'world cup', 'euro', 'nations league',
        )
        secondary_terms = (
            'libertadores', 'sudamericana', 'mls', 'belgium', 'swiss', 'austria', 'croatia',
            'greece', 'czech', 'denmark', 'norway', 'sweden', 'brazil', 'argentina',
        )
        if any(term in league_name for term in preferred_terms):
            return 'preferred'
        if any(term in league_name for term in secondary_terms):
            return 'secondary'
        return 'other'

    @staticmethod
    def _is_draw(row: dict[str, Any]) -> bool:
        text = str(row.get('selection') or '').strip().lower()
        return text in {'draw', 'x', 'ничья'}

    def _risk_tags(self, row: dict[str, Any]) -> list[str]:
        tags: list[str] = []
        league_bucket = self._league_bucket(row)
        books_count = self._int(row.get('books_count'))
        sources_count = self._int(row.get('sources_count'))
        odds = self._float(row.get('odds'))
        family = str(row.get('family') or '')
        source_summary = dict(row.get('source_summary') or {})
        context_source = str(source_summary.get('context_source') or '').strip().lower()

        if league_bucket not in {'preferred', 'secondary'} and sources_count < 2:
            tags.append('non_core_single_source')
        if family == 'h2h' and not self._is_draw(row) and odds >= 3.40 and sources_count < 2:
            tags.append('h2h_high_odds_single_source')
        if league_bucket == 'other' and family == 'h2h' and not self._is_draw(row) and odds >= 3.20:
            tags.append('non_core_h2h_high_odds')
        if books_count <= 1:
            tags.append('single_book_publish')
        if league_bucket == 'other' and context_source in {'newsapi', 'gnews'}:
            tags.append('non_core_news_only_context')
        return tags
