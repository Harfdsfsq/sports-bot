from __future__ import annotations

from pathlib import Path

WORKFLOW = Path('.github/workflows/run-bot.yml')


def replace_once(text: str, old: str, new: str) -> str:
    if old not in text:
        return text
    return text.replace(old, new, 1)


def insert_after(text: str, marker: str, block: str) -> str:
    if block.strip() in text:
        return text
    if marker not in text:
        return text
    return text.replace(marker, marker + block, 1)


def insert_before(text: str, marker: str, block: str) -> str:
    if block.strip() in text:
        return text
    if marker not in text:
        return text
    return text.replace(marker, block + marker, 1)


def main() -> int:
    if not WORKFLOW.exists():
        raise SystemExit(f'missing workflow: {WORKFLOW}')
    text = WORKFLOW.read_text(encoding='utf-8')
    original = text

    # Give post-run cache/upload room, but reduce payload below so this is a safety net.
    text = replace_once(text, 'timeout-minutes: 35', 'timeout-minutes: 45')

    # Make workflow-level env match the runtime policy script, so GitHub UI/report is not misleading.
    text = text.replace('CONTROLLED_FALLBACK_STRICT_MATCH_MARKET_DEDUPE: "true"', 'CONTROLLED_FALLBACK_STRICT_MATCH_MARKET_DEDUPE: "false"')
    if 'CONTROLLED_FALLBACK_REQUIRE_TOTALS_SANITY_FOR_TELEGRAM:' not in text:
        marker = '      CONTROLLED_FALLBACK_TIER_B_REQUIRE_INDEPENDENT_SOURCES: "false"\n'
        text = insert_after(text, marker, '      CONTROLLED_FALLBACK_REQUIRE_TOTALS_SANITY_FOR_TELEGRAM: "false"\n')

    # Restore candidates that were intentionally waiting for the next line-movement check.
    marker = '          python -u scripts/apply_publication_family_policy.py || true\n'
    restore_block = '          python -u scripts/restore_awaiting_movement_candidates.py || true\n'
    # Only add to the Publish controlled fallback block, not every occurrence: use the exact block around fallback.
    fallback_anchor = '      - name: Publish controlled fallback\n        if: always()\n        shell: bash\n        run: |\n'
    if restore_block.strip() not in text and fallback_anchor in text:
        start = text.index(fallback_anchor)
        after = text[start:]
        idx = after.find(marker)
        if idx >= 0:
            absolute = start + idx + len(marker)
            text = text[:absolute] + restore_block + text[absolute:]

    # Persist candidates that are blocked only because the next scheduled run is before kickoff.
    publish_line = '          python scripts/publish_controlled_fallback_guarded.py || python scripts/publish_controlled_fallback.py || true\n'
    persist_block = (
        '          python -u scripts/persist_awaiting_movement_candidates.py || true\n'
        '          python -u scripts/build_fresh_b_cover_diagnostics.py || true\n'
        '          cp .data/exports/latest-awaiting-movement-candidates.json artifacts/run-bot/latest-awaiting-movement-candidates.json 2>/dev/null || true\n'
        '          cp .data/exports/latest-awaiting-movement-restore.json artifacts/run-bot/latest-awaiting-movement-restore.json 2>/dev/null || true\n'
        '          cp .data/exports/latest-fresh-b-cover-diagnostics.json artifacts/run-bot/latest-fresh-b-cover-diagnostics.json 2>/dev/null || true\n'
    )
    text = insert_after(text, publish_line, persist_block)

    # Build fresh/cumulative diagnostics once after runtime and once after cumulative coverage is updated.
    runbot_diag_marker = '          python -u scripts/build_b_cover_candidate_gap_report.py || true\n'
    runbot_diag_block = '          python -u scripts/build_fresh_b_cover_diagnostics.py || true\n'
    text = insert_after(text, runbot_diag_marker, runbot_diag_block)

    cumulative_marker = '          cp .data/exports/latest-b-cover-candidate-gap-report.csv artifacts/run-bot/latest-b-cover-candidate-gap-report.csv 2>/dev/null || true\n'
    cumulative_block = (
        '          python -u scripts/build_fresh_b_cover_diagnostics.py || true\n'
        '          cp .data/exports/latest-fresh-b-cover-diagnostics.json artifacts/run-bot/latest-fresh-b-cover-diagnostics.json 2>/dev/null || true\n'
    )
    text = insert_after(text, cumulative_marker, cumulative_block)

    # Narrow runtime commit. Do not commit full cache/exports/date-folders every run.
    old_git_add = '          git add -f .data/state.json .data/published-candidate-index.json .data/fallback-sent-index.json .data/candidate-lifecycle-state.json .data/exports .data/cache .data/day_inventory .data/line_history .data/provider_quota_governor_state.json .data/provider_request_budget_state.json .data/provider_quota_state.json || true\n'
    new_git_add = (
        '          git add -f .data/state.json .data/published-candidate-index.json .data/fallback-sent-index.json .data/candidate-lifecycle-state.json \\\n'
        '            .data/exports/latest-*.json .data/exports/latest-*.txt .data/exports/latest-*.csv \\\n'
        '            .data/day_inventory/current.json .data/day_inventory/latest.json .data/day_inventory/today.json .data/day_inventory/${DAY_INVENTORY_CACHE_DATE}.json \\\n'
        '            .data/line_history/latest.json .data/line_history/${DAY_INVENTORY_CACHE_DATE}.json \\\n'
        '            .data/provider_quota_governor_state.json .data/provider_request_budget_state.json .data/provider_quota_state.json || true\n'
    )
    text = replace_once(text, old_git_add, new_git_add)

    # Add prune step before upload.
    upload_marker = '      - name: Upload run artifact\n'
    prune_step = (
        '      - name: Prune artifact payload\n'
        '        if: always()\n'
        '        shell: bash\n'
        '        run: |\n'
        '          set -uo pipefail\n'
        '          python -u scripts/prune_run_artifacts.py || true\n\n'
    )
    text = insert_before(text, upload_marker, prune_step)

    # Narrow upload payload: artifacts/run-bot is already the curated bundle.
    old_upload_paths = '''          path: |
            artifacts/run-bot/**
            .data/exports/**
            .data/cache/**
            .data/day_inventory/**
            .data/line_history/**
            .data/provider_quota_governor_state.json
            .data/provider_request_budget_state.json
            .data/published-candidate-index.json
            .data/fallback-sent-index.json
            .data/candidate-lifecycle-state.json
'''
    new_upload_paths = '''          path: |
            artifacts/run-bot/**
            .data/exports/latest-*.json
            .data/exports/latest-*.txt
            .data/exports/latest-*.csv
            .data/day_inventory/current.json
            .data/day_inventory/latest.json
            .data/day_inventory/today.json
            .data/line_history/latest.json
            .data/provider_quota_governor_state.json
            .data/provider_request_budget_state.json
            .data/published-candidate-index.json
            .data/fallback-sent-index.json
            .data/candidate-lifecycle-state.json
'''
    text = replace_once(text, old_upload_paths, new_upload_paths)

    if text != original:
        WORKFLOW.write_text(text, encoding='utf-8')
        print('patched .github/workflows/run-bot.yml')
    else:
        print('workflow already patched or expected anchors were not found')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
