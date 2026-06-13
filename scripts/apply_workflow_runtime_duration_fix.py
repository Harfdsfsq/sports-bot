from __future__ import annotations

"""Patch .github/workflows/run-bot.yml to reduce runtime and upload stalls.

Run from the repository root:
    python scripts/apply_workflow_runtime_duration_fix.py
"""

from pathlib import Path

WF = Path('.github/workflows/run-bot.yml')


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if old not in text:
        print(f'[WARN] pattern not found: {label}')
        return text
    return text.replace(old, new, 1)


def main() -> int:
    if not WF.exists():
        raise SystemExit('run-bot.yml not found; run this from repo root')
    text = WF.read_text(encoding='utf-8')
    original = text

    text = replace_once(text, '    timeout-minutes: 35\n', '    timeout-minutes: 45\n', 'job timeout')
    text = text.replace('      CONTROLLED_FALLBACK_STRICT_MATCH_MARKET_DEDUPE: "true"\n', '      CONTROLLED_FALLBACK_STRICT_MATCH_MARKET_DEDUPE: "false"\n')

    # Do promotion once before fallback. Later calls are diagnostic-only but still
    # rescan large JSON trees; they add minutes and can rewrite rescue candidates.
    text = text.replace(
'''          python -u scripts/build_b_cover_candidate_gap_report.py || true
          cp .data/exports/latest-b-cover-candidate-gap-report.json artifacts/run-bot/latest-b-cover-candidate-gap-report.json 2>/dev/null || true
          cp .data/exports/latest-b-cover-candidate-gap-report.csv artifacts/run-bot/latest-b-cover-candidate-gap-report.csv 2>/dev/null || true
''',
'''          # B-cover promotion already ran in Run bot before controlled fallback.
          # Avoid a second heavy scan/re-promotion here; keep the existing report.
          cp .data/exports/latest-b-cover-candidate-gap-report.json artifacts/run-bot/latest-b-cover-candidate-gap-report.json 2>/dev/null || true
          cp .data/exports/latest-b-cover-candidate-gap-report.csv artifacts/run-bot/latest-b-cover-candidate-gap-report.csv 2>/dev/null || true
          cp .data/exports/latest-b-cover-value-promotion.json artifacts/run-bot/latest-b-cover-value-promotion.json 2>/dev/null || true
''', 1)

    text = text.replace(
'''          python -u scripts/day_inventory_cumulative_coverage.py || true
          python -u scripts/backfill_inventory_bookmaker_coverage.py || true
          python -u scripts/guard_day_inventory_no_shrink.py repair || true
          python -u scripts/build_b_cover_candidate_gap_report.py || true
''',
'''          python -u scripts/day_inventory_cumulative_coverage.py || true
          python -u scripts/backfill_inventory_bookmaker_coverage.py || true
          python -u scripts/guard_day_inventory_no_shrink.py repair || true
          # Do not re-run B-cover promotion here; it is intentionally run once before fallback.
''', 1)

    text = replace_once(text,
'''          git add -f .data/state.json .data/published-candidate-index.json .data/fallback-sent-index.json .data/candidate-lifecycle-state.json .data/exports .data/cache .data/day_inventory .data/line_history .data/provider_quota_governor_state.json .data/provider_request_budget_state.json .data/provider_quota_state.json || true
''',
'''          git add -f .data/state.json .data/published-candidate-index.json .data/fallback-sent-index.json .data/candidate-lifecycle-state.json .data/day_inventory/*.json .data/line_history/*.json .data/exports/latest-*.json .data/exports/latest-*.txt .data/exports/latest-run-bot.log .data/provider_quota_governor_state.json .data/provider_request_budget_state.json .data/provider_quota_state.json || true
          git reset -- .data/cache .data/exports/20* .data/exports/*line-snapshots*.json 2>/dev/null || true
''', 'compact git add')

    text = replace_once(text,
'''      - name: Upload run artifact
        if: always()
        uses: actions/upload-artifact@v5
        with:
          name: run-bot-${{ github.run_id }}
          path: |
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
          if-no-files-found: ignore
          include-hidden-files: true
          retention-days: 7
''',
'''      - name: Prune artifact payload
        if: always()
        shell: bash
        run: |
          set -uo pipefail
          python -u scripts/prune_run_artifacts.py || true
          cp .data/exports/latest-artifact-prune-status.json artifacts/run-bot/latest-artifact-prune-status.json 2>/dev/null || true

      - name: Upload run artifact
        if: always()
        uses: actions/upload-artifact@v5
        with:
          name: run-bot-${{ github.run_id }}
          path: |
            artifacts/run-bot/**
            .data/exports/latest-*.json
            .data/exports/latest-*.txt
            .data/exports/latest-run-bot.log
            .data/day_inventory/current.json
            .data/day_inventory/latest.json
            .data/day_inventory/today.json
            .data/day_inventory/${{ env.DAY_INVENTORY_CACHE_DATE }}.json
            .data/line_history/latest.json
            .data/line_history/${{ env.DAY_INVENTORY_CACHE_DATE }}.json
            .data/provider_quota_governor_state.json
            .data/provider_request_budget_state.json
            .data/published-candidate-index.json
            .data/fallback-sent-index.json
            .data/candidate-lifecycle-state.json
          if-no-files-found: ignore
          include-hidden-files: true
          retention-days: 7
''', 'compact upload artifact')

    if text == original:
        print('No changes made; workflow may already be patched.')
        return 0
    WF.write_text(text, encoding='utf-8')
    print('Patched .github/workflows/run-bot.yml for compact artifacts and shorter post-run path.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
