from __future__ import annotations

from pathlib import Path

PATH = Path('app/providers/weather_common.py')

OLD = """        elif country:\n            query = f'{match.home_team}, {country}'\n        else:\n            query = match.home_team\n        if not query.strip():\n            return None\n"""

NEW = """        elif country and _env_bool('WEATHER_ALLOW_TEAM_NAME_FALLBACK', False):\n            query = f'{match.home_team}, {country}'\n        elif _env_bool('WEATHER_ALLOW_TEAM_NAME_FALLBACK', False):\n            query = match.home_team\n        else:\n            return None\n        if not query.strip():\n            return None\n"""


def main() -> int:
    if not PATH.exists():
        print(f'skip: {PATH} not found')
        return 0
    src = PATH.read_text(encoding='utf-8')
    if "WEATHER_ALLOW_TEAM_NAME_FALLBACK" in src:
        print(f'already patched: {PATH}')
        return 0
    if OLD not in src:
        print('warn: weather fallback block not found')
        return 0
    PATH.write_text(src.replace(OLD, NEW, 1), encoding='utf-8')
    print(f'patched: {PATH}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
