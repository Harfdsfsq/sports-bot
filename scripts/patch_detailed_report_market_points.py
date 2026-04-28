from __future__ import annotations

from pathlib import Path

REPORT_PATH = Path('scripts/build_detailed_run_report.py')

HELPER = r'''

def _format_market_point(value: Any) -> str:
    if value in (None, ''):
        return ''
    try:
        number = float(value)
    except Exception:
        text = str(value).strip()
        return text
    if abs(number) < 0.000001:
        return '0'
    if number.is_integer():
        return str(int(number))
    text = f'{number:.2f}'.rstrip('0').rstrip('.')
    return text


def detailed_selection_text(candidate: dict[str, Any]) -> str:
    """Telegram text for candidate selection with the market line included.

    Previous detailed reports showed totals as just "Меньше" / "Больше" even
    when candidate.point was available. Operators need the actual line, e.g.
    "Меньше 2.5 @1.98". This helper keeps BTTS/H2H unchanged and adds point
    only for markets where the line is meaningful.
    """
    raw_selection = candidate.get('selection') or candidate.get('market') or ''
    text = translate_selection_text(raw_selection, candidate.get('home_team'), candidate.get('away_team')).strip()
    point = _format_market_point(candidate.get('point'))
    if not point:
        return text

    family = str(candidate.get('family') or candidate.get('market_family') or '').strip().lower()
    normalized = text.lower().replace(',', '.')
    if point.lower() in normalized:
        return text

    is_total = family in {'totals', 'total', 'match_total'} or normalized in {'меньше', 'больше', 'under', 'over'} or normalized.startswith(('меньше ', 'больше ', 'under ', 'over '))
    is_team_total = family in {'teamtotals', 'team_total', 'individual_totals'}
    is_spread = family in {'spreads', 'spread', 'handicap', 'dnb'}

    if is_total or is_team_total:
        return f'{text} {point}'.strip()
    if is_spread:
        signed = point
        try:
            number = float(candidate.get('point'))
            if number > 0:
                signed = '+' + _format_market_point(number)
            else:
                signed = _format_market_point(number)
        except Exception:
            pass
        if '(' in text and ')' in text:
            return text
        return f'{text} ({signed})'.strip()
    return text
'''

OLD_LINE = '    selection = translate_selection_text(candidate.get("selection") or candidate.get("market") or "", candidate.get("home_team"), candidate.get("away_team"))\n'
NEW_LINE = '    selection = detailed_selection_text(candidate)\n'


def main() -> int:
    if not REPORT_PATH.exists():
        print(f'skip: {REPORT_PATH} not found')
        return 0
    src = REPORT_PATH.read_text(encoding='utf-8')
    original = src

    if 'def detailed_selection_text(candidate:' not in src:
        marker = '\ndef candidate_identity(candidate: dict[str, Any]) -> dict[str, str]:\n'
        if marker in src:
            src = src.replace(marker, HELPER + marker, 1)
        else:
            print('warn: candidate_identity marker not found')

    if OLD_LINE in src and NEW_LINE not in src:
        src = src.replace(OLD_LINE, NEW_LINE, 1)
    elif NEW_LINE in src:
        pass
    else:
        print('warn: selection line marker not found')

    if src != original:
        REPORT_PATH.write_text(src, encoding='utf-8')
        print(f'patched: {REPORT_PATH}')
    else:
        print(f'already patched or no changes: {REPORT_PATH}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
