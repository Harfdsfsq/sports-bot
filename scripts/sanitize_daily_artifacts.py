"""Remove credential strings from diagnostic artifacts before upload/cache."""
from __future__ import annotations

import os
from pathlib import Path

from app.cli import _redact_log_text


def sanitize(text, secrets):
    for secret in secrets:
        text = text.replace(secret, '***')
    return _redact_log_text(text)


def main():
    secrets = [value for key, value in os.environ.items() if any(token in key for token in ('TOKEN', 'API_KEY', 'ODDS_API_IO_KEY', 'WEATHERAPI_KEY')) and len(value) >= 8]
    paths = list(Path('.data').rglob('*.json')) + list(Path('.data').rglob('*.log')) + list(Path('.logs').glob('*.json'))
    for path in paths:
        text = path.read_text(errors='replace')
        cleaned = sanitize(text, secrets)
        if cleaned != text:
            path.write_text(cleaned)


if __name__ == '__main__':
    main()
