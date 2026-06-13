from __future__ import annotations

"""Apply HARIZON v10 workflow/lifecycle/runtime fixes.

Run from repository root after extracting the archive:
    python scripts/apply_harizon_v10_fresh_lifecycle_runtime_fix.py

The patch is intentionally textual and idempotent because users may apply it on
a repository that already contains v7/v8/v9 pieces.
"""

from pathlib import Path

WF = Path(".github/workflows/run-bot.yml")


def _replace(text: str, old: str, new: str, label: str, once: bool = True) -> str:
    if old not in text:
        print(f"[WARN] pattern not found: {label}")
        return text
    return text.replace(old, new, 1 if once else -1)


def _ensure_after(text: str, anchor: str, snippet: str, label: str) -> str:
    if snippet.strip() in text:
        print(f"[OK] already present: {label}")
        return text
    if anchor not in text:
        print(f"[WARN] anchor not found: {label}")
        return text
    return text.replace(anchor, anchor + snippet, 1)


def main() -> int:
    if not WF.exists():
        raise SystemExit(".github/workflows/run-bot.yml not found; run from repo root")
    text = WF.read_text(encoding="utf-8")
    original = text

    # Runtime/post-run stability.
    text = text.replace("    timeout-minutes: 35\n", "    timeout-minutes: 45\n")
    text = text.replace("      CONTROLLED_FALLBACK_STRICT_MATCH_MARKET_DEDUPE: \"true\"\n", "      CONTROLLED_FALLBACK_STRICT_MATCH_MARKET_DEDUPE: \"false\"\n")

    # Stop SportLogic diagnostics from spending requests when provider is disabled.
    replacements = {
        '      SPORTLOGIC_ACTIVE_ODDS_SMOKE_PAGES: "2"\n': '      SPORTLOGIC_ACTIVE_ODDS_SMOKE_PAGES: "0"\n',
        '      SPORTLOGIC_ACTIVE_ODDS_SMOKE_GAME_LIMIT: "40"\n': '      SPORTLOGIC_ACTIVE_ODDS_SMOKE_GAME_LIMIT: "0"\n',
    }
    for old, new in replacements.items():
        text = text.replace(old, new)
    text = _ensure_after(
        text,
        '      SPORTLOGIC_ENABLED: "false"\n',
        '      SPORTLOGIC_SMOKE_ENABLED: "false"\n      PROVIDER_SMOKE_ENABLE_SPORTLOGIC: "false"\n',
        "sportlogic smoke disabled env",
    )

    # Restore awaiting lifecycle candidates before fallback sees the pool.
    text = _ensure_after(
        text,
        '          python -u scripts/apply_publication_family_policy.py || true\n',
        '          python -u scripts/restore_awaiting_movement_candidates.py || true\n',
        "restore awaiting movement candidates before fallback",
    )

    # Persist awaiting lifecycle candidates and build fresh diagnostics after fallback.
    text = _ensure_after(
        text,
        '          python scripts/publish_controlled_fallback_guarded.py || python scripts/publish_controlled_fallback.py || true\n',
        '          python -u scripts/persist_awaiting_movement_candidates.py || true\n          python -u scripts/build_fresh_b_cover_diagnostics.py || true\n',
        "persist awaiting movement candidates after fallback",
    )
    text = _ensure_after(
        text,
        '          cp .data/exports/latest-controlled-fallback-prepublish-guard.json artifacts/run-bot/latest-controlled-fallback-prepublish-guard.json 2>/dev/null || true\n',
        '          cp .data/exports/latest-awaiting-movement-candidates.json artifacts/run-bot/latest-awaiting-movement-candidates.json 2>/dev/null || true\n          cp .data/exports/latest-awaiting-movement-restore.json artifacts/run-bot/latest-awaiting-movement-restore.json 2>/dev/null || true\n          cp .data/exports/latest-fresh-b-cover-diagnostics.json artifacts/run-bot/latest-fresh-b-cover-diagnostics.json 2>/dev/null || true\n',
        "copy lifecycle/fresh diagnostics",
    )

    # Run heavy B-cover promotion once before fallback; later steps keep existing report.
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
          python -u scripts/build_fresh_b_cover_diagnostics.py || true
          # Do not re-run B-cover promotion here; it is intentionally run once before fallback.
''', 1)

    # Commit compact runtime state, not full cache/export trees.
    text = _replace(
        text,
'''          git add -f .data/state.json .data/published-candidate-index.json .data/fallback-sent-index.json .data/candidate-lifecycle-state.json .data/exports .data/cache .data/day_inventory .data/line_history .data/provider_quota_governor_state.json .data/provider_request_budget_state.json .data/provider_quota_state.json || true
''',
'''          git add -f .data/state.json .data/published-candidate-index.json .data/fallback-sent-index.json .data/candidate-lifecycle-state.json .data/day_inventory/*.json .data/line_history/*.json .data/exports/latest-*.json .data/exports/latest-*.txt .data/exports/latest-*.csv .data/exports/latest-run-bot.log .data/provider_quota_governor_state.json .data/provider_request_budget_state.json .data/provider_quota_state.json || true
          git reset -- .data/cache .data/exports/20* .data/exports/*line-snapshots*.json .data/exports/*.jsonl 2>/dev/null || true
''',
        "compact git add",
    )

    # Compact artifact upload. This block may still be unpatched if v9 was not applied.
    old_upload = '''      - name: Upload run artifact
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
'''
    new_upload = '''      - name: Prune artifact payload
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
            .data/exports/latest-*.csv
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
'''
    if "Prune artifact payload" not in text:
        text = _replace(text, old_upload, new_upload, "compact upload artifact")

    if text == original:
        print("No workflow changes made; it may already be patched.")
    else:
        WF.write_text(text, encoding="utf-8")
        print("Patched .github/workflows/run-bot.yml with v10 fresh lifecycle/runtime fixes.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
