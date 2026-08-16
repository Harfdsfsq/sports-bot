from __future__ import annotations

from pathlib import Path

RUNNER_PATH = Path('app/services/runner.py')

RUNNER_METHODS = r'''
    def _day_inventory_target_date(self, now_utc: datetime) -> str:
        explicit = str(__import__('os').getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
        if explicit:
            return explicit
        try:
            return now_utc.astimezone(self.settings.tzinfo).date().isoformat()
        except Exception:
            return now_utc.date().isoformat()

    def _match_from_day_inventory_row(self, row: dict[str, Any]) -> Match | None:
        try:
            from app.utils import canonicalize_league_name, canonicalize_team_name, parse_datetime
            kickoff_raw = row.get('kickoff_utc') or row.get('commence_time') or row.get('kickoff_local')
            home = str(row.get('home_team') or '').strip()
            away = str(row.get('away_team') or '').strip()
            league = str(row.get('league_name') or '').strip()
            if not kickoff_raw or not home or not away or not league:
                return None
            source_ids = row.get('source_ids') if isinstance(row.get('source_ids'), dict) else {}
            source = str(next(iter(source_ids.keys()), '') or 'day_inventory')
            source_event_id = str(source_ids.get(source) or row.get('source_event_id') or row.get('canonical_match_id') or '')
            return Match(
                source=source,
                source_event_id=source_event_id,
                sport_key=str(row.get('sport_key') or 'soccer'),
                league_name=league,
                home_team=home,
                away_team=away,
                commence_time=parse_datetime(str(kickoff_raw)),
                home_team_norm=str(row.get('home_team_norm') or canonicalize_team_name(home)),
                away_team_norm=str(row.get('away_team_norm') or canonicalize_team_name(away)),
                league_key=str(row.get('league_key') or canonicalize_league_name(league)),
                tier=str(row.get('tier') or 'mid'),
                metadata={**(row.get('metadata') if isinstance(row.get('metadata'), dict) else {}), 'from_day_inventory': True},
            )
        except Exception as exc:
            self.provider_runtime_errors['day_inventory'].append(self._format_exception(exc))
            return None

    def _load_day_inventory_matches(self, now_utc: datetime) -> list[Match]:
        import json
        import os
        if str(os.getenv('DAY_INVENTORY_RUNTIME_ENABLED', 'true')).strip().lower() in {'0', 'false', 'no', 'off'}:
            return []
        local_date = self._day_inventory_target_date(now_utc)
        candidates = [
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
        matches: list[Match] = []
        max_items = max(1, int(getattr(self.settings, 'day_inventory_runtime_match_limit', 240) or 240))
        for row in rows[:max_items]:
            if not isinstance(row, dict):
                continue
            match = self._match_from_day_inventory_row(row)
            if match is None:
                continue
            matches.append(match)
        self._mark_provider_status(
            'day_inventory',
            enabled=True,
            loaded=True,
            matches_loaded=len(matches),
            target_date=local_date,
            source='runtime_inventory',
        )
        return matches

    def _merge_day_inventory_matches(self, matches: list[Match], now_utc: datetime) -> list[Match]:
        inventory_matches = self._load_day_inventory_matches(now_utc)
        if not inventory_matches:
            return matches
        merged: dict[str, Match] = {match.match_key: match for match in matches}
        added = 0
        for match in inventory_matches:
            if match.match_key in merged:
                continue
            merged[match.match_key] = match
            added += 1
        ordered = sorted(merged.values(), key=lambda item: (item.commence_time.isoformat(), item.league_name, item.home_team, item.away_team))
        self._mark_provider_status(
            'day_inventory',
            merged_total=len(ordered),
            merged_added=added,
            bootstrap_total_before_inventory=len(matches),
        )
        return ordered

    def _expand_context_targets_from_inventory(
        self,
        selected_matches: list[Match],
        fallback_matches: list[Match],
        offers_by_match: dict[str, list[Offer]],
        provider_key: str,
    ) -> list[Match]:
        import os
        if str(os.getenv('DAY_INVENTORY_CONTEXT_BACKFILL_ENABLED', 'true')).strip().lower() in {'0', 'false', 'no', 'off'}:
            return selected_matches
        provider_key = str(provider_key or 'provider').strip().lower()
        limit_env = f'{provider_key.upper()}_CONTEXT_MATCH_LIMIT'
        try:
            limit = int(float(os.getenv(limit_env) or getattr(self.settings, f'{provider_key}_context_match_limit', 0) or 0))
        except Exception:
            limit = 0
        if limit <= 0:
            limit = len(selected_matches)
        default_backfill_limit = int(float(os.getenv('DAY_INVENTORY_CONTEXT_BACKFILL_LIMIT', '64') or 64))
        # Never shrink a provider target list that upstream selection already made larger.
        # Previous version used max(limit, default_backfill_limit) and then sliced selected_by_key,
        # which reduced sstats from 80 targets to 64 in the 2026-04-28 15:00 MSK run.
        hard_cap = max(len(selected_matches), limit, default_backfill_limit)
        selected_by_key: dict[str, Match] = {match.match_key: match for match in selected_matches}

        def score(match: Match) -> tuple[int, int, float, str]:
            has_offers = 1 if offers_by_match.get(match.match_key) else 0
            tier_rank = {'top': 0, 'mid': 1, 'low': 2}.get(str(getattr(match, 'tier', 'mid')), 1)
            try:
                hours_to_kickoff = max(0.0, (match.commence_time - datetime.now(UTC)).total_seconds() / 3600.0)
            except Exception:
                hours_to_kickoff = 999.0
            return (-has_offers, tier_rank, hours_to_kickoff, match.league_name.lower())

        ranked_fallback = sorted(fallback_matches, key=score)
        for match in ranked_fallback:
            if len(selected_by_key) >= hard_cap:
                break
            if match.match_key in selected_by_key:
                continue
            selected_by_key[match.match_key] = match
        expanded = list(selected_by_key.values())
        if len(expanded) > hard_cap:
            expanded = expanded[:hard_cap]
        self._mark_provider_status(
            provider_key,
            context_backfill_enabled=True,
            context_targets_before_backfill=len(selected_matches),
            context_targets_after_backfill=len(expanded),
            context_backfill_limit=hard_cap,
        )
        return expanded

'''

MERGE_CALL = '            deduped_matches = self._merge_day_inventory_matches(deduped_matches, now_utc)\n'

CONTEXT_REPLACEMENTS = {
    "'sstats': self._select_provider_context_matches(context_target_matches, 'sstats', fallback_matches=filtered_matches, offers_by_match=merged_offers),": "'sstats': self._expand_context_targets_from_inventory(self._select_provider_context_matches(context_target_matches, 'sstats', fallback_matches=filtered_matches, offers_by_match=merged_offers), filtered_matches, merged_offers, 'sstats'),",
    "'bzzoiro': self._select_provider_context_matches(context_target_matches, 'bzzoiro', fallback_matches=filtered_matches, offers_by_match=merged_offers),": "'bzzoiro': self._expand_context_targets_from_inventory(self._select_provider_context_matches(context_target_matches, 'bzzoiro', fallback_matches=filtered_matches, offers_by_match=merged_offers), filtered_matches, merged_offers, 'bzzoiro'),",
    "'futrixmetrics': self._select_provider_context_matches(context_target_matches, 'futrixmetrics', fallback_matches=filtered_matches, offers_by_match=merged_offers),": "'futrixmetrics': self._expand_context_targets_from_inventory(self._select_provider_context_matches(context_target_matches, 'futrixmetrics', fallback_matches=filtered_matches, offers_by_match=merged_offers), filtered_matches, merged_offers, 'futrixmetrics'),",
    "'football_data': self._select_provider_context_matches(context_target_matches, 'football_data', fallback_matches=filtered_matches, offers_by_match=merged_offers),": "'football_data': self._expand_context_targets_from_inventory(self._select_provider_context_matches(context_target_matches, 'football_data', fallback_matches=filtered_matches, offers_by_match=merged_offers), filtered_matches, merged_offers, 'football_data'),",
    "'thesportsdb': self._select_provider_context_matches(context_target_matches, 'thesportsdb', fallback_matches=filtered_matches, offers_by_match=merged_offers),": "'thesportsdb': self._expand_context_targets_from_inventory(self._select_provider_context_matches(context_target_matches, 'thesportsdb', fallback_matches=filtered_matches, offers_by_match=merged_offers), filtered_matches, merged_offers, 'thesportsdb'),",
}


def replace_once(src: str, old: str, new: str, label: str) -> str:
    if old not in src:
        print(f'warn: marker not found: {label}')
        return src
    return src.replace(old, new, 1)


def main() -> int:
    if not RUNNER_PATH.exists():
        print(f'skip: {RUNNER_PATH} not found')
        return 0
    src = RUNNER_PATH.read_text(encoding='utf-8')
    original = src

    # app/services/runner.py can already ship its own, richer implementation:
    #     def _merge_day_inventory_matches(self, bootstrap_matches, bootstrap_meta, now_utc, ...)
    # In that case this script must not inject its own two-argument call site, otherwise
    # run-once dies immediately with:
    #     TypeError: PredictionRunner._merge_day_inventory_matches() missing 1 required
    #                positional argument: 'now_utc'
    # The helper methods and the call sites that use them are therefore always applied
    # together, never separately.
    owns_runtime_methods = 'def _merge_day_inventory_matches' not in src

    if owns_runtime_methods:
        src = replace_once(
            src,
            '    async def run_once(self) -> dict[str, Any]:\n',
            RUNNER_METHODS + '\n    async def run_once(self) -> dict[str, Any]:\n',
            'insert day inventory runtime methods',
        )

    helpers_available = 'def _expand_context_targets_from_inventory' in src
    merge_available = 'def _merge_day_inventory_matches(self, matches: list[Match], now_utc: datetime)' in src

    if merge_available:
        if MERGE_CALL not in src:
            src = replace_once(
                src,
                '            deduped_matches = self._dedupe_matches(bootstrap_matches)\n',
                '            deduped_matches = self._dedupe_matches(bootstrap_matches)\n' + MERGE_CALL,
                'merge inventory after bootstrap dedupe',
            )
    elif MERGE_CALL in src:
        # Repair a checkout patched by an older version of this script against a runner
        # that provides its own incompatible merge signature.
        src = src.replace(MERGE_CALL, '', 1)
        print('repaired: removed incompatible day inventory merge call')
    else:
        print('skip: runner provides its own day inventory merge, call site left untouched')

    for old, new in CONTEXT_REPLACEMENTS.items():
        if helpers_available:
            if new in src:
                continue
            src = replace_once(src, old, new, f'expand {old.split(":", 1)[0]} targets')
        elif new in src:
            src = src.replace(new, old)
            print(f'repaired: reverted context expansion for {old.split(":", 1)[0]}')

    if src != original:
        RUNNER_PATH.write_text(src, encoding='utf-8')
        print(f'patched: {RUNNER_PATH}')
    else:
        print(f'already patched or no changes: {RUNNER_PATH}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
