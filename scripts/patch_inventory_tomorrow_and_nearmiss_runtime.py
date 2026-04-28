from __future__ import annotations

from pathlib import Path

RUNNER_PATH = Path('app/services/runner.py')

OLD_CANDIDATES = """        candidates = [
            Path('.data/day_inventory') / f'{local_date}.json',
            Path('.data/day_inventory/today.json'),
            Path('.data/day_inventory/current.json'),
            Path('.data/day_inventory/latest.json'),
        ]
        payload: dict[str, Any] = {}
        for path in candidates:
            try:
                if path.exists():
                    raw = json.loads(path.read_text(encoding='utf-8'))
                    if isinstance(raw, dict) and isinstance(raw.get('matches'), list):
                        payload = raw
                        break
            except Exception as exc:
                self.provider_runtime_errors['day_inventory'].append(self._format_exception(exc))
        rows = payload.get('matches') if isinstance(payload.get('matches'), list) else []
"""

NEW_CANDIDATES = """        from datetime import timedelta
        today_path = Path('.data/day_inventory') / f'{local_date}.json'
        candidates = [
            today_path,
            Path('.data/day_inventory/today.json'),
            Path('.data/day_inventory/current.json'),
            Path('.data/day_inventory/latest.json'),
        ]
        extra_dated_paths: list[Path] = []
        try:
            local_now = now_utc.astimezone(self.settings.tzinfo)
            tomorrow = (local_now.date() + timedelta(days=1)).isoformat()
            publish_window_hours = float(getattr(self.settings, 'publish_window_hours', 6) or 6)
            crosses_midnight = (local_now + timedelta(hours=publish_window_hours)).date() > local_now.date()
            evening_warmup = local_now.hour >= int(float(__import__('os').getenv('NEXT_DAY_INVENTORY_WARMUP_START_HOUR_LOCAL', '18') or 18))
            if crosses_midnight or evening_warmup:
                extra_dated_paths.append(Path('.data/day_inventory') / f'{tomorrow}.json')
        except Exception as exc:
            self.provider_runtime_errors['day_inventory'].append(self._format_exception(exc))
        payloads: list[dict[str, Any]] = []
        seen_payload_dates: set[str] = set()
        for path in [*candidates, *extra_dated_paths]:
            try:
                if not path.exists():
                    continue
                raw = json.loads(path.read_text(encoding='utf-8'))
                if not isinstance(raw, dict) or not isinstance(raw.get('matches'), list):
                    continue
                date_key = str(raw.get('date_local') or path.stem)
                if date_key in seen_payload_dates:
                    continue
                seen_payload_dates.add(date_key)
                payloads.append(raw)
            except Exception as exc:
                self.provider_runtime_errors['day_inventory'].append(self._format_exception(exc))
        rows: list[dict[str, Any]] = []
        for payload in payloads:
            rows.extend([row for row in (payload.get('matches') or []) if isinstance(row, dict)])
"""

OLD_MARK_STATUS = """            matches_loaded=len(matches),
            target_date=local_date,
            source='runtime_inventory',
"""

NEW_MARK_STATUS = """            matches_loaded=len(matches),
            target_date=local_date,
            loaded_inventory_dates=sorted(seen_payload_dates) if 'seen_payload_dates' in locals() else [local_date],
            source='runtime_inventory',
"""

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


def replace_if_present(src: str, old: str, new: str, label: str) -> str:
    if old in src and new not in src:
        return src.replace(old, new, 1)
    if old not in src and new not in src:
        print(f'warn: marker not found: {label}')
    return src


def main() -> int:
    if not RUNNER_PATH.exists():
        print(f'skip: {RUNNER_PATH} not found')
        return 0
    src = RUNNER_PATH.read_text(encoding='utf-8')
    original = src
    src = replace_if_present(src, OLD_CANDIDATES, NEW_CANDIDATES, 'day inventory candidate paths')
    src = replace_if_present(src, OLD_MARK_STATUS, NEW_MARK_STATUS, 'day inventory status loaded dates')
    src = replace_if_present(src, OLD_SORT_BLOCK, NEW_SORT_BLOCK, 'near miss priority score block')
    if 'expanded = list(selected_by_key.values())' in src and 'expanded = sorted(selected_by_key.values(), key=score)' not in src:
        src = src.replace('expanded = list(selected_by_key.values())', 'expanded = sorted(selected_by_key.values(), key=score)', 1)
    if 'near_miss_queue_items=' not in src:
        marker = "            context_backfill_limit=hard_cap,\n"
        if marker in src:
            src = src.replace(marker, marker + "            near_miss_queue_items=len(near_miss_priority) if 'near_miss_priority' in locals() else 0,\n", 1)
    if src != original:
        RUNNER_PATH.write_text(src, encoding='utf-8')
        print(f'patched: {RUNNER_PATH}')
    else:
        print(f'already patched or no changes: {RUNNER_PATH}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
