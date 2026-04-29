from __future__ import annotations

from pathlib import Path

ROOT = Path('.').resolve()
TARGET = ROOT / 'app' / 'providers' / 'odds_api_io.py'
PATCH_VERSION = 'v1-dual-account-odds-api-io'


def main() -> int:
    text = TARGET.read_text(encoding='utf-8')
    if 'ODDS_API_IO_DUAL_ACCOUNT_PATCH_VERSION' in text:
        print({'patch': PATCH_VERSION, 'changed': False, 'reason': 'already_applied'})
        return 0

    changed = False
    if 'import os\n' not in text:
        text = text.replace('from typing import Any\n', 'from typing import Any\nimport os\n', 1)
        changed = True

    old = '        self._requests_used = 0\n'
    new = '''        self._requests_used = 0
        self.ODDS_API_IO_DUAL_ACCOUNT_PATCH_VERSION = "v1-dual-account-odds-api-io"
        self._primary_api_key = str(getattr(settings, "odds_api_io_key", None) or os.getenv("ODDS_API_IO_KEY", "") or "").strip()
        self._secondary_api_key = str(os.getenv("ODDS_API_IO_KEY_2", "") or "").strip()
        self._account_request_counts = {"account1": 0, "account2": 0}
        self._account_request_limits = {
            "account1": max(0, int(float(os.getenv("ODDS_API_IO_ACCOUNT1_PER_RUN_MAX", "70") or 70))),
            "account2": max(0, int(float(os.getenv("ODDS_API_IO_ACCOUNT2_PER_RUN_MAX", "70") or 70))),
        }
'''
    if old in text:
        text = text.replace(old, new, 1)
        changed = True

    # Primary-key presence in both stats blocks.
    text = text.replace(
        '"api_key_present": bool(getattr(self.settings, "odds_api_io_key", None)),',
        '"api_key_present": bool(self._primary_api_key),\n            "secondary_api_key_present": bool(self._secondary_api_key),\n            "account_request_limits": dict(self._account_request_limits),\n            "account_request_counts": dict(self._account_request_counts),',
    )

    # Fetch matches still uses account1 only for fixture bootstrap/events.
    text = text.replace(
        '        api_key = getattr(self.settings, "odds_api_io_key", None)\n        if not api_key:',
        '        api_key = self._primary_api_key\n        if not api_key:',
        1,
    )
    # Fetch offers also starts from account1, but account2 is used for odds/multi below.
    text = text.replace(
        '        api_key = getattr(self.settings, "odds_api_io_key", None)\n        if not api_key:',
        '        api_key = self._primary_api_key\n        if not api_key:',
        1,
    )

    old = '''        target_books = self._bookmakers_param()
        stats["requested_bookmakers"] = target_books
'''
    new = '''        target_books = self._bookmakers_param()
        secondary_target_books = self._bookmakers_param_secondary()
        stats["requested_bookmakers"] = target_books
        stats["requested_bookmakers_secondary"] = secondary_target_books if self._secondary_api_key else None
'''
    if old in text:
        text = text.replace(old, new, 1)
        changed = True

    old = '''                event_list = await self._fetch_odds_multi_chunk(client, api_key, event_id_list, target_books, stats)
                if stats.get("rate_limited"):
                    break
'''
    new = '''                event_list = await self._fetch_odds_multi_chunk(client, api_key, event_id_list, target_books, stats)
                if self._secondary_api_key and secondary_target_books and not stats.get("rate_limited"):
                    secondary_events = await self._fetch_odds_multi_chunk(client, self._secondary_api_key, event_id_list, secondary_target_books, stats)
                    event_list = list(event_list) + list(secondary_events)
                if stats.get("rate_limited"):
                    break
'''
    if old in text:
        text = text.replace(old, new, 1)
        changed = True

    old = '''            if not self._request_budget_allows(stats):
                return []
            attempts += 1
            stats["odds_requests"] += 1
            self._requests_used += 1
'''
    new = '''            account_name = self._account_name_for_key(api_key)
            if not self._request_budget_allows(stats) or not self._account_request_budget_allows(account_name, stats):
                return []
            attempts += 1
            stats["odds_requests"] += 1
            self._requests_used += 1
            self._account_request_counts[account_name] = self._account_request_counts.get(account_name, 0) + 1
            stats["account_request_counts"] = dict(self._account_request_counts)
'''
    if old in text:
        text = text.replace(old, new, 1)
        changed = True

    old_method = '''    def _bookmakers_param(self) -> str:
        """Restrict odds-api.io requests to Bet365 and Unibet only.

        We intentionally ignore any extra bookmaker names that may appear in
        env/config so the provider cannot silently widen coverage again.
        """
        preferred = list(getattr(self.settings, "odds_api_io_bookmakers", []) or [])
        values: list[str] = []
        allowed = {
            "bet365": "Bet365",
            "unibet": "Unibet",
        }
        for item in preferred:
            raw = str(item or "").strip()
            if not raw:
                continue
            value = allowed.get(normalize_bookmaker_name(raw))
            if value and value not in values:
                values.append(value)
        return ",".join(values or ["Bet365", "Unibet"])
'''
    new_method = '''    def _bookmakers_param(self) -> str:
        """Account 1: original odds-api.io account, capped to Bet365 + Unibet."""
        preferred = [item.strip() for item in os.getenv("ODDS_API_IO_BOOKMAKERS_ACCOUNT1", "Bet365,Unibet").split(",") if item.strip()]
        if not preferred:
            preferred = list(getattr(self.settings, "odds_api_io_bookmakers", []) or [])
        return self._bookmakers_param_from_allowed(
            preferred,
            {
                "bet365": "Bet365",
                "unibet": "Unibet",
            },
            ["Bet365", "Unibet"],
        )

    def _bookmakers_param_secondary(self) -> str:
        """Account 2: separate odds-api.io account for two extra bookmakers.

        Default pair: Betfair Exchange + Sbobet. This complements Bet365/Unibet
        with an exchange/sharp signal instead of duplicating the same books.
        """
        preferred = [item.strip() for item in os.getenv("ODDS_API_IO_BOOKMAKERS_ACCOUNT2", "Betfair Exchange,Sbobet").split(",") if item.strip()]
        allowed = {
            "betfairexchange": "Betfair Exchange",
            "betfair": "Betfair Exchange",
            "sbobet": "Sbobet",
            "betmgm": "BetMGM",
            "fanduel": "FanDuel",
            "draftkings": "DraftKings",
            "betano": "Betano",
            "1xbet": "1xbet",
            "stake": "Stake",
            "circa": "Circa",
            "betfairsportsbook": "Betfair Sportsbook",
        }
        return self._bookmakers_param_from_allowed(preferred, allowed, ["Betfair Exchange", "Sbobet"])

    def _bookmakers_param_from_allowed(self, preferred: list[str], allowed: dict[str, str], fallback: list[str]) -> str:
        values: list[str] = []
        for item in preferred:
            raw = str(item or "").strip()
            if not raw:
                continue
            value = allowed.get(normalize_bookmaker_name(raw))
            if value and value not in values:
                values.append(value)
            if len(values) >= 2:
                break
        return ",".join(values or fallback[:2])

    def _account_name_for_key(self, api_key: str) -> str:
        return "account2" if self._secondary_api_key and api_key == self._secondary_api_key else "account1"

    def _account_request_budget_allows(self, account_name: str, stats: dict[str, Any]) -> bool:
        limit = int(self._account_request_limits.get(account_name, 0) or 0)
        used = int(self._account_request_counts.get(account_name, 0) or 0)
        stats["account_request_limits"] = dict(self._account_request_limits)
        stats["account_request_counts"] = dict(self._account_request_counts)
        if limit <= 0:
            stats["budget_exhausted"] = True
            stats[f"{account_name}_budget_exhausted"] = True
            return False
        if used >= limit:
            stats["budget_exhausted"] = True
            stats[f"{account_name}_budget_exhausted"] = True
            return False
        return True
'''
    if old_method in text:
        text = text.replace(old_method, new_method, 1)
        changed = True

    if changed:
        TARGET.write_text(text, encoding='utf-8')
    print({'patch': PATCH_VERSION, 'changed': changed, 'target': str(TARGET)})
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
