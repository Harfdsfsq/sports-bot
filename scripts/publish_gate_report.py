from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ART = Path('artifacts')
ART.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def main() -> int:
    data = load_json(ART / 'latest-canonical-picks.json', [])
    allow_families = {x.strip() for x in os.getenv('PHASE12_ALLOW_FAMILIES', 'totals,dnb,h2h').split(',') if x.strip()}
    reject_single_source = os.getenv('PHASE12_REJECT_SINGLE_SOURCE', 'true').lower() in {'1','true','yes','on'}
    min_quality = float(os.getenv('MIN_QUALITY_SCORE_PUBLISH', '70'))
    report: list[dict[str, Any]] = []
    for item in data:
        reasons: list[str] = []
        family = str(item.get('family') or '')
        books_count = int(item.get('books_count') or 0)
        sources_count = int(item.get('sources_count') or 0)
        quality_score = float((item.get('source_summary') or {}).get('quality_score') or 0.0)
        quality_status = str((item.get('source_summary') or {}).get('quality_status') or '')
        integrity_flags = list(item.get('integrity_flags') or [])
        if family not in allow_families:
            reasons.append(f'family_not_allowed:{family}')
        if books_count < 2:
            reasons.append(f'books_below_min:{books_count}')
        if reject_single_source and sources_count < 1:
            reasons.append('sources_below_min:0')
        if reject_single_source and sources_count == 1:
            reasons.append('single_source_rejected')
        if quality_score < min_quality:
            reasons.append(f'quality_below_min:{quality_score:.2f}')
        if quality_status and quality_status != 'passed_quality':
            reasons.append(f'quality_status_not_clean:{quality_status}')
        for flag in integrity_flags:
            reasons.append(f'integrity:{flag}')
        report.append({
            'match_key': item.get('match_key'),
            'family': family,
            'selection': item.get('selection'),
            'selected_odds': item.get('selected_odds'),
            'quality_score': quality_score,
            'quality_status': quality_status,
            'publishable': len(reasons) == 0,
            'reasons': reasons,
        })
    (ART / 'publish-gate-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'total': len(report), 'publishable': sum(1 for x in report if x['publishable'])}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
