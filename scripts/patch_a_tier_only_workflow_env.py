from __future__ import annotations

"""Patch run-bot workflow defaults to match A-tier-only publication.

This is a local one-time helper. It does not change prediction logic directly;
it only removes stale workflow defaults that still describe B-tier as publishable
before runtime policy scripts override them.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path('.').resolve()
WORKFLOW = ROOT / '.github' / 'workflows' / 'run-bot.yml'
OUT = ROOT / '.data' / 'exports' / 'latest-a-tier-workflow-env-cleanup.json'

REPLACEMENTS = {
    'PUBLISH_ALLOW_B_TIER: "true"': 'PUBLISH_ALLOW_B_TIER: "false"',
    'PUBLISH_COVERAGE_TIER_MODE: "hybrid"': 'PUBLISH_COVERAGE_TIER_MODE: "a_only_publish_b_watchlist"',
    'MIN_BOOKS_PUBLISH: "1"': 'MIN_BOOKS_PUBLISH: "2"',
    'PUBLISH_MIN_BOOKS: "1"': 'PUBLISH_MIN_BOOKS: "2"',
    'PUBLISH_TIER_B_MIN_BOOKS: "1"': 'PUBLISH_TIER_B_MIN_BOOKS: "2"',
    'MIN_SOURCES_PUBLISH: "1"': 'MIN_SOURCES_PUBLISH: "2"',
    'PUBLISH_MIN_ODDS_SOURCES: "1"': 'PUBLISH_MIN_ODDS_SOURCES: "2"',
    'PUBLISH_MIN_CONTEXT_SOURCES: "1"': 'PUBLISH_MIN_CONTEXT_SOURCES: "2"',
    'MIN_CONTEXT_SOURCES_PUBLISH: "1"': 'MIN_CONTEXT_SOURCES_PUBLISH: "2"',
    'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES: "1"': 'CONTROLLED_FALLBACK_MIN_ODDS_SOURCES: "2"',
    'CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES: "false"': 'CONTROLLED_FALLBACK_TIER_A_REQUIRE_2_ODDS_SOURCES: "true"',
    'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS: "1"': 'CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS: "2"',
    'CONTROLLED_FALLBACK_TIER_B_REQUIRE_2_BOOKS_FOR_TELEGRAM: "false"': 'CONTROLLED_FALLBACK_TIER_B_REQUIRE_2_BOOKS_FOR_TELEGRAM: "true"',
    'CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES: "false"': 'CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES: "true"',
    'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM: "false"': 'CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM: "true"',
    'CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES: "false"': 'CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES: "true"',
    'CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B: "true"': 'CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B: "false"',
    'PROMOTE_A_COVER_ONLY_PUBLISH_WINDOW: "true"': 'PROMOTE_A_COVER_ONLY_PUBLISH_WINDOW: "false"',
}

INSERT_AFTER = 'CONTROLLED_FALLBACK_TELEGRAM_ALLOW_TIER_B: "false"'
INSERT_LINES = [
    '      PUBLISH_B_TIER_WATCH_ONLY: "true"',
    '      CONTROLLED_FALLBACK_TIER_B_WATCH_ONLY: "true"',
    '      CONTROLLED_FALLBACK_TIER_B_PUBLISH_ENABLED: "false"',
    '      PROMOTE_A_COVER_ONLY_PUBLISH_WINDOW: "false"',
    '      PROMOTE_A_COVER_PREP_OUTSIDE_PUBLISH_WINDOW: "true"',
    '      PROMOTE_A_COVER_ACTIVE_WINDOW_HOURS: "24"',
]


def main() -> int:
    report = {
        'created_at_utc': datetime.now(timezone.utc).isoformat(),
        'workflow': str(WORKFLOW),
        'status': 'starting',
        'changed': False,
        'replacements_applied': [],
        'already_ok': [],
        'missing_patterns': [],
        'inserted_lines': [],
    }
    if not WORKFLOW.exists():
        report['status'] = 'missing_workflow'
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    text = WORKFLOW.read_text(encoding='utf-8')
    for old, new in REPLACEMENTS.items():
        if old in text:
            text = text.replace(old, new)
            report['changed'] = True
            report['replacements_applied'].append(old)
        elif new in text:
            report['already_ok'].append(new)
        else:
            report['missing_patterns'].append(old)

    if INSERT_AFTER in text:
        lines = text.splitlines()
        existing = set(line.strip() for line in lines)
        out_lines: list[str] = []
        inserted = False
        for line in lines:
            out_lines.append(line)
            if line.strip() == INSERT_AFTER and not inserted:
                for add in INSERT_LINES:
                    if add.strip() not in existing:
                        out_lines.append(add)
                        report['inserted_lines'].append(add.strip())
                        report['changed'] = True
                inserted = True
        text = '\n'.join(out_lines) + '\n'

    WORKFLOW.write_text(text, encoding='utf-8')
    report['status'] = 'updated' if report['changed'] else 'already_clean'
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
