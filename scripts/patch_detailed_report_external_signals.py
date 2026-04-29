from __future__ import annotations

from pathlib import Path

TARGET = Path('scripts/build_detailed_run_report.py')
PATCH_VERSION = 'v1-detailed-report-external-signals'


def main() -> int:
    text = TARGET.read_text(encoding='utf-8')
    changed = False
    if '"external_signals",' not in text:
        marker = '        "gnews",\n    ]'
        repl = '        "gnews",\n        "external_signals",\n    ]'
        if marker in text:
            text = text.replace(marker, repl, 1)
            changed = True
    if changed:
        TARGET.write_text(text, encoding='utf-8')
    print({'patch': PATCH_VERSION, 'changed': changed})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
