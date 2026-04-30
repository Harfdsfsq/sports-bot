"""Defensive runtime patches for Harizon sports-bot.

This file is imported automatically before app/scripts modules.  Keep patches
idempotent and non-fatal: startup must never fail because of a patch.
"""

from __future__ import annotations

import builtins
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def _patch_file(path: Path, patcher: Any) -> None:
    try:
        text = path.read_text(encoding="utf-8")
        updated = patcher(text)
        if isinstance(updated, str) and updated != text:
            path.write_text(updated, encoding="utf-8")
    except Exception:
        return


def _replace_once(text: str, old: str, new: str) -> str:
    return text.replace(old, new, 1) if old in text else text


def _normalize_probability_percent_patched(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, str):
            raw = value.strip()
            if not raw or raw.lower() in {"n/a", "na", "none", "null", "-", "--", "unknown"}:
                return None
            had_percent = "%" in raw
            raw = raw.replace("%", "").replace(",", ".")
            match = re.search(r"[-+]?\d*\.?\d+", raw)
            if not match:
                return None
            number = float(match.group(0))
            if had_percent or number > 1.0:
                number /= 100.0
        else:
            number = float(value)
            if number > 1.0:
                number /= 100.0
    except Exception:
        return None
    return max(0.0, min(1.0, number))


# ---------------------------------------------------------------------------
# Runner wiring.
# ---------------------------------------------------------------------------

def _patch_runner_text(text: str) -> str:
    text = text.replace(
        "        self.api_football = self._safe_provider('app.providers.api_football', 'ApiFootballContextProvider')\n",
        "        self.api_football = None  # api-football removed from Harizon runtime\n",
        1,
    )
    if "self.sportlogic" not in text:
        text = _replace_once(
            text,
            "        self.allsportsapi = self._safe_provider('app.providers.allsportsapi', 'AllSportsApiOddsProvider')\n",
            "        self.allsportsapi = self._safe_provider('app.providers.allsportsapi', 'AllSportsApiOddsProvider')\n"
            "        self.sportlogic = self._safe_provider('app.providers.sportlogic_provider', 'SportLogicProvider')\n",
        )
    if "'sportlogic': self.sportlogic" not in text:
        text = _replace_once(
            text,
            "            'allsportsapi': self.allsportsapi,\n            'bookies_bootstrap': self.bookies_bootstrap,\n",
            "            'allsportsapi': self.allsportsapi,\n"
            "            'sportlogic': self.sportlogic,\n"
            "            'bookies_bootstrap': self.bookies_bootstrap,\n",
        )
    if "key == 'sportlogic'" not in text:
        text = _replace_once(
            text,
            "        if key == 'allsportsapi':\n            return bool(getattr(self.settings, 'allsportsapi_api_key', None))\n        return True\n",
            "        if key == 'allsportsapi':\n            return bool(getattr(self.settings, 'allsportsapi_api_key', None))\n"
            "        if key == 'sportlogic':\n"
            "            return bool(getattr(self.settings, 'sportlogic_api_key', None) or os.getenv('SPORTLOGIC_API_KEY') or os.getenv('SPORTLOGIC_KEY') or os.getenv('SPORTLOGIC_TOKEN'))\n"
            "        return True\n",
        )
    if "provider_name == 'sportlogic'" not in text:
        text = _replace_once(
            text,
            "        if provider_name == 'odds_api_io':\n            return bool(getattr(self.settings, 'enable_odds_api_io', default))\n",
            "        if provider_name == 'odds_api_io':\n            return bool(getattr(self.settings, 'enable_odds_api_io', default))\n"
            "        if provider_name == 'sportlogic':\n"
            "            return str(os.getenv('ENABLE_SPORTLOGIC', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'} and str(os.getenv('SPORTLOGIC_ENABLED', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'}\n",
        )
    if "module_name.endswith('sportlogic_provider')" not in text:
        text = _replace_once(
            text,
            "        if module_name.endswith('allsportsapi') and not self._provider_enabled('allsportsapi', default=False):\n            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')\n            return None\n",
            "        if module_name.endswith('allsportsapi') and not self._provider_enabled('allsportsapi', default=False):\n"
            "            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')\n"
            "            return None\n"
            "        if module_name.endswith('sportlogic_provider') and not self._provider_enabled('sportlogic', default=True):\n"
            "            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')\n"
            "            return None\n",
        )
    if "sportlogic_offers" not in text:
        text = _replace_once(
            text,
            "                (allsportsapi_offers, allsportsapi_stats, allsportsapi_preview),\n            ) = await asyncio.gather(\n",
            "                (allsportsapi_offers, allsportsapi_stats, allsportsapi_preview),\n"
            "                (sportlogic_offers, sportlogic_stats, sportlogic_preview),\n"
            "            ) = await asyncio.gather(\n",
        )
        text = _replace_once(
            text,
            "                self._fetch_provider(\n                    self.allsportsapi,\n                    'fetch_offers',\n                    filtered_matches,\n                    empty_data={},\n                ),\n            )\n",
            "                self._fetch_provider(\n"
            "                    self.allsportsapi,\n"
            "                    'fetch_offers',\n"
            "                    filtered_matches,\n"
            "                    empty_data={},\n"
            "                ),\n"
            "                self._fetch_provider(\n"
            "                    self.sportlogic,\n"
            "                    'fetch_offers',\n"
            "                    filtered_matches,\n"
            "                    empty_data={},\n"
            "                ),\n"
            "            )\n",
        )
        text = _replace_once(text, "                'allsportsapi': allsportsapi_offers,\n            }\n", "                'allsportsapi': allsportsapi_offers,\n                'sportlogic': sportlogic_offers,\n            }\n")
    if "provider_targets['sportlogic']" not in text:
        text = _replace_once(
            text,
            "                'gnews': self._select_provider_context_matches(context_target_matches, 'gnews', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n            }\n",
            "                'gnews': self._select_provider_context_matches(context_target_matches, 'gnews', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n"
            "                'sportlogic': self._select_provider_context_matches(context_target_matches, 'sportlogic', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n"
            "            }\n",
        )
    if "sportlogic_contexts" not in text:
        text = _replace_once(text, "                (gnews_contexts, gnews_stats, gnews_preview),\n            ) = await asyncio.gather(\n", "                (gnews_contexts, gnews_stats, gnews_preview),\n                (sportlogic_contexts, sportlogic_context_stats, sportlogic_context_preview),\n            ) = await asyncio.gather(\n")
        text = _replace_once(text, "                self._fetch_provider(self.gnews, 'fetch_context', provider_targets['gnews'], empty_data={}),\n            )\n", "                self._fetch_provider(self.gnews, 'fetch_context', provider_targets['gnews'], empty_data={}),\n                self._fetch_provider(self.sportlogic, 'fetch_context', provider_targets['sportlogic'], empty_data={}),\n            )\n")
        text = _replace_once(text, "                'gnews': gnews_contexts,\n            }\n", "                'gnews': gnews_contexts,\n                'sportlogic': sportlogic_contexts,\n            }\n")
    if "'sportlogic': sportlogic_stats" not in text:
        text = _replace_once(text, "                'allsportsapi': allsportsapi_stats,\n                'futrixmetrics': futrixmetrics_stats,\n", "                'allsportsapi': allsportsapi_stats,\n                'sportlogic': sportlogic_stats,\n                'futrixmetrics': futrixmetrics_stats,\n")
    if "'sportlogic_context': sportlogic_context_stats" not in text:
        text = _replace_once(text, "                'gnews': gnews_stats,\n                'weather': weather_stats,\n", "                'gnews': gnews_stats,\n                'sportlogic_context': sportlogic_context_stats,\n                'weather': weather_stats,\n")
    return text


# ---------------------------------------------------------------------------
# SportLogic documented contract patch.
# ---------------------------------------------------------------------------

def _patch_sportlogic_provider_text(text: str) -> str:
    text = text.replace('BASE_URL = "https://api.sportlogic.io/v1"', 'BASE_URL = "https://api.sportlogic.io/api/v1"')
    text = text.replace('os.getenv("SPORTLOGIC_HEADER_NAME", "Authorization")', 'os.getenv("SPORTLOGIC_HEADER_NAME", "X-API-Key")')
    text = text.replace('f"{self.base_url}/football/fixtures"', 'f"{self.base_url}/games"')
    text = text.replace('f"{self.base_url}/football/odds/{fixture_id}"', 'f"{self.base_url}/games/{fixture_id}/odds"')
    text = text.replace('f"{self.base_url}/football/results/{fixture_id}"', 'f"{self.base_url}/outcomes/{fixture_id}"')
    text = text.replace(
        'payload = await self._get_json(client, "/football/fixtures", {"date": date_key}, stats, preview)',
        'payload = await self._get_json(client, "/games", {"date_from": date_key, "date_to": date_key, "status": "scheduled", "per_page": 100}, stats, preview)',
    )
    text = text.replace('payload = await self._get_json(client, f"/football/odds/{event_id}", {}, stats, preview)', 'payload = await self._get_json(client, f"/games/{event_id}/odds", {}, stats, preview)')
    text = text.replace(
        '            response = await client.get(f"{self.base_url}{path}", headers=self._headers(), params=params or None)\n',
        '            stats.setdefault("attempted_paths", []).append({"path": path, "params": dict(params or {})})\n'
        '            response = await client.get(f"{self.base_url}{path}", headers=self._headers(), params=params or None)\n',
    )
    marker = '        for row in rows:\n            # Shape A: bookmakers -> markets -> outcomes\n'
    patch = '''        for row in rows:
            # SportLogic documented flat odds shape: option_name/option_value/odds + market/bookmaker objects.
            if isinstance(row, dict) and ("option_name" in row or "market_id" in row) and ("odds" in row or "price" in row):
                market_payload = row.get("market") if isinstance(row.get("market"), dict) else {}
                bookmaker_payload = row.get("bookmaker") if isinstance(row.get("bookmaker"), dict) else {}
                market_key = str(market_payload.get("key") or row.get("market_key") or row.get("market") or "").strip().lower()
                market_name = str(market_payload.get("name") or market_key or row.get("market_id") or "sportlogic_market")
                option_name = str(row.get("option_name") or row.get("name") or row.get("selection") or "").strip()
                option_value = row.get("option_value") if row.get("option_value") not in ("", None) else row.get("line")
                price = row.get("odds") or row.get("price") or row.get("decimal")
                book = str(bookmaker_payload.get("name") or row.get("bookmaker_name") or row.get("bookmaker") or "SportLogic")
                low = option_name.lower()
                if bool(row.get("is_suspended")):
                    continue
                if market_key in {"match_winner", "winner", "1x2", "full_time_result"}:
                    if low in {"home", "1"}:
                        add(book, "h2h", match.home_team, price, team_side="home", market_name=market_name)
                    elif low in {"draw", "x", "tie"}:
                        add(book, "h2h", "Draw", price, market_name=market_name)
                    elif low in {"away", "2"}:
                        add(book, "h2h", match.away_team, price, team_side="away", market_name=market_name)
                    else:
                        side = "home" if option_name == match.home_team else "away" if option_name == match.away_team else None
                        add(book, "h2h", option_name, price, team_side=side, market_name=market_name)
                    continue
                if market_key in {"goals_over_under", "total_goals", "over_under", "totals"}:
                    if "over" in low:
                        add(book, "totals", "Over", price, self._float(option_value), market_name=market_name)
                    elif "under" in low:
                        add(book, "totals", "Under", price, self._float(option_value), market_name=market_name)
                    continue
                if market_key in {"both_teams_to_score", "btts"}:
                    if "yes" in low:
                        add(book, "btts", "Yes", price, market_name=market_name)
                    elif "no" in low:
                        add(book, "btts", "No", price, market_name=market_name)
                    continue

            # Shape A: bookmakers -> markets -> outcomes
'''
    if marker in text and "SportLogic documented flat odds shape" not in text:
        text = text.replace(marker, patch, 1)
    return text


# ---------------------------------------------------------------------------
# Runtime policy/budget patches based on docs catalog.
# ---------------------------------------------------------------------------

def _patch_harizon_runtime_policy_text(text: str) -> str:
    if '"ENABLE_SPORTLOGIC": "true"' not in text:
        text = _replace_once(
            text,
            '        "BZZOIRO_MAX_PAGES": policy_value("BZZOIRO_MAX_PAGES", "24"),\n',
            '        "BZZOIRO_MAX_PAGES": policy_value("BZZOIRO_MAX_PAGES", "24"),\n'
            '        "ENABLE_SPORTLOGIC": "true",\n'
            '        "SPORTLOGIC_ENABLED": "true",\n'
            '        "SPORTLOGIC_BASE_URL": "https://api.sportlogic.io/api/v1",\n'
            '        "SPORTLOGIC_HEADER_NAME": "X-API-Key",\n'
            '        "SPORTLOGIC_PER_RUN_MAX": policy_value("SPORTLOGIC_PER_RUN_MAX", "40"),\n'
            '        "SPORTLOGIC_MATCH_LIMIT": policy_value("SPORTLOGIC_MATCH_LIMIT", "80"),\n'
            '        "SPORTLOGIC_ODDS_MATCH_LIMIT": policy_value("SPORTLOGIC_ODDS_MATCH_LIMIT", "32"),\n'
            '        "SPORTLOGIC_TIMEOUT_SECONDS": policy_value("SPORTLOGIC_TIMEOUT_SECONDS", "20"),\n',
        )
    # football-data.org registered docs: 10 req/min; current daily cap should be respected, never bypassed.
    text = text.replace('"FOOTBALL_DATA_CONTEXT_MATCH_LIMIT": os.getenv("FOOTBALL_DATA_CONTEXT_MATCH_LIMIT") or "72"', '"FOOTBALL_DATA_CONTEXT_MATCH_LIMIT": os.getenv("FOOTBALL_DATA_CONTEXT_MATCH_LIMIT") or "48"')
    return text


def _patch_provider_budget_text(text: str) -> str:
    if "'sportlogic'" not in text.split("HARIZON_CRITICAL_PROVIDERS", 1)[1].split("}", 1)[0]:
        text = text.replace("    'openfootball_public',\n}", "    'openfootball_public',\n    'sportlogic',\n}", 1)
    # Do not bypass football-data.org safe daily cap. It has a tight free/registered quota and was observed above cap.
    text = text.replace("    'football_data',\n", "", 1)
    text = text.replace("    'football_data': 8,\n", "", 1)
    if "'sportlogic': 40" not in text:
        text = text.replace("    'openfootball_public': 8,\n}", "    'openfootball_public': 8,\n    'sportlogic': 40,\n}", 1)
    if "'sportlogic': {" not in text:
        insert = """
    'sportlogic': {
        'per_run_max': 40,
        'safe_daily_budget': 480,
        'min_spacing_minutes': 0,
        'env': {
            'ENABLE_SPORTLOGIC': 'true',
            'SPORTLOGIC_ENABLED': 'true',
            'SPORTLOGIC_BASE_URL': 'https://api.sportlogic.io/api/v1',
            'SPORTLOGIC_HEADER_NAME': 'X-API-Key',
            'SPORTLOGIC_PER_RUN_MAX': '40',
            'SPORTLOGIC_MATCH_LIMIT': '80',
            'SPORTLOGIC_ODDS_MATCH_LIMIT': '32',
            'SPORTLOGIC_TIMEOUT_SECONDS': '20',
        },
        'disable_env': {
            'ENABLE_SPORTLOGIC': 'false',
            'SPORTLOGIC_ENABLED': 'false',
            'SPORTLOGIC_PER_RUN_MAX': '0',
            'SPORTLOGIC_MATCH_LIMIT': '0',
            'SPORTLOGIC_ODDS_MATCH_LIMIT': '0',
        },
    },
"""
        text = text.replace("    'bzzoiro': {", insert + "    'bzzoiro': {", 1)
    # Hard guard: only truly critical/high-quota providers may recover after stale daily counter exhaustion.
    text = text.replace(
        "and name in HARIZON_CRITICAL_PROVIDERS\n            and decision['reason'].startswith('daily_budget_exhausted:')",
        "and name in {'odds_api_io', 'bzzoiro', 'sstats', 'sportlogic'}\n            and decision['reason'].startswith('daily_budget_exhausted:')",
    )
    return text


def _patch_budget_config_file() -> None:
    path = ROOT / "config" / "provider_request_budget.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        providers = payload.setdefault("providers", {})
        providers["sportlogic"] = {
            "enabled": True,
            "per_run_max": 40,
            "safe_daily_budget": 480,
            "min_spacing_minutes": 0,
            "secret_env_keys": ["SPORTLOGIC_API_KEY"],
            "limit": {"documented_daily_assumption": 500, "safe_buffer_requests": 20},
            "env": {
                "ENABLE_SPORTLOGIC": "true",
                "SPORTLOGIC_ENABLED": "true",
                "SPORTLOGIC_BASE_URL": "https://api.sportlogic.io/api/v1",
                "SPORTLOGIC_HEADER_NAME": "X-API-Key",
                "SPORTLOGIC_PER_RUN_MAX": "40",
                "SPORTLOGIC_MATCH_LIMIT": "80",
                "SPORTLOGIC_ODDS_MATCH_LIMIT": "32",
                "SPORTLOGIC_TIMEOUT_SECONDS": "20",
            },
            "disable_env": {
                "ENABLE_SPORTLOGIC": "false",
                "SPORTLOGIC_ENABLED": "false",
                "SPORTLOGIC_PER_RUN_MAX": "0",
                "SPORTLOGIC_MATCH_LIMIT": "0",
                "SPORTLOGIC_ODDS_MATCH_LIMIT": "0",
                "SPORTLOGIC_API_KEY": "",
            },
        }
        if "football_data" in providers:
            providers["football_data"]["safe_daily_budget"] = min(int(providers["football_data"].get("safe_daily_budget") or 72), 72)
            providers["football_data"]["per_run_max"] = min(int(providers["football_data"].get("per_run_max") or 4), 4)
            providers["football_data"].setdefault("limit", {})["registered_requests_per_minute"] = 10
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        return


# ---------------------------------------------------------------------------
# Reports/workflow.
# ---------------------------------------------------------------------------

def _patch_detailed_report_text(text: str) -> str:
    text = text.replace('            "api_football",\n', '')
    text = text.replace('        "api_football",\n', '')
    if '"sportlogic",' not in text:
        text = text.replace('            "odds_api_io",\n', '            "odds_api_io",\n            "sportlogic",\n')
        text = text.replace('        "odds_api_io",\n', '        "odds_api_io",\n        "sportlogic",\n')
    return text


def _patch_workflow_text(text: str) -> str:
    if "SPORTLOGIC_API_KEY:" not in text:
        text = text.replace("      SPORTSBOOK_RAPIDAPI_KEY: ${{ secrets.SPORTSBOOK_RAPIDAPI_KEY }}\n", "      SPORTSBOOK_RAPIDAPI_KEY: ${{ secrets.SPORTSBOOK_RAPIDAPI_KEY }}\n      SPORTLOGIC_API_KEY: ${{ secrets.SPORTLOGIC_API_KEY }}\n", 1)
    if "SPORTLOGIC_BASE_URL:" not in text:
        text = text.replace("      SPORTLOGIC_TIMEOUT_SECONDS: \"20\"\n", "      SPORTLOGIC_TIMEOUT_SECONDS: \"20\"\n      SPORTLOGIC_BASE_URL: https://api.sportlogic.io/api/v1\n      SPORTLOGIC_HEADER_NAME: X-API-Key\n", 1)
    return text


def _apply_file_patches() -> None:
    _patch_budget_config_file()
    _patch_file(ROOT / "app" / "services" / "runner.py", _patch_runner_text)
    _patch_file(ROOT / "app" / "providers" / "sportlogic_provider.py", _patch_sportlogic_provider_text)
    _patch_file(ROOT / "scripts" / "apply_harizon_runtime_policy.py", _patch_harizon_runtime_policy_text)
    _patch_file(ROOT / "scripts" / "apply_provider_request_budget.py", _patch_provider_budget_text)
    _patch_file(ROOT / "scripts" / "build_detailed_run_report.py", _patch_detailed_report_text)
    _patch_file(ROOT / ".github" / "workflows" / "run-bot.yml", _patch_workflow_text)


# ---------------------------------------------------------------------------
# Import-time monkey patches.
# ---------------------------------------------------------------------------

def _apply_import_patches() -> None:
    utils_mod = sys.modules.get("app.utils")
    if utils_mod is not None:
        try:
            utils_mod.normalize_probability_percent = _normalize_probability_percent_patched
        except Exception:
            pass


_original_import = builtins.__import__


def _patched_import(name, globals=None, locals=None, fromlist=(), level=0):
    module = _original_import(name, globals, locals, fromlist, level)
    if name == "app.utils" or name.startswith("app.utils."):
        _apply_import_patches()
    return module


try:
    _apply_file_patches()
except Exception:
    pass

builtins.__import__ = _patched_import
_apply_import_patches()
