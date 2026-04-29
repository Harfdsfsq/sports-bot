from __future__ import annotations

"""Patch settlement matching to reduce false no_match outcomes.

The historical settlement probe showed many rows with a reasonable best fuzzy
candidate below the legacy score threshold. This patch keeps the normal threshold
first, then allows a soft accept only when:
- the best score is still reasonably high;
- both team similarities are acceptable in direct or reversed orientation;
- kickoff times are close enough;
- the row actually has a final score.

This is a runtime patch applied in GitHub Actions before app.cli run-once.
"""

from pathlib import Path

ROOT = Path('.').resolve()
TARGET = ROOT / 'app' / 'services' / 'settlement.py'
PATCH_VERSION = 'v1-soft-settlement-match'


def main() -> int:
    text = TARGET.read_text(encoding='utf-8')
    changed = False

    if 'SETTLEMENT_SOFT_MATCH_PATCH_VERSION' in text:
        print(f'{PATCH_VERSION}: already applied')
        return 0

    if '\nimport os\n' not in text:
        text = text.replace('import json\n', 'import json\nimport os\n', 1)
        changed = True

    old_threshold = "threshold = float(getattr(self.settings, 'settlement_match_score_threshold', 70.0) or 70.0)"
    new_threshold = "threshold = float(os.getenv('SETTLEMENT_MATCH_SCORE_THRESHOLD', str(getattr(self.settings, 'settlement_match_score_threshold', 70.0) or 70.0)) or 70.0)"
    if old_threshold in text:
        text = text.replace(old_threshold, new_threshold, 1)
        changed = True

    old_block = """        if best_key[1] < threshold:\n            debug['match_failure'] = 'below_threshold'\n            debug['match_threshold'] = threshold\n            return None, debug\n        return best_row, debug\n"""
    new_block = """        if best_key[1] < threshold:\n            soft_threshold = float(os.getenv('SETTLEMENT_SOFT_MATCH_SCORE_THRESHOLD', '56.0') or 56.0)\n            max_soft_time_diff_hours = float(os.getenv('SETTLEMENT_SOFT_MATCH_MAX_TIME_DIFF_HOURS', '36.0') or 36.0)\n            min_team_similarity = float(os.getenv('SETTLEMENT_SOFT_MATCH_MIN_TEAM_SIMILARITY', '0.58') or 0.58)\n            event_start = self._extract_start(best_row) if best_row is not None else None\n            time_diff_hours = 999.0\n            if event_start is not None:\n                try:\n                    time_diff_hours = abs((match_start.astimezone(UTC) - event_start.astimezone(UTC)).total_seconds()) / 3600.0\n                except Exception:\n                    time_diff_hours = 999.0\n            event_home = self._extract_team_name(best_row, 'home') if best_row is not None else ''\n            event_away = self._extract_team_name(best_row, 'away') if best_row is not None else ''\n            direct_home = team_similarity(str(bet.get('home_team') or ''), event_home)\n            direct_away = team_similarity(str(bet.get('away_team') or ''), event_away)\n            reverse_home = team_similarity(str(bet.get('home_team') or ''), event_away)\n            reverse_away = team_similarity(str(bet.get('away_team') or ''), event_home)\n            direct_floor = min(direct_home, direct_away)\n            reverse_floor = min(reverse_home, reverse_away)\n            best_team_floor = max(direct_floor, reverse_floor)\n            soft_accept = (\n                best_key[1] >= soft_threshold\n                and bool(best_key[0])\n                and time_diff_hours <= max_soft_time_diff_hours\n                and best_team_floor >= min_team_similarity\n            )\n            debug['match_threshold'] = threshold\n            debug['soft_match_threshold'] = soft_threshold\n            debug['soft_match_time_diff_hours'] = round(float(time_diff_hours), 3)\n            debug['soft_match_team_floor'] = round(float(best_team_floor), 3)\n            debug['soft_match_patch_version'] = os.getenv('SETTLEMENT_SOFT_MATCH_PATCH_VERSION', 'v1-soft-settlement-match')\n            if soft_accept:\n                debug['match_failure'] = None\n                debug['matched_via'] = 'soft_threshold'\n                debug['soft_match_accepted'] = True\n                return best_row, debug\n            debug['match_failure'] = 'below_threshold'\n            debug['soft_match_accepted'] = False\n            return None, debug\n        return best_row, debug\n"""
    if old_block in text:
        text = text.replace(old_block, new_block, 1)
        changed = True
    else:
        print(f'{PATCH_VERSION}: target block not found; no settlement matching patch applied')

    if changed:
        TARGET.write_text(text, encoding='utf-8')
        print(f'{PATCH_VERSION}: patched {TARGET}')
    else:
        print(f'{PATCH_VERSION}: no changes')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
