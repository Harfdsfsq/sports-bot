from __future__ import annotations

import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

UTC = timezone.utc
EXPORT = Path('.data/exports')
OUT_JSON = EXPORT / 'latest-detailed-run-report.json'
OUT_TXT = EXPORT / 'latest-detailed-run-report.txt'
SENT_STATE = Path('.data/detailed-run-report-sent.json')


def _load(path: str | Path, default: Any) -> Any:
    try:
        p = Path(path)
        if p.exists() and p.stat().st_size > 0:
            return json.loads(p.read_text(encoding='utf-8', errors='replace'))
    except Exception:
        pass
    return default


def _write(path: str | Path, data: Any) -> None:
    p = Path(path); p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, str):
        p.write_text(data, encoding='utf-8')
    else:
        p.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def _int(v: Any) -> int:
    try:
        if isinstance(v, (list, tuple, set, dict)):
            return len(v)
        return int(float(str(v).replace(',', '.'))) if v not in (None, '') else 0
    except Exception:
        return 0


def _float(v: Any) -> float:
    try:
        return float(str(v).replace(',', '.')) if v not in (None, '') else 0.0
    except Exception:
        return 0.0


def _rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if isinstance(payload, dict):
        for key in ('evaluated', 'candidates', 'checked_candidates', 'rejected_candidates', 'near_misses'):
            if isinstance(payload.get(key), list):
                return [x for x in payload[key] if isinstance(x, dict)]
    return []


def _unwrap(row: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    c = row.get('candidate') if isinstance(row.get('candidate'), dict) else row
    m = row.get('metrics') if isinstance(row.get('metrics'), dict) else c.get('metrics') if isinstance(c.get('metrics'), dict) else {}
    rs = row.get('reject_reasons') or row.get('reasons') or row.get('hard_reject_reasons') or c.get('reject_reasons') or c.get('reasons') or []
    if isinstance(rs, str):
        rs = [rs]
    return c, m, [str(x) for x in rs if str(x).strip()]


def _metric(c: dict[str, Any], m: dict[str, Any], *keys: str) -> float:
    for k in keys:
        if m.get(k) not in (None, ''):
            return _float(m.get(k))
        if c.get(k) not in (None, ''):
            return _float(c.get(k))
    return 0.0


def _reason_counts(report: dict[str, Any], rows: list[dict[str, Any]]) -> Counter:
    out = Counter()
    for key in ('reject_reasons', 'reason_counts', 'rejection_reasons'):
        raw = report.get(key)
        if isinstance(raw, dict):
            for k, v in raw.items():
                out[str(k)] += _int(v)
    if not out:
        for row in rows:
            _, _, rs = _unwrap(row); out.update(rs)
    return out


def _patch_lines() -> list[str]:
    out: list[str] = []
    specs = [
        ('confirmation bridge', 'latest-controlled-fallback-confirmation-bridge.json'),
        ('quality shadow', 'latest-quality-shadow-diagnostics.json'),
        ('current price patch', 'latest-current-price-recheck-value.json'),
        ('zero-raw recovery', 'latest-zero-raw-candidate-recovery.json'),
        ('match-window recovery', 'latest-runtime-match-window-recovery.json'),
        ('evidence/integrity patch', 'latest-fallback-evidence-integrity-runtime-patch.json'),
        ('provider targeting', 'latest-fallback-provider-enrichment-targets.json'),
    ]
    for label, name in specs:
        p = _load(EXPORT / name, {})
        if isinstance(p, dict) and p:
            bits = [str(p.get('status') or p.get('version') or 'ok')]
            for k in ('recovered_matches', 'recovered_candidates', 'patched_odds_source_metrics', 'patched_hard_reject_inputs', 'market_probability_guard_softened_without_real_prices', 'fallback_evaluated'):
                if p.get(k) not in (None, ''):
                    bits.append(f'{k} {p.get(k)}')
            out.append(f'\u2022 {label}: ' + ' | '.join(bits[:5]))
    return out


def _provider_lines(debug: dict[str, Any]) -> list[str]:
    summary = (((debug.get('provider_diagnostics') or {}).get('summary') or {}) if isinstance(debug.get('provider_diagnostics'), dict) else {})
    providers = summary.get('providers') if isinstance(summary.get('providers'), dict) else {}
    out: list[str] = []
    for name in ('odds_api_io', 'bzzoiro', 'sstats', 'football_data', 'thesportsdb', 'sportlogic', 'openfootball'):
        row = providers.get(name) if isinstance(providers.get(name), dict) else {}
        stats = row.get('stats') if isinstance(row.get('stats'), dict) else {}
        if isinstance(stats.get('stats'), dict): stats = stats.get('stats')
        if row or stats:
            out.append(f"\u2022 {name}: data {_int(row.get('matches_with_data'))}/{_int(row.get('items_total'))}, req {stats.get('requests', 0)}, err {stats.get('response_errors', 0)}")
    return out


def _inventory_lines() -> list[str]:
    inv = _load(EXPORT / 'latest-day-inventory-summary.json', {})
    counts = inv.get('counts') if isinstance(inv, dict) and isinstance(inv.get('counts'), dict) else {}
    if not counts:
        return []
    total = _int(counts.get('matches_total'))
    return [
        '\U0001F4E6 \u0414\u043d\u0435\u0432\u043d\u043e\u0439 inventory',
        f"\u2022 \u041c\u0430\u0442\u0447\u0435\u0439 \u0432\u0441\u0435\u0433\u043e: {total}",
        f"\u2022 \u0421 \u043b\u0438\u043d\u0438\u044f\u043c\u0438: {_int(counts.get('matches_with_odds'))}/{total}",
        f"\u2022 \u0421 \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442\u043e\u043c: {_int(counts.get('matches_with_context'))}/{total}",
        f"\u2022 \u0413\u043e\u0442\u043e\u0432\u043e \u043a \u043c\u043e\u0434\u0435\u043b\u0438: {_int(counts.get('matches_ready_for_model'))}/{total}",
        f"\u2022 \u0411\u043b\u0438\u0436\u0430\u0439\u0448\u0438\u0435 6 \u0447\u0430\u0441\u043e\u0432: {_int(counts.get('matches_next_6h_ready'))}/{_int(counts.get('matches_next_6h'))} \u0433\u043e\u0442\u043e\u0432\u043e",
    ]


# --- Run summary freshness -------------------------------------------------
#
# `.logs/debug-last-run.json` is removed between runs (stale_debug_removed) and
# is not always recreated. The builder used to fall back to whatever
# `latest-run-summary.json` happened to be committed in the repository, so the
# same two-day-old numbers were rendered and re-sent on every run. Resolve the
# summary by freshness instead, and say out loud which source was used.

SUMMARY_KEYS = (
    'matches_seen',
    'matches_with_offers',
    'contexts_built',
    'candidates_raw',
    'candidates_before_quality',
    'candidates_publishable',
    'published_to_telegram',
)

SUMMARY_SOURCES = (
    ('debug-last-run', Path('.logs/debug-last-run.json')),
    ('main-run-lifecycle', EXPORT / 'latest-main-run-lifecycle.json'),
    ('run-summary', EXPORT / 'latest-run-summary.json'),
    ('committed-debug-artifact', Path('artifacts/run-bot/debug-last-run.json')),
)

TIMESTAMP_KEYS = (
    'created_at_utc',
    'created_at',
    'finished_at_utc',
    'finished_at',
    'started_at_utc',
    'started_at',
    'updated_at_utc',
    'updated_at',
    'generated_at',
    'timestamp',
)


def _parse_ts(value: Any) -> datetime | None:
    if value in (None, ''):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _payload_ts(payload: Any, path: str | Path) -> datetime | None:
    if isinstance(payload, dict):
        for key in TIMESTAMP_KEYS:
            parsed = _parse_ts(payload.get(key))
            if parsed is not None:
                return parsed
        nested = payload.get('summary')
        if isinstance(nested, dict):
            for key in TIMESTAMP_KEYS:
                parsed = _parse_ts(nested.get(key))
                if parsed is not None:
                    return parsed
    try:
        return datetime.fromtimestamp(Path(path).stat().st_mtime, tz=UTC)
    except Exception:
        return None


def _extract_summary(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    nested = payload.get('summary')
    if isinstance(nested, dict) and any(key in nested for key in SUMMARY_KEYS):
        return dict(nested)
    if any(key in payload for key in SUMMARY_KEYS):
        return {key: payload.get(key) for key in SUMMARY_KEYS if payload.get(key) is not None}
    if isinstance(nested, dict) and nested:
        return dict(nested)
    return {}


def _resolve_summary() -> tuple[dict[str, Any], dict[str, Any]]:
    max_age = _float(os.getenv('DETAILED_RUN_REPORT_MAX_SOURCE_AGE_MINUTES') or 180) or 180.0
    now = datetime.now(UTC)
    considered: list[dict[str, Any]] = []
    best: tuple[dict[str, Any], dict[str, Any]] | None = None
    for name, path in SUMMARY_SOURCES:
        payload = _load(path, None)
        if not isinstance(payload, dict) or not payload:
            considered.append({'source': name, 'path': str(path), 'status': 'missing'})
            continue
        summary = _extract_summary(payload)
        if not summary:
            considered.append({'source': name, 'path': str(path), 'status': 'no_summary'})
            continue
        ts = _payload_ts(payload, path)
        age = ((now - ts).total_seconds() / 60.0) if ts is not None else None
        row = {
            'source': name,
            'path': str(path),
            'status': 'ok',
            'timestamp_utc': ts.isoformat() if ts is not None else None,
            'age_minutes': round(age, 1) if age is not None else None,
            'stale': bool(age is None or age > max_age),
        }
        considered.append(row)
        if best is None:
            best = (row, summary)
            continue
        best_row = best[0]
        if best_row['stale'] and not row['stale']:
            best = (row, summary)
        elif best_row['stale'] and row['stale']:
            best_age = best_row.get('age_minutes')
            if age is not None and (best_age is None or age < best_age):
                best = (row, summary)
    if best is None:
        return {}, {
            'selected': None,
            'selected_path': None,
            'selected_timestamp_utc': None,
            'selected_age_minutes': None,
            'max_age_minutes': max_age,
            'stale': True,
            'sources': considered,
        }
    row, summary = best
    return summary, {
        'selected': row['source'],
        'selected_path': row['path'],
        'selected_timestamp_utc': row['timestamp_utc'],
        'selected_age_minutes': row['age_minutes'],
        'max_age_minutes': max_age,
        'stale': row['stale'],
        'sources': considered,
    }


def _freshness_lines(freshness: dict[str, Any]) -> list[str]:
    if not isinstance(freshness, dict) or not freshness.get('selected'):
        return [
            '\U0001F552 \u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a \u0446\u0438\u0444\u0440: \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d',
            '\u2022 \u041d\u0438 debug-last-run, \u043d\u0438 main-run-lifecycle, \u043d\u0438 run-summary \u044d\u0442\u043e\u0433\u043e \u043f\u0440\u043e\u0433\u043e\u043d\u0430 \u043d\u0435 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b.',
            '',
        ]
    age = freshness.get('selected_age_minutes')
    age_text = f'{_float(age):.0f} \u043c\u0438\u043d \u043d\u0430\u0437\u0430\u0434' if age is not None else '\u0432\u043e\u0437\u0440\u0430\u0441\u0442 \u043d\u0435\u0438\u0437\u0432\u0435\u0441\u0442\u0435\u043d'
    if freshness.get('stale'):
        return [
            f"\U0001F552 \u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a \u0446\u0438\u0444\u0440: {freshness.get('selected')} ({age_text}) \u2014 \u26a0\ufe0f \u0423\u0421\u0422\u0410\u0420\u0415\u041b",
            '\u2022 \u0421\u0432\u0435\u0436\u0438\u0439 debug/run summary \u0442\u0435\u043a\u0443\u0449\u0435\u0433\u043e \u043f\u0440\u043e\u0433\u043e\u043d\u0430 \u043d\u0435 \u043d\u0430\u0439\u0434\u0435\u043d: \u0446\u0438\u0444\u0440\u044b \u043d\u0438\u0436\u0435 \u043e\u0442\u043d\u043e\u0441\u044f\u0442\u0441\u044f \u043a \u043f\u0440\u0435\u0434\u044b\u0434\u0443\u0449\u0435\u043c\u0443 \u043f\u0440\u043e\u0433\u043e\u043d\u0443.',
            '',
        ]
    return [f"\U0001F552 \u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a \u0446\u0438\u0444\u0440: {freshness.get('selected')} ({age_text}) \u2014 \u0441\u0432\u0435\u0436\u0438\u0439", '']


def build() -> dict[str, Any]:
    debug = _load('.logs/debug-last-run.json', {})
    if not isinstance(debug, dict):
        debug = {}
    fallback = _load(EXPORT / 'latest-controlled-fallback-report.json', {}) or _load('artifacts/controlled-fallback-report.json', {})
    rows = _rows(fallback)
    reasons = _reason_counts(fallback if isinstance(fallback, dict) else {}, rows)
    summary, freshness = _resolve_summary()
    near = []
    for row in rows[:12]:
        c, m, rs = _unwrap(row)
        near.append({'candidate': c, 'metrics': m, 'reasons': rs, 'score': [_metric(c,m,'canonical_ev_pct','ev_pct'), _metric(c,m,'canonical_edge_pp','edge_pp'), _metric(c,m,'quality_score','quality')]})
    near.sort(key=lambda x: x['score'], reverse=True)
    return {'created_at': datetime.now(UTC).isoformat(), 'summary': summary, 'summary_freshness': freshness, 'candidate_counts': {'evaluated': len(rows), 'rescue_checked': _int((fallback or {}).get('rescue_candidates_checked') if isinstance(fallback, dict) else len(rows)), 'selected_count': _int((fallback or {}).get('selected_count') if isinstance(fallback, dict) else 0)}, 'reason_counts': dict(reasons.most_common(12)), 'near_misses': near[:8], 'patch_lines': _patch_lines(), 'provider_work_lines': _provider_lines(debug), 'coverage_pipeline_lines': _inventory_lines(), 'published': bool(isinstance(fallback, dict) and (fallback.get('published') or fallback.get('selected_count')))}


def render(payload: dict[str, Any]) -> str:
    lines = ['\U0001F9FE \u041f\u043e\u0434\u0440\u043e\u0431\u043d\u044b\u0439 \u043e\u0442\u0447\u0451\u0442 run \u2014 ' + ('\u043f\u0440\u043e\u0433\u043d\u043e\u0437 \u043e\u043f\u0443\u0431\u043b\u0438\u043a\u043e\u0432\u0430\u043d' if payload.get('published') else '\u043f\u0440\u043e\u0433\u043d\u043e\u0437\u043e\u0432 \u043d\u0435\u0442'), '']
    lines += _freshness_lines(payload.get('summary_freshness') or {})
    if payload.get('coverage_pipeline_lines'):
        lines += payload['coverage_pipeline_lines'] + ['']
    s = payload.get('summary') or {}; c = payload.get('candidate_counts') or {}
    lines += ['\u2699\ufe0f \u0427\u0442\u043e \u0441\u0434\u0435\u043b\u0430\u043b \u0441\u043a\u0440\u0438\u043f\u0442', f"\u2022 \u041c\u0430\u0442\u0447\u0438: {_int(s.get('matches_seen'))} | \u0441 \u043b\u0438\u043d\u0438\u044f\u043c\u0438: {_int(s.get('matches_with_offers'))} | \u043a\u043e\u043d\u0442\u0435\u043a\u0441\u0442\u043e\u0432: {_int(s.get('contexts_built'))}", f"\u2022 \u041a\u0430\u043d\u0434\u0438\u0434\u0430\u0442\u044b: raw {_int(s.get('candidates_raw'))} | \u0434\u043e \u043a\u0430\u0447\u0435\u0441\u0442\u0432\u0430 {_int(s.get('candidates_before_quality'))} | publishable {_int(s.get('candidates_publishable'))}", f"\u2022 \u0420\u0435\u0437\u0435\u0440\u0432 \u043f\u0440\u043e\u0432\u0435\u0440\u0438\u043b: {c.get('rescue_checked', 0)} | \u043e\u0446\u0435\u043d\u0435\u043d\u043e \u0432 \u043e\u0442\u0447\u0451\u0442\u0435: {c.get('evaluated', 0)} | \u0432\u044b\u0431\u0440\u0430\u043d\u043e: {c.get('selected_count', 0)}", '']
    if payload.get('patch_lines'):
        lines += ['\U0001F9E9 \u0414\u0438\u0430\u0433\u043d\u043e\u0441\u0442\u0438\u043a\u0430 \u043f\u0430\u0442\u0447\u0435\u0439'] + payload['patch_lines'] + ['']
    if payload.get('provider_work_lines'):
        lines += ['\U0001F4E1 \u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a\u0438 / \u0444\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u0440\u0430\u0431\u043e\u0442\u0430'] + payload['provider_work_lines'] + ['']
    else:
        lines += ['\U0001F4E1 \u0418\u0441\u0442\u043e\u0447\u043d\u0438\u043a\u0438 / \u0444\u0430\u043a\u0442\u0438\u0447\u0435\u0441\u043a\u0430\u044f \u0440\u0430\u0431\u043e\u0442\u0430', '\u2022 \u0421\u0432\u0435\u0436\u0430\u044f \u0434\u0438\u0430\u0433\u043d\u043e\u0441\u0442\u0438\u043a\u0430 \u043f\u0440\u043e\u0432\u0430\u0439\u0434\u0435\u0440\u043e\u0432 \u043d\u0435\u0434\u043e\u0441\u0442\u0443\u043f\u043d\u0430: .logs/debug-last-run.json \u043d\u0435 \u0431\u044b\u043b \u0441\u043e\u0437\u0434\u0430\u043d \u044d\u0442\u0438\u043c \u043f\u0440\u043e\u0433\u043e\u043d\u043e\u043c.', '']
    reasons = Counter(payload.get('reason_counts') or {})
    if reasons:
        total = sum(reasons.values()) or 1
        lines.append('\U0001F6AB \u041f\u043e\u0447\u0435\u043c\u0443 \u043d\u0435 \u043f\u0440\u043e\u0448\u043b\u0438')
        for r, n in reasons.most_common(10):
            lines.append(f'\u2022 {r} \u2014 {n} ({n/total*100:.0f}%)')
        lines.append('')
    near = payload.get('near_misses') or []
    if near:
        lines.append('\u26a0\ufe0f \u041f\u043e\u0433\u0440\u0430\u043d\u0438\u0447\u043d\u044b\u0435 \u043a\u0430\u043d\u0434\u0438\u0434\u0430\u0442\u044b')
        for i, item in enumerate(near[:8], 1):
            cand = item.get('candidate') or {}; m = item.get('metrics') or {}; rs = item.get('reasons') or []
            home = cand.get('home_team_ru') or cand.get('home_team') or cand.get('home') or '\u043d/\u0434'
            away = cand.get('away_team_ru') or cand.get('away_team') or cand.get('away') or '\u043d/\u0434'
            sel = cand.get('selection') or cand.get('market') or '\u043d/\u0434'
            odds = _metric(cand, m, 'odds', 'selected_odds')
            lines.append(f"{i}. {home} \u2014 {away} | {sel} @{odds:.2f}")
            lines.append(f"   \u2022 EV {_metric(cand,m,'canonical_ev_pct','ev_pct'):+.1f}% | edge {_metric(cand,m,'canonical_edge_pp','edge_pp'):+.1f} \u043f.\u043f. | q {_metric(cand,m,'quality_score','quality'):.1f}")
            if rs: lines.append('   \u2022 \u043d\u0435 \u043f\u0440\u043e\u0448\u043b\u043e: ' + '; '.join(str(x) for x in rs[:5]))
        lines.append('')
    top = reasons.most_common(1)[0][0] if reasons else ''
    lines += ['\U0001F4CC \u0412\u044b\u0432\u043e\u0434']
    if (payload.get('summary_freshness') or {}).get('stale'):
        lines.append('\u2022 \u041e\u0442\u0447\u0451\u0442 \u043f\u043e\u0441\u0442\u0440\u043e\u0435\u043d \u043d\u0430 \u0443\u0441\u0442\u0430\u0440\u0435\u0432\u0448\u0435\u043c \u0441\u0440\u0435\u0437\u0435: \u0441\u043d\u0430\u0447\u0430\u043b\u0430 \u043d\u0443\u0436\u043d\u043e \u0432\u0435\u0440\u043d\u0443\u0442\u044c \u0441\u0432\u0435\u0436\u0438\u0439 debug/run summary.')
    if 'price_integrity' in top or 'price integrity' in top:
        lines.append('\u2022 \u0413\u043b\u0430\u0432\u043d\u044b\u0439 \u0431\u043b\u043e\u043a\u0435\u0440 \u2014 price integrity: \u0432\u044b\u0431\u0440\u0430\u043d\u043d\u0430\u044f \u0446\u0435\u043d\u0430 \u0432\u044b\u0433\u043b\u044f\u0434\u0438\u0442 \u0432\u044b\u0431\u0440\u043e\u0441\u043e\u043c \u043e\u0442\u043d\u043e\u0441\u0438\u0442\u0435\u043b\u044c\u043d\u043e \u0440\u044b\u043d\u043a\u0430.')
    elif 'recheck' in top or 'line' in top:
        lines.append('\u2022 \u0413\u043b\u0430\u0432\u043d\u044b\u0439 \u0431\u043b\u043e\u043a\u0435\u0440 \u2014 \u043e\u0436\u0438\u0434\u0430\u043d\u0438\u0435 \u0444\u0438\u043d\u0430\u043b\u044c\u043d\u043e\u0433\u043e line/current-price recheck \u043f\u0435\u0440\u0435\u0434 \u043f\u0443\u0431\u043b\u0438\u043a\u0430\u0446\u0438\u0435\u0439.')
    else:
        lines.append('\u2022 \u041f\u0440\u043e\u0433\u043d\u043e\u0437\u043e\u0432 \u043d\u0435\u0442 \u0438\u0437-\u0437\u0430 \u043a\u043e\u043c\u0431\u0438\u043d\u0430\u0446\u0438\u0438 value, \u043a\u0430\u0447\u0435\u0441\u0442\u0432\u0430, \u0438\u0441\u0442\u043e\u0447\u043d\u0438\u043a\u043e\u0432 \u0438 \u0444\u0438\u043d\u0430\u043b\u044c\u043d\u044b\u0445 safety-guard\u2019\u043e\u0432.')
    lines += ['', '\u2696\ufe0f \u0414\u0438\u0441\u043a\u043b\u0435\u0439\u043c\u0435\u0440: \u0430\u043d\u0430\u043b\u0438\u0442\u0438\u043a\u0430 \u0431\u043e\u0442\u0430 \u043d\u0435 \u0433\u0430\u0440\u0430\u043d\u0442\u0438\u0440\u0443\u0435\u0442 \u0440\u0435\u0437\u0443\u043b\u044c\u0442\u0430\u0442 \u0438 \u043d\u0435 \u044f\u0432\u043b\u044f\u0435\u0442\u0441\u044f \u0444\u0438\u043d\u0430\u043d\u0441\u043e\u0432\u043e\u0439 \u0440\u0435\u043a\u043e\u043c\u0435\u043d\u0434\u0430\u0446\u0438\u0435\u0439.']
    return '\n'.join(lines)


def _split(text: str, limit: int) -> list[str]:
    if len(text) <= limit: return [text]
    parts, cur, size = [], [], 0
    for line in text.splitlines():
        add = len(line) + 1
        if cur and size + add > limit:
            parts.append('\n'.join(cur)); cur=[]; size=0
        cur.append(line); size += add
    if cur: parts.append('\n'.join(cur))
    total = len(parts)
    return [f'\U0001F9FE \u041f\u043e\u0434\u0440\u043e\u0431\u043d\u044b\u0439 \u043e\u0442\u0447\u0451\u0442 run \u2014 \u0447\u0430\u0441\u0442\u044c {i}/{total}\n\n{p}' for i, p in enumerate(parts, 1)]


def send(text: str) -> dict[str, Any]:
    token = os.getenv('TELEGRAM_TOKEN') or os.getenv('TELEGRAM_BOT_TOKEN'); chat = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat: return {'sent': False, 'reason': 'missing_credentials'}
    h = hashlib.sha1(text.encode('utf-8')).hexdigest(); state = _load(SENT_STATE, {})
    if isinstance(state, dict) and state.get('last_hash') == h and str(os.getenv('DETAILED_RUN_REPORT_FORCE_SEND','')).lower() not in {'1','true','yes','on','force'}:
        return {'sent': False, 'reason': 'unchanged', 'hash': h}
    url = 'https://api.telegram.org/bot' + str(token) + '/sendMessage'
    parts = _split(text, _int(os.getenv('TELEGRAM_MESSAGE_SOFT_LIMIT') or 3900) or 3900)
    try:
        for part in parts:
            data = parse.urlencode({'chat_id': chat, 'text': part, 'disable_web_page_preview': 'true'}).encode('utf-8')
            req = request.Request(url, data=data, method='POST')
            with request.urlopen(req, timeout=20) as resp: resp.read()
    except Exception as exc:
        return {'sent': False, 'reason': 'telegram_send_error', 'error': repr(exc), 'hash': h}
    _write(SENT_STATE, {'last_hash': h, 'sent_at': datetime.now(UTC).isoformat(), 'parts': len(parts)})
    return {'sent': True, 'parts': len(parts), 'hash': h}


def main() -> int:
    payload = build(); text = render(payload); payload['text'] = text
    should_send = str(os.getenv('DETAILED_RUN_REPORT_SEND_TELEGRAM','')).lower() in {'1','true','yes','on','force'}
    payload['telegram'] = send(text) if should_send else {'sent': False, 'reason': 'disabled'}
    _write(OUT_JSON, payload); _write(OUT_TXT, text); print(text)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
