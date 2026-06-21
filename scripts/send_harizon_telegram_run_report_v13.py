from __future__ import annotations

import importlib.util
import json
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


def _as_int(value: Any) -> int:
    try:
        if value in (None, ''):
            return 0
        if isinstance(value, (list, tuple, set, dict)):
            return len(value)
        return int(float(str(value).replace(',', '.')))
    except Exception:
        return 0


def build_payload() -> dict[str, Any]:
    try:
        from scripts.build_a_tier_funnel_diagnostics import main as funnel_main
        funnel_main()
    except Exception:
        pass
    payload = _base_build_payload()
    payload['version'] = 'harizon-telegram-report-v13-a-tier-funnel'
    diag = payload.setdefault('diagnostics', {})
    diag['a_tier_funnel_diagnostics'] = _load_json(EXPORT_DIR / 'latest-a-tier-funnel-diagnostics.json')
    diag['controlled_fallback_prepublish_guard'] = _load_json(EXPORT_DIR / 'latest-controlled-fallback-prepublish-guard.json')
    return payload


def _extra_lines(payload: dict[str, Any]) -> list[str]:
    diag = payload.get('diagnostics') if isinstance(payload.get('diagnostics'), dict) else {}
    funnel = diag.get('a_tier_funnel_diagnostics') if isinstance(diag.get('a_tier_funnel_diagnostics'), dict) else {}
    guard = diag.get('controlled_fallback_prepublish_guard') if isinstance(diag.get('controlled_fallback_prepublish_guard'), dict) else {}
    contract = diag.get('workflow_env_contract') if isinstance(diag.get('workflow_env_contract'), dict) else {}
    lines: list[str] = []
    if guard or contract:
        daily_existing = guard.get('daily_existing') if isinstance(guard.get('daily_existing'), dict) else {}
        count = _as_int(daily_existing.get('count'))
        limit = _as_int(guard.get('daily_limit')) or 3
        reserved = _as_int(guard.get('reserved_slots') or contract.get('reserved_slots'))
        release = _as_int(guard.get('reserved_slot_release_local_hour') or contract.get('reserved_slot_release_hour')) or 18
        lines.append(f'• Daily slot allocator: published today {count}/{limit}; reserved slots {reserved}; release {release:02d}:00 local.')
    if funnel:
        lines.append(
            f"• A-tier funnel: cover {_as_int(funnel.get('a_cover_rows'))}; active future {_as_int(funnel.get('active_future_a_cover_rows'))}; in publish window {_as_int(funnel.get('in_publish_window_a_cover_rows'))}; raw candidates {_as_int(funnel.get('raw_candidates_before_quality'))}; active A-cover with raw {_as_int(funnel.get('active_a_cover_with_raw_candidate'))}; active without raw {_as_int(funnel.get('active_a_cover_without_raw_candidate'))}; A-cover in fallback {_as_int(funnel.get('a_cover_seen_in_fallback'))}; active in fallback {_as_int(funnel.get('active_a_cover_seen_in_fallback'))}; A-cover published {_as_int(funnel.get('a_cover_published_rows'))}."
        )
    return lines


def render(payload: dict[str, Any]) -> str:
    text = _base_render(payload)
    lines = _extra_lines(payload)
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
