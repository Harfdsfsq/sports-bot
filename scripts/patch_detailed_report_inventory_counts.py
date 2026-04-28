from __future__ import annotations

from pathlib import Path

REPORT_PATH = Path('scripts/build_detailed_run_report.py')

INVENTORY_FUNCTION = r'''

def inventory_count_snapshot() -> dict[str, Any]:
    """Return cumulative day-inventory counts for the detailed Telegram report.

    The main report's first line is intentionally per-run. These inventory
    counts show the accumulated daily coverage so operators can verify that the
    2-hour runs are enriching the same match pool instead of starting over.
    """
    paths = [
        Path('.data/day_inventory/today.json'),
        Path('.data/day_inventory/current.json'),
        Path('.data/day_inventory/latest.json'),
    ]
    for path in paths:
        payload = load_json(path, {})
        if not isinstance(payload, dict):
            continue
        counts = payload.get('counts') if isinstance(payload.get('counts'), dict) else {}
        if not counts:
            continue
        return {
            'matches_total': as_int(counts.get('matches_total')),
            'matches_with_odds': as_int(counts.get('matches_with_odds')),
            'matches_with_context': as_int(counts.get('matches_with_context')),
            'matches_ready_for_model': as_int(counts.get('matches_ready_for_model')),
            'matches_ready_for_publish': as_int(counts.get('matches_ready_for_publish')),
            'coverage_rows_seen_last_run': as_int(counts.get('coverage_rows_seen_last_run')),
            'runtime_matches_with_odds_last_run': as_int(counts.get('runtime_matches_with_odds_last_run')),
            'runtime_matches_with_context_last_run': as_int(counts.get('runtime_matches_with_context_last_run')),
        }
    return {}
'''


def main() -> int:
    if not REPORT_PATH.exists():
        print(f'skip: {REPORT_PATH} not found')
        return 0
    src = REPORT_PATH.read_text(encoding='utf-8')
    original = src

    if 'def inventory_count_snapshot()' not in src:
        marker = '\ndef build_payload() -> dict[str, Any]:\n'
        if marker in src:
            src = src.replace(marker, INVENTORY_FUNCTION + marker, 1)
        else:
            print('warn: build_payload marker not found')

    if '"inventory_counts": inventory_count_snapshot(),' not in src:
        marker = '        "provider_lines": provider_summary(),\n'
        if marker in src:
            src = src.replace(marker, marker + '        "inventory_counts": inventory_count_snapshot(),\n', 1)
        else:
            print('warn: payload provider_lines marker not found')

    if 'Накоплено за день:' not in src:
        old = "    lines.append(f\"• Матчи: {as_int(summary.get('matches_seen'))} | с линиями: {as_int(summary.get('matches_with_offers'))} | контекстов: {as_int(summary.get('contexts_built'))}\")\n"
        new = old + (
            "    inventory_counts = payload.get('inventory_counts') or {}\n"
            "    if inventory_counts:\n"
            "        lines.append(\n"
            "            f\"• Накоплено за день: матчей {as_int(inventory_counts.get('matches_total'))} | \"\n"
            "            f\"с линиями {as_int(inventory_counts.get('matches_with_odds'))} | \"\n"
            "            f\"контекстов {as_int(inventory_counts.get('matches_with_context'))} | \"\n"
            "            f\"готово к модели {as_int(inventory_counts.get('matches_ready_for_model'))}\"\n"
            "        )\n"
        )
        if old in src:
            src = src.replace(old, new, 1)
        else:
            print('warn: render match summary marker not found')

    if src != original:
        REPORT_PATH.write_text(src, encoding='utf-8')
        print(f'patched: {REPORT_PATH}')
    else:
        print(f'already patched or no changes: {REPORT_PATH}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
