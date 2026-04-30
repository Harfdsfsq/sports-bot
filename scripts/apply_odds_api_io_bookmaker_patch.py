from __future__ import annotations

"""Patch odds-api.io bookmaker selection at runtime.

The provider previously hard-limited requests to Bet365/Unibet even when the
workflow exported ODDS_API_IO_BOOKMAKERS=Bet365,Unibet,Betfair Exchange,Sbobet.
That made the run look correctly configured while the actual API request still
used only two bookmakers, keeping consensus and market-derived signals weak.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
TARGET = ROOT / 'app' / 'providers' / 'odds_api_io.py'
OUT = ROOT / '.data' / 'exports' / 'latest-odds-api-io-bookmaker-patch.json'
POLICY_VERSION = 'v1-allow-configured-four-bookmakers'
UTC = timezone.utc

OLD = '''    def _bookmakers_param(self) -> str:\n        """Restrict odds-api.io requests to Bet365 and Unibet only.\n\n        We intentionally ignore any extra bookmaker names that may appear in\n        env/config so the provider cannot silently widen coverage again.\n        """\n        preferred = list(getattr(self.settings, "odds_api_io_bookmakers", []) or [])\n        values: list[str] = []\n        allowed = {\n            "bet365": "Bet365",\n            "unibet": "Unibet",\n        }\n        for item in preferred:\n            raw = str(item or "").strip()\n            if not raw:\n                continue\n            value = allowed.get(normalize_bookmaker_name(raw))\n            if value and value not in values:\n                values.append(value)\n        return ",".join(values or ["Bet365", "Unibet"])\n'''

NEW = '''    def _bookmakers_param(self) -> str:\n        """Use every configured target bookmaker supported by odds-api.io.\n\n        Runtime policy exports four target books. Keeping only Bet365/Unibet\n        caused single-source/single-book style diagnostics and prevented useful\n        market-derived signals. This method now honors the configured list while\n        still allow-listing known bookmaker names.\n        """\n        preferred = list(getattr(self.settings, "odds_api_io_bookmakers", []) or [])\n        values: list[str] = []\n        allowed = {\n            "bet365": "Bet365",\n            "unibet": "Unibet",\n            "betfair": "Betfair Exchange",\n            "betfair exchange": "Betfair Exchange",\n            "sbobet": "Sbobet",\n            "sbo": "Sbobet",\n        }\n        for item in preferred:\n            raw = str(item or "").strip()\n            if not raw:\n                continue\n            normalized = normalize_bookmaker_name(raw)\n            value = allowed.get(normalized) or allowed.get(raw.lower())\n            if value and value not in values:\n                values.append(value)\n        return ",".join(values or ["Bet365", "Unibet", "Betfair Exchange", "Sbobet"])\n'''


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def main() -> int:
    if not TARGET.exists():
        report = {'status': 'missing_target', 'path': str(TARGET)}
        write_json(OUT, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1

    text = TARGET.read_text(encoding='utf-8')
    changed = False
    status = 'already_patched'
    if OLD in text:
        text = text.replace(OLD, NEW)
        TARGET.write_text(text, encoding='utf-8')
        changed = True
        status = 'patched'
    elif 'Use every configured target bookmaker supported by odds-api.io' not in text:
        status = 'old_function_not_found'

    report = {
        'status': status,
        'changed': changed,
        'version': POLICY_VERSION,
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'target': str(TARGET),
        'expected_requested_bookmakers': 'Bet365,Unibet,Betfair Exchange,Sbobet',
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if status in {'patched', 'already_patched'} else 1


if __name__ == '__main__':
    raise SystemExit(main())
