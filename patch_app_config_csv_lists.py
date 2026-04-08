from __future__ import annotations

import pathlib
import re
import sys

REPLACEMENT = """@field_validator(
    'run_sports',
    'target_bookmakers',
    'consensus_bookmakers',
    'odds_api_io_bookmakers',
    'bookies_api_sports',
    'espn_soccer_leagues',
    'espn_soft_fail_statuses',
    'supported_total_lines',
    'supported_team_total_lines',
    'openfootball_competition_map',
    'sharp_bookmakers',
    'consensus_alias_groups',
    'risky_totals_league_terms',
    'risky_totals_team_terms',
    mode='before',
)
@classmethod
def split_csv(cls, value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in text.split(',') if item.strip()]
"""


def patch_config(path: pathlib.Path) -> None:
    text = path.read_text(encoding='utf-8')

    pattern = re.compile(
        r"@field_validator\(\s*.*?mode='before',\s*\)\s*@classmethod\s*def split_csv\(cls, value: Any\) -> list\[str\]:\s*if value is None:\s*return \[\]\s*if isinstance\(value, list\):\s*return \[str\(item\)\.strip\(\) for item in value if str\(item\)\.strip\(\)\]\s*text = str\(value\)\.strip\(\)\s*if not text:\s*return \[\]\s*return \[item\.strip\(\) for item in text\.split\(','\) if item\.strip\(\)\]",
        re.DOTALL,
    )

    new_text, count = pattern.subn(REPLACEMENT, text, count=1)
    if count != 1:
        raise SystemExit(
            'Не удалось автоматически найти блок CSV-валидатора в app/config.py. '
            'Открой файл вручную и добавь в @field_validator поля '\
            "'sharp_bookmakers', 'consensus_alias_groups', 'risky_totals_league_terms', 'risky_totals_team_terms'."
        )

    path.write_text(new_text, encoding='utf-8')


if __name__ == '__main__':
    repo_root = pathlib.Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else pathlib.Path.cwd()
    config_path = repo_root / 'app' / 'config.py'
    if not config_path.exists():
        raise SystemExit(f'Файл не найден: {config_path}')
    patch_config(config_path)
    print(f'Готово: {config_path}')
