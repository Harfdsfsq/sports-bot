from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc


class CoverageAuditService:
    def __init__(self, report_path: str) -> None:
        self.report_path = Path(report_path)
        self.report_path.parent.mkdir(parents=True, exist_ok=True)

    def build(self, *, debug_path: str) -> dict[str, Any]:
        debug_file = Path(debug_path)
        payload = json.loads(debug_file.read_text(encoding='utf-8')) if debug_file.exists() else {}
        summary = dict(payload.get('summary') or {})
        provider_summary = dict(((payload.get('provider_diagnostics') or {}).get('summary')) or {})
        provider_runtime_errors = dict(provider_summary.get('provider_runtime_errors') or {})
        provider_rate_limits = dict(provider_summary.get('provider_rate_limits') or {})
        providers = dict(provider_summary.get('providers') or {})
        rejections = dict(summary.get('rejections') or {})
        top_rejections = dict(sorted(rejections.items(), key=lambda kv: (-int(kv[1] or 0), kv[0]))[:12])

        provider_rows: list[dict[str, Any]] = []
        rate_limit_counter: Counter[str] = Counter({str(k): int(v.get('http_429_seen') or v.get('rate_limited') or 0) if isinstance(v, dict) else int(v or 0) for k, v in provider_rate_limits.items()})
        for provider_name, provider_payload in providers.items():
            stats = dict((provider_payload or {}).get('stats') or {})
            errors = [str(item) for item in (provider_runtime_errors.get(provider_name) or []) if str(item).strip()]
            error_blob = ' | '.join(errors).lower()
            if provider_name not in rate_limit_counter and ('429' in error_blob or 'rate limit' in error_blob):
                rate_limit_counter[provider_name] += 1
            provider_rows.append({
                'provider': provider_name,
                'type': str((provider_payload or {}).get('type') or ''),
                'matches_with_data': int((provider_payload or {}).get('matches_with_data') or 0),
                'items_total': int((provider_payload or {}).get('items_total') or 0),
                'stats': stats,
                'runtime_errors': errors[:6],
            })

        report = {
            'created_at': datetime.now(UTC).isoformat(),
            'debug_path': str(debug_file),
            'coverage': {
                'matches_before_publish_window': int(summary.get('matches_before_publish_window') or 0),
                'matches_seen': int(summary.get('matches_seen') or 0),
                'matches_with_offers': int(summary.get('matches_with_offers') or 0),
                'contexts_built': int(summary.get('contexts_built') or 0),
                'self_history_contexts_built': int(summary.get('self_history_contexts_built') or 0),
                'candidates_before_quality': int(summary.get('candidates_before_quality') or 0),
                'candidates_publishable': int(summary.get('candidates_publishable') or 0),
                'published': int(summary.get('published') or 0),
                'published_to_telegram': int(summary.get('published_to_telegram') or 0),
                'derived_candidates_before_quality': int(summary.get('candidates_before_quality_with_derived_market_signal') or 0),
                'derived_publishable': int(summary.get('publishable_with_derived_market_signal') or 0),
            },
            'top_rejections': top_rejections,
            'provider_rate_limits': dict(rate_limit_counter),
            'published_candidates_single_source_context': int(provider_summary.get('published_candidates_single_source_context') or 0),
            'published_candidates_low_book_support': int(provider_summary.get('published_candidates_low_book_support') or 0),
            'providers': provider_rows,
        }
        self.report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        return report
