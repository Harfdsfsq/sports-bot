from __future__ import annotations

import importlib.util
import json
import re
from pathlib import Path
from typing import Any

V12_PATH = Path(__file__).with_name('send_harizon_telegram_run_report_v12.py')
EXPORT_DIR = Path('.data/exports')


def _load_v12() -> Any:
    spec = importlib.util.spec_from_file_location('harizon_report_v12', V12_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f'cannot load {V12_PATH}')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


v12 = _load_v12()
v9 = v12.v9
_base_build_payload = v12.build_payload
_base_render = v12.render


def _load_json(path: Path) -> dict[str, Any]:
    try:
        if path.exists() and path.stat().st_size > 0:
            data = json.loads(path.read_text(encoding='utf-8'))
            return data if isinstance(data, dict) else {}
    except Exception:
        pass
    return {}


def _load_run_bot_status() -> dict[str, Any]:
    path = EXPORT_DIR / 'latest-run-bot-step-status.json'
    payload = _load_json(path)
    if payload:
        return payload
    try:
        if path.exists() and path.stat().st_size > 0:
            text = path.read_text(encoding='utf-8', errors='replace').strip()
            lower = text.lower()
            if 'run bot ok' in lower:
                status = 'ok'
            elif 'failed' in lower or 'timed out' in lower:
                status = 'failed_or_timed_out'
            else:
                status = 'text_status_unknown'
            return {'status': status, 'raw_status_text': text, 'legacy_text_status_artifact': True}
    except Exception:
        pass
    log_path = EXPORT_DIR / 'latest-run-bot.log'
    try:
        if log_path.exists() and log_path.stat().st_size > 0:
            return {'status': 'log_exists_no_status_artifact', 'log_size_bytes': log_path.stat().st_size}
    except Exception:
        pass
    return {}


def _as_int(value: Any) -> int:
    try:
        if value in (None, ''):
            return 0
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value).replace(',', '.')))
    except Exception:
        return 0


def _refresh_inventory_coverage() -> dict[str, Any]:
    status: dict[str, Any] = {'status': 'not_started'}
    try:
        from scripts.guard_day_inventory_no_shrink import repair as guard_repair
        guard_report = guard_repair()
        status['guard'] = guard_report if isinstance(guard_report, dict) else {'status': str(guard_report)}
    except Exception as exc:
        status['guard'] = {'status': 'error', 'error': f'{type(exc).__name__}: {exc}'}
    try:
        from scripts.day_inventory_cumulative_coverage import main as cumulative_main
        code = cumulative_main()
        status['cumulative'] = {'status': 'ok' if code == 0 else 'non_zero', 'code': code}
    except Exception as exc:
        status['cumulative'] = {'status': 'error', 'error': f'{type(exc).__name__}: {exc}'}
    try:
        from scripts.backfill_inventory_bookmaker_coverage import main as backfill_main
        code = backfill_main()
        status['bookmaker_backfill'] = {'status': 'ok' if code == 0 else 'non_zero', 'code': code}
    except Exception as exc:
        status['bookmaker_backfill'] = {'status': 'error', 'error': f'{type(exc).__name__}: {exc}'}
    try:
        out = EXPORT_DIR / 'latest-telegram-report-inventory-refresh.json'
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(status, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')
    except Exception:
        pass
    return status


def _promote_a_cover_candidates() -> dict[str, Any]:
    try:
        from scripts.promote_a_cover_value_candidates import main as promote_main
        code = promote_main()
        return {'status': 'ok' if code == 0 else 'non_zero', 'code': code}
    except Exception as exc:
        return {'status': 'error', 'error': f'{type(exc).__name__}: {exc}'}


def _rendered_candidate_counter(text: str) -> tuple[int, int] | None:
    match = re.search(r'Raw/candidates before quality:\s*(\d+)\s*/\s*(\d+)', text)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def _top_counter_items(value: Any, limit: int = 4) -> str:
    if not isinstance(value, dict):
        return 'n/a'
    items = []
    for key, raw in value.items():
        count = _as_int(raw)
        if count > 0:
            items.append((str(key), count))
    items.sort(key=lambda item: item[1], reverse=True)
    if not items:
        return 'n/a'
    return '; '.join(f'{key} {count}' for key, count in items[:limit])


def build_payload() -> dict[str, Any]:
    refresh_status = _refresh_inventory_coverage()
    promotion_status = _promote_a_cover_candidates()
    try:
        from scripts.build_a_tier_funnel_diagnostics import main as funnel_main
        funnel_main()
    except Exception:
        pass
    try:
        from scripts.build_a_cover_candidate_gap_report import main as gap_main
        gap_main()
    except Exception:
        pass
    payload = _base_build_payload()
    payload['version'] = 'harizon-telegram-report-v13-a-tier-funnel'
    diag = payload.setdefault('diagnostics', {})
    diag['telegram_report_inventory_refresh'] = refresh_status
    diag['telegram_report_a_cover_promotion_refresh'] = promotion_status
    diag['run_bot_step_status'] = _load_run_bot_status()
    diag['pytest_status'] = _load_json(EXPORT_DIR / 'latest-pytest-status.json')
    diag['a_tier_funnel_diagnostics'] = _load_json(EXPORT_DIR / 'latest-a-tier-funnel-diagnostics.json')
    diag['a_cover_candidate_gap_report'] = _load_json(EXPORT_DIR / 'latest-a-cover-candidate-gap-report.json')
    diag['a_cover_value_promotion'] = _load_json(EXPORT_DIR / 'latest-a-cover-value-promotion.json')
    diag['controlled_fallback_prepublish_guard'] = _load_json(EXPORT_DIR / 'latest-controlled-fallback-prepublish-guard.json')
    diag['day_inventory_cumulative_coverage'] = _load_json(EXPORT_DIR / 'latest-day-inventory-cumulative-coverage.json')
    return payload


def _extra_lines(payload: dict[str, Any], base_text: str = '') -> list[str]:
    diag = payload.get('diagnostics') if isinstance(payload.get('diagnostics'), dict) else {}
    funnel = diag.get('a_tier_funnel_diagnostics') if isinstance(diag.get('a_tier_funnel_diagnostics'), dict) else {}
    gap = diag.get('a_cover_candidate_gap_report') if isinstance(diag.get('a_cover_candidate_gap_report'), dict) else {}
    promotion = diag.get('a_cover_value_promotion') if isinstance(diag.get('a_cover_value_promotion'), dict) else {}
    guard = diag.get('controlled_fallback_prepublish_guard') if isinstance(diag.get('controlled_fallback_prepublish_guard'), dict) else {}
    contract = diag.get('workflow_env_contract') if isinstance(diag.get('workflow_env_contract'), dict) else {}
    cumulative = diag.get('day_inventory_cumulative_coverage') if isinstance(diag.get('day_inventory_cumulative_coverage'), dict) else {}
    refresh = diag.get('telegram_report_inventory_refresh') if isinstance(diag.get('telegram_report_inventory_refresh'), dict) else {}
    promotion_refresh = diag.get('telegram_report_a_cover_promotion_refresh') if isinstance(diag.get('telegram_report_a_cover_promotion_refresh'), dict) else {}
    run_status = diag.get('run_bot_step_status') if isinstance(diag.get('run_bot_step_status'), dict) else {}
    pytest_status = diag.get('pytest_status') if isinstance(diag.get('pytest_status'), dict) else {}
    lines: list[str] = []
    if run_status:
        status_text = str(run_status.get('status') or 'unknown')
        code = run_status.get('exit_code')
        suffix = f'; exit {code}' if code not in (None, '') else ''
        if run_status.get('legacy_text_status_artifact'):
            suffix += '; legacy text status'
        lines.append(f'• Run bot step: {status_text}{suffix}; started {run_status.get("started_at_utc") or "n/a"}; finished {run_status.get("finished_at_utc") or "n/a"}.')
    else:
        lines.append('• Run bot step: no fresh step-status artifact — отчёт может быть post-failure, а не полноценный run.')
    if pytest_status:
        status_text = str(pytest_status.get('status') or 'unknown')
        code = pytest_status.get('original_exitstatus', pytest_status.get('exit_code'))
        nb = pytest_status.get('non_blocking_for_cron')
        lines.append(f'• Pytest: {status_text}; original exit {code}; non-blocking {bool(nb)}.')
    lines.append('• Effective A/B contract: A=2 bookmaker/line confirmations + 2 context sources; second API odds-source is diagnostic. B=1 odds-source + 2 books + 1 context.')
    if promotion_refresh:
        lines.append(f"• Report-time A-cover promotion refresh: {promotion_refresh.get('status')}; code {promotion_refresh.get('code', 'n/a')}.")
    if guard or contract:
        daily_existing = guard.get('daily_existing') if isinstance(guard.get('daily_existing'), dict) else {}
        count = _as_int(daily_existing.get('count'))
        limit = _as_int(guard.get('daily_limit')) or 3
        reserved = _as_int(guard.get('reserved_slots') or contract.get('reserved_slots'))
        release = _as_int(guard.get('reserved_slot_release_local_hour') or contract.get('reserved_slot_release_hour')) or 18
        lines.append(f'• Daily slot allocator: published today {count}/{limit}; reserved slots {reserved}; release {release:02d}:00 local.')
    if funnel:
        rendered_counter = _rendered_candidate_counter(base_text)
        raw_after_quality = _as_int(funnel.get('raw_candidates_after_quality'))
        raw_before_quality = _as_int(funnel.get('raw_candidates_before_quality'))
        if rendered_counter is not None:
            raw_after_quality, raw_before_quality = rendered_counter
        lines.append(
            f"• A-tier funnel: cover {_as_int(funnel.get('a_cover_rows'))}; active future {_as_int(funnel.get('active_future_a_cover_rows'))}; in publish window {_as_int(funnel.get('in_publish_window_a_cover_rows'))}; raw/quality {raw_after_quality}/{raw_before_quality}; active A-cover with raw {_as_int(funnel.get('active_a_cover_with_raw_candidate'))}; active without raw {_as_int(funnel.get('active_a_cover_without_raw_candidate'))}; A-cover in fallback {_as_int(funnel.get('a_cover_seen_in_fallback'))}; active in fallback {_as_int(funnel.get('active_a_cover_seen_in_fallback'))}; A-cover published {_as_int(funnel.get('a_cover_published_rows'))}."
        )
    if promotion:
        status = str(promotion.get('status') or 'n/a')
        existing_stats = promotion.get('existing_rescue_stats') if isinstance(promotion.get('existing_rescue_stats'), dict) else {}
        error = str(promotion.get('error') or '').strip()
        suffix = f'; error {error}' if error else ''
        lines.append(
            f"• A-cover promotion: status {status}; promoted {_as_int(promotion.get('promoted_count'))}; active {_as_int(promotion.get('active_a_cover_rows'))}; in-window {_as_int(promotion.get('in_publish_window_a_cover_rows'))}; considered {_as_int(promotion.get('considered_a_cover_rows'))}; rescue kept {_as_int(existing_stats.get('kept'))}/{_as_int(existing_stats.get('loaded'))}; dropped stale {_as_int(existing_stats.get('dropped_outside_window'))}; top skips {_top_counter_items(promotion.get('reason_counts'), 5)}{suffix}."
        )
    if gap:
        lines.append(
            f"• A-cover candidate gap: active {_as_int(gap.get('active_future_a_cover_rows'))}; in-window {_as_int(gap.get('in_publish_window_a_cover_rows'))}; statuses {_top_counter_items(gap.get('status_counts'), 3)}; top reasons {_top_counter_items(gap.get('reason_counts'), 5)}."
        )
    if cumulative:
        selected = cumulative.get('inventory_selection') if isinstance(cumulative.get('inventory_selection'), dict) else {}
        selected_rows = _as_int(selected.get('selected_rows')) or _as_int(cumulative.get('matches_total'))
        date_rows = _as_int(selected.get('date_path_rows'))
        if selected_rows:
            suffix = ''
            if date_rows and selected_rows > date_rows:
                suffix = f'; защищено от shrink {date_rows}→{selected_rows}'
            refresh_guard = refresh.get('guard') if isinstance(refresh.get('guard'), dict) else {}
            guard_status = str(refresh_guard.get('status') or '').strip()
            guard_note = f'; refresh {guard_status}' if guard_status else ''
            lines.append(f"• Inventory no-shrink source: selected {selected_rows} rows from {selected.get('selected_path') or cumulative.get('inventory_path')}{suffix}{guard_note}.")
    return lines


def render(payload: dict[str, Any]) -> str:
    text = _base_render(payload)
    lines = _extra_lines(payload, text)
    if not lines:
        return text
    insert = '\n'.join(lines)
    marker = '• Shadow ranking after daily cap:'
    if marker in text:
        return text.replace(marker, insert + '\n' + marker, 1)
    marker = '• Inventory target-expand stage:'
    if marker in text:
        return text.replace(marker, insert + '\n' + marker, 1)
    return text


v9.v8.v7.v5.build_payload = build_payload
v9.v8.v7.v5.render = render
v9.v8.v7.build_payload = build_payload
v9.v8.v7.render = render


if __name__ == '__main__':
    raise SystemExit(v9.v8.v7.v5.main())
