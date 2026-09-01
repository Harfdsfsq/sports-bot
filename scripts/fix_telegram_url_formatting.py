from __future__ import annotations

"""Repair accidental brace-wrapped Telegram API URLs in repository scripts."""

from pathlib import Path

PATTERNS = (
    ('f"{{https://api.telegram.org/bot{token}}}/sendMessage"', 'f"https://api.telegram.org/bot{token}/sendMessage"'),
    ("f'{{https://api.telegram.org/bot{token}}}/sendMessage'", "f'https://api.telegram.org/bot{token}/sendMessage'"),
)


def main() -> int:
    changed = []
    for path in Path('scripts').glob('*.py'):
        text = path.read_text(encoding='utf-8')
        new = text
        for old, repl in PATTERNS:
            new = new.replace(old, repl)
        if new != text:
            path.write_text(new, encoding='utf-8')
            changed.append(str(path))
    print({'changed': changed, 'count': len(changed)})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
