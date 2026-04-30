from __future__ import annotations

"""Runtime patch for strict odds-api.io two-account routing.

Routing:
- account1 uses ODDS_API_IO_KEY and requests only Bet365,Unibet
- account2 uses ODDS_API_IO_KEY_2 or the second value from ODDS_API_IO_KEY_POOL and requests only Betfair Exchange,Sbobet

The provider merges both returned odds payloads after both calls.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path('.').resolve()
TARGET = ROOT / 'app' / 'providers' / 'odds_api_io.py'
OUT = ROOT / '.data' / 'exports' / 'latest-odds-api-io-bookmaker-patch.json'
POLICY_VERSION = 'v2-strict-two-account-routing'
UTC = timezone.utc

BOOKMAKER_BLOCK = '''    def _bookmakers_param(self) -> str:\n        return "Bet365,Unibet"\n\n    def _odds_account_specs(self, primary_key: str) -> list[tuple[str, str, str]]:\n        specs: list[tuple[str, str, str]] = []\n        first = str(primary_key or "").strip()\n        if first:\n            specs.append(("account1", first, "Bet365,Unibet"))\n        second = str(os.getenv("ODDS_API_IO_KEY_2") or "").strip()\n        if not second:\n            pool = str(os.getenv("ODDS_API_IO_KEY_POOL") or "").strip()\n            values = [item.strip() for item in pool.split(",") if item.strip()] if pool else []\n            if len(values) >= 2:\n                second = values[1]\n        if second and second != first:\n            specs.append(("account2", second, "Betfair Exchange,Sbobet"))\n        return specs\n'''

FETCH_BLOCK = '''    async def _fetch_odds_multi_chunk(\n        self,\n        client: httpx.AsyncClient,\n        api_key: str,\n        event_ids: list[int],\n        target_books: str,\n        stats: dict[str, Any],\n    ) -> list[dict[str, Any]]:\n        combined: list[dict[str, Any]] = []\n        specs = self._odds_account_specs(api_key)\n        stats["requested_bookmakers"] = "; ".join(f"{name}:{books}" for name, _key, books in specs)\n        stats["requested_bookmakers_by_account"] = [\n            {"account": name, "bookmakers": books} for name, _key, books in specs\n        ]\n        for name, key, books in specs:\n            rows = await self._fetch_odds_multi_chunk_single(client, key, event_ids, books, stats, name)\n            combined.extend(rows)\n            if stats.get("rate_limited"):\n                break\n        return combined\n\n    async def _fetch_odds_multi_chunk_single(\n        self,\n        client: httpx.AsyncClient,\n        api_key: str,\n        event_ids: list[int],\n        target_books: str,\n        stats: dict[str, Any],\n        account_name: str,\n    ) -> list[dict[str, Any]]:\n        if not event_ids:\n            return []\n        attempts = 0\n        while attempts < 2:\n            if not self._request_budget_allows(stats):\n                return []\n            attempts += 1\n            stats["odds_requests"] += 1\n            self._requests_used += 1\n            try:\n                response = await self._request_odds_multi(client, api_key, event_ids, target_books)\n            except Exception as exc:\n                stats["response_errors"] += 1\n                stats["last_body_preview"] = f"{account_name} odds request failed: {exc}"\n                continue\n            stats["odds_http_statuses"].append(response.status_code)\n            stats["last_body_preview"] = response.text[:2000]\n            if response.status_code == 429:\n                stats["response_errors"] += 1\n                stats["rate_limited"] = True\n                stats["rate_limited_account"] = account_name\n                return []\n            if response.status_code == 200:\n                payload = self._safe_json(response)\n                if payload is None:\n                    stats["response_errors"] += 1\n                    continue\n                shape = self._payload_shape(payload)\n                if shape not in stats["payload_shapes"]:\n                    stats["payload_shapes"].append(shape)\n                rows = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []\n                stats[f"{account_name}_odds_payload_events"] = int(stats.get(f"{account_name}_odds_payload_events") or 0) + len(rows)\n                return rows\n            if response.status_code >= 500 and len(event_ids) > 1:\n                mid = max(1, len(event_ids) // 2)\n                left = await self._fetch_odds_multi_chunk_single(client, api_key, event_ids[:mid], target_books, stats, account_name)\n                right = await self._fetch_odds_multi_chunk_single(client, api_key, event_ids[mid:], target_books, stats, account_name)\n                return left + right\n            stats["response_errors"] += 1\n            if response.status_code < 500:\n                return []\n        return []\n'''


def replace_block(text: str, start_marker: str, end_marker: str, replacement: str) -> tuple[str, bool]:
    start = text.find(start_marker)
    if start < 0:
        return text, False
    end = text.find(end_marker, start + len(start_marker))
    if end < 0:
        return text, False
    return text[:start] + replacement + text[end:], True


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + '\n', encoding='utf-8')


def main() -> int:
    if not TARGET.exists():
        report = {'status': 'missing_target'}
        write_json(OUT, report)
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    text = TARGET.read_text(encoding='utf-8')
    actions: list[str] = []
    changed = False
    if 'import os\n' not in text:
        text = text.replace('from __future__ import annotations\n\n', 'from __future__ import annotations\n\nimport os\n')
        actions.append('add_os_import')
        changed = True
    if '_odds_account_specs' not in text:
        text, ok = replace_block(text, '    def _bookmakers_param(self) -> str:\n', '    @staticmethod\n    def _safe_json', BOOKMAKER_BLOCK + '\n    @staticmethod\n    def _safe_json')
        actions.append('replace_bookmaker_block' if ok else 'bookmaker_block_not_found')
        changed = changed or ok
    else:
        actions.append('bookmaker_block_already_patched')
    if '_fetch_odds_multi_chunk_single' not in text:
        text, ok = replace_block(text, '    async def _fetch_odds_multi_chunk(\n', '    def _request_budget_allows', FETCH_BLOCK + '\n    def _request_budget_allows')
        actions.append('replace_fetch_block' if ok else 'fetch_block_not_found')
        changed = changed or ok
    else:
        actions.append('fetch_block_already_patched')
    if changed:
        TARGET.write_text(text, encoding='utf-8')
    ok = '_odds_account_specs' in text and '_fetch_odds_multi_chunk_single' in text
    report = {
        'status': 'ok' if ok else 'incomplete',
        'changed': changed,
        'actions': actions,
        'version': POLICY_VERSION,
        'updated_at_utc': datetime.now(UTC).isoformat(),
        'account1': 'ODDS_API_IO_KEY -> Bet365,Unibet',
        'account2': 'ODDS_API_IO_KEY_2 or pool[1] -> Betfair Exchange,Sbobet',
    }
    write_json(OUT, report)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if ok else 1


if __name__ == '__main__':
    raise SystemExit(main())
