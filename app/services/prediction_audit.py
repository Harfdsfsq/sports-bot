"""Reviewed data incidents are excluded from model learning, never bank accounting."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

AUDIT_VERSION = 'context-audit-2026-10-12'


@lru_cache(maxsize=1)
def exclusions():
    path = Path(__file__).resolve().parents[2] / 'config/audited_prediction_exclusions.json'
    return {row['prediction_id']: row['reason'] for row in json.loads(path.read_text())['exclusions']}


def invalid_prediction_reasons(row):
    explicit = row.get('learning_exclusion_reasons') or []
    result = list(explicit)
    for field in ('prediction_id', 'fingerprint'):
        reason = exclusions().get(row.get(field))
        if reason and reason not in result:
            result.append(reason)
    return result
