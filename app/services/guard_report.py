from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class FunnelStep:
    name: str
    count: int

    def as_dict(self) -> dict[str, Any]:
        return {'name': self.name, 'count': self.count}


class GuardReportService:
    def __init__(self, settings: Any) -> None:
        self.settings = settings

    def build_report(
        self,
        *,
        rejections: dict[str, int],
        forecast_rows: list[dict[str, Any]],
        quality_decisions: list[dict[str, Any]] | None = None,
        context_summary: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        rows = [dict(item) for item in forecast_rows if isinstance(item, dict)]
        quality_rows = [dict(item) for item in (quality_decisions or []) if isinstance(item, dict)]
        context_summary = dict(context_summary or {})

        status_counter: Counter[str] = Counter(str(row.get('forecast_status') or row.get('model_filter_status') or 'unknown') for row in rows)
        family_counter: Counter[str] = Counter(str(row.get('family') or row.get('forecast_family') or 'unknown') for row in rows)
        rejection_counter: Counter[str] = Counter({str(k): int(v) for k, v in (rejections or {}).items()})
        quality_reason_counter: Counter[str] = Counter()
        guard_family_counter: Counter[str] = Counter()
        league_bucket_counter: Counter[str] = Counter()

        for row in rows:
            family = str(row.get('family') or row.get('forecast_family') or 'unknown')
            league_bucket = str(row.get('match_tier') or row.get('league_bucket') or 'unknown')
            reasons = list(row.get('quality_reasons') or row.get('reasons') or [])
            if reasons:
                quality_reason_counter[str(reasons[0])] += 1
                guard_family_counter[family] += 1
                league_bucket_counter[league_bucket] += 1

        for decision in quality_rows:
            reasons = list(decision.get('reasons') or [])
            if reasons:
                quality_reason_counter[str(reasons[0])] += 1

        funnel = self._funnel(rows=rows, quality_rows=quality_rows, context_summary=context_summary)
        return {
            'summary': {
                'forecast_rows': len(rows),
                'quality_decisions': len(quality_rows),
                'matches_seen': int(context_summary.get('matches_seen') or 0),
                'matches_with_offers': int(context_summary.get('matches_with_offers') or 0),
                'contexts_built': int(context_summary.get('contexts_built') or 0),
                'published': int(context_summary.get('published') or status_counter.get('published', 0)),
            },
            'funnel': [step.as_dict() for step in funnel],
            'top_rejections': dict(rejection_counter.most_common(15)),
            'forecast_status_counts': dict(status_counter.most_common()),
            'family_counts': dict(family_counter.most_common()),
            'top_quality_reasons': dict(quality_reason_counter.most_common(15)),
            'guarded_family_counts': dict(guard_family_counter.most_common()),
            'guarded_league_bucket_counts': dict(league_bucket_counter.most_common()),
        }

    def export_report(self, export_dir: str, report: dict[str, Any]) -> dict[str, str]:
        root = Path(export_dir)
        root.mkdir(parents=True, exist_ok=True)
        json_path = root / 'guard-report.json'
        csv_path = root / 'guard-report-funnel.csv'
        json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        with csv_path.open('w', newline='', encoding='utf-8') as handle:
            writer = csv.DictWriter(handle, fieldnames=['name', 'count'])
            writer.writeheader()
            for row in report.get('funnel', []):
                writer.writerow({'name': row.get('name', ''), 'count': row.get('count', 0)})
        return {
            'guard_report_json': str(json_path),
            'guard_report_funnel_csv': str(csv_path),
        }

    @staticmethod
    def _funnel(
        *,
        rows: list[dict[str, Any]],
        quality_rows: list[dict[str, Any]],
        context_summary: dict[str, Any],
    ) -> list[FunnelStep]:
        matches_seen = int(context_summary.get('matches_seen') or 0)
        matches_with_offers = int(context_summary.get('matches_with_offers') or 0)
        contexts_built = int(context_summary.get('contexts_built') or 0)
        raw_candidates = int(context_summary.get('raw_candidates') or 0)
        passed_quality = int(context_summary.get('passed_quality') or 0)
        published = int(context_summary.get('published') or 0)

        if not raw_candidates:
            raw_candidates = len(rows)
        if not passed_quality and quality_rows:
            passed_quality = sum(1 for item in quality_rows if str(item.get('status') or '').startswith('passed_quality'))

        return [
            FunnelStep('matches_seen', matches_seen),
            FunnelStep('matches_with_offers', matches_with_offers),
            FunnelStep('contexts_built', contexts_built),
            FunnelStep('raw_candidates', raw_candidates),
            FunnelStep('passed_quality', passed_quality),
            FunnelStep('published', published),
        ]
