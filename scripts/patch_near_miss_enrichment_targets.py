from __future__ import annotations

from pathlib import Path

RUNNER_PATH = Path('app/services/runner.py')

OLD_SORT_BLOCK = """        def score(match: Match) -> tuple[int, int, float, str]:
            has_offers = 1 if offers_by_match.get(match.match_key) else 0
            tier_rank = {'top': 0, 'mid': 1, 'low': 2}.get(str(getattr(match, 'tier', 'mid')), 1)
            try:
                hours_to_kickoff = max(0.0, (match.commence_time - datetime.now(UTC)).total_seconds() / 3600.0)
            except Exception:
                hours_to_kickoff = 999.0
            return (-has_offers, tier_rank, hours_to_kickoff, match.league_name.lower())

        ranked_fallback = sorted(fallback_matches, key=score)
"""

NEW_SORT_BLOCK = """        import json
        near_miss_priority: dict[str, float] = {}
        near_miss_paths = [
            Path('.data/provider_cache/day-shortlist/latest-near-miss-enrichment-queue.json'),
            Path('.data/exports/latest-near-miss-enrichment-queue.json'),
        ]
        for queue_path in near_miss_paths:
            try:
                if not queue_path.exists():
                    continue
                payload = json.loads(queue_path.read_text(encoding='utf-8'))
                items = payload.get('items') if isinstance(payload, dict) else []
                if not isinstance(items, list):
                    continue
                for idx, item in enumerate(items):
                    if not isinstance(item, dict):
                        continue
                    key = str(item.get('match_key') or '').strip()
                    if not key:
                        continue
                    priority = float(item.get('priority') or 0.0)
                    # Preserve order as a tiny tie-breaker: earlier queue items stay first.
                    near_miss_priority[key] = max(near_miss_priority.get(key, 0.0), priority + max(0.0, 0.001 * (len(items) - idx)))
            except Exception as exc:
                self.provider_runtime_errors['near_miss_enrichment_queue'].append(self._format_exception(exc))

        def score(match: Match) -> tuple[float, int, int, float, str]:
            queue_priority = near_miss_priority.get(match.match_key, 0.0)
            has_offers = 1 if offers_by_match.get(match.match_key) else 0
            tier_rank = {'top': 0, 'mid': 1, 'low': 2}.get(str(getattr(match, 'tier', 'mid')), 1)
            try:
                hours_to_kickoff = max(0.0, (match.commence_time - datetime.now(UTC)).total_seconds() / 3600.0)
            except Exception:
                hours_to_kickoff = 999.0
            return (-queue_priority, -has_offers, tier_rank, hours_to_kickoff, match.league_name.lower())

        ranked_fallback = sorted(fallback_matches, key=score)
"""


def patch_runner() -> bool:
    if not RUNNER_PATH.exists():
        print(f'skip: {RUNNER_PATH} not found')
        return False
    src = RUNNER_PATH.read_text(encoding='utf-8')
    original = src
    if 'near_miss_enrichment_queue' not in src:
        if OLD_SORT_BLOCK in src:
            src = src.replace(OLD_SORT_BLOCK, NEW_SORT_BLOCK, 1)
        else:
            print('warn: enrichment score block not found')
    if 'expanded = list(selected_by_key.values())' in src:
        src = src.replace('expanded = list(selected_by_key.values())', 'expanded = sorted(selected_by_key.values(), key=score)', 1)
    if 'near_miss_queue_items=' not in src:
        marker = "            context_backfill_limit=hard_cap,\n"
        if marker in src:
            src = src.replace(marker, marker + "            near_miss_queue_items=len(near_miss_priority),\n", 1)
    if src != original:
        RUNNER_PATH.write_text(src, encoding='utf-8')
        print(f'patched: {RUNNER_PATH}')
        return True
    print(f'already patched or no changes: {RUNNER_PATH}')
    return False


def patch_controlled_fallback() -> None:
    try:
        import apply_confirmation_source_fallback_patch

        apply_confirmation_source_fallback_patch.main()
    except Exception as exc:
        print(f'warn: confirmation-source fallback patch failed: {type(exc).__name__}: {exc}')


def main() -> int:
    patch_runner()
    patch_controlled_fallback()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
