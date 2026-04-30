from __future__ import annotations

"""Startup patches applied before every Python entrypoint.

This file keeps emergency runtime fixes centralized.  It is loaded from the
repository root `sitecustomize.py`, so it runs for `python -m app.cli ...` and
for `python scripts/*.py` in GitHub Actions.
"""

import builtins
import json
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _write_if_changed(path: Path, old: str | None, new: str | None) -> None:
    if old is None or new is None or new == old:
        return
    try:
        path.write_text(new, encoding="utf-8")
    except Exception:
        return


def _patch_file(rel_path: str, patcher: Any) -> None:
    path = ROOT / rel_path
    text = _read(path)
    if text is None:
        return
    try:
        updated = patcher(text)
    except Exception:
        return
    _write_if_changed(path, text, updated)


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
# SportLogic: documented API contract and robust odds parsing/fallback.
# ---------------------------------------------------------------------------

def _patch_sportlogic_provider(text: str) -> str:
    text = text.replace('BASE_URL = "https://api.sportlogic.io/v1"', 'BASE_URL = "https://api.sportlogic.io/api/v1"')
    text = text.replace('os.getenv("SPORTLOGIC_HEADER_NAME", "Authorization")', 'os.getenv("SPORTLOGIC_HEADER_NAME", "X-API-Key")')
    text = text.replace('f"{self.base_url}/football/fixtures"', 'f"{self.base_url}/games"')
    text = text.replace('f"{self.base_url}/football/odds/{fixture_id}"', 'f"{self.base_url}/games/{fixture_id}/odds"')
    text = text.replace('f"{self.base_url}/football/results/{fixture_id}"', 'f"{self.base_url}/outcomes/{fixture_id}"')
    text = text.replace(
        'payload = await self._get_json(client, "/football/fixtures", {"date": date_key}, stats, preview)',
        'payload = await self._get_json(client, "/games", {"date_from": date_key, "date_to": date_key, "status": "scheduled", "per_page": 100}, stats, preview)',
    )
    text = text.replace(
        'payload = await self._get_json(client, "/games", {"date_from": date_key, "date_to": date_key, "status": "scheduled", "per_page": 100}, stats, preview)',
        'payload = await self._get_json(client, "/games", {"date_from": date_key, "date_to": date_key, "per_page": 100}, stats, preview)',
    )
    text = text.replace(
        '            response = await client.get(f"{self.base_url}{path}", headers=self._headers(), params=params or None)\n',
        '            stats.setdefault("attempted_paths", []).append({"path": path, "params": dict(params or {})})\n'
        '            response = await client.get(f"{self.base_url}{path}", headers=self._headers(), params=params or None)\n',
    )

    old_odds_block_1 = 'payload = await self._get_json(client, f"/football/odds/{event_id}", {}, stats, preview)\n                rows = self._extract_odds_rows(payload)'
    old_odds_block_2 = 'payload = await self._get_json(client, f"/games/{event_id}/odds", {}, stats, preview)\n                rows = self._extract_odds_rows(payload)'
    new_odds_block = (
        'payload = await self._get_json(client, f"/games/{event_id}/odds", {}, stats, preview)\n'
        '                rows = self._extract_odds_rows(payload)\n'
        '                if not rows:\n'
        '                    payload = await self._get_json(client, "/odds", {"game_id": event_id}, stats, preview)\n'
        '                    rows = self._extract_odds_rows(payload)'
    )
    if old_odds_block_1 in text:
        text = text.replace(old_odds_block_1, new_odds_block, 1)
    elif old_odds_block_2 in text and '"/odds", {"game_id": event_id}' not in text:
        text = text.replace(old_odds_block_2, new_odds_block, 1)

    if '"odds_rows_empty"' not in text:
        text = text.replace(
            '                if parsed:\n                    offers_by_match[item["match"].match_key].extend(parsed)\n                    stats["offers_parsed"] += len(parsed)\n',
            '                if not rows:\n                    stats["odds_rows_empty"] = int(stats.get("odds_rows_empty") or 0) + 1\n'
            '                if parsed:\n                    offers_by_match[item["match"].match_key].extend(parsed)\n                    stats["offers_parsed"] += len(parsed)\n',
            1,
        )

    marker = '        for row in rows:\n            # Shape A: bookmakers -> markets -> outcomes\n'
    patch = '''        for row in rows:
            # SportLogic documented flat odds shape: option_name/option_value/odds + market/bookmaker objects.
            if isinstance(row, dict) and ("option_name" in row or "market_id" in row or "market" in row) and ("odds" in row or "price" in row):
                market_payload = row.get("market") if isinstance(row.get("market"), dict) else {}
                bookmaker_payload = row.get("bookmaker") if isinstance(row.get("bookmaker"), dict) else {}
                market_key = str(market_payload.get("key") or row.get("market_key") or row.get("market") or "").strip().lower()
                market_name = str(market_payload.get("name") or market_key or row.get("market_id") or "sportlogic_market")
                option_name = str(row.get("option_name") or row.get("name") or row.get("selection") or row.get("label") or "").strip()
                option_value = row.get("option_value") if row.get("option_value") not in ("", None) else row.get("line") or row.get("point")
                price = row.get("odds") or row.get("price") or row.get("decimal") or row.get("value")
                book = str(bookmaker_payload.get("name") or row.get("bookmaker_name") or row.get("bookmaker") or "SportLogic")
                low = option_name.lower()
                if bool(row.get("is_suspended")):
                    continue
                if market_key in {"match_winner", "winner", "1x2", "full_time_result", "moneyline"}:
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
                if market_key in {"goals_over_under", "total_goals", "over_under", "totals", "total"}:
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
    if marker in text and 'SportLogic documented flat odds shape' not in text:
        text = text.replace(marker, patch, 1)

    # Make envelopes with pagination or nested data robust: data -> data/items/odds.
    if '"odds"' not in text.split('for key in ("data", "response", "results", "fixtures", "matches", "events", "items")', 1)[0][-300:]:
        text = text.replace(
            'for key in ("data", "response", "results", "fixtures", "matches", "events", "items"):',
            'for key in ("data", "response", "results", "fixtures", "matches", "events", "items", "odds"):',
        )
    return text


# ---------------------------------------------------------------------------
# Controlled fallback: no single-source Telegram reserve picks.
# ---------------------------------------------------------------------------

def _patch_controlled_fallback(text: str) -> str:
    if "controlled_fallback_sources_below_min" in text and "controlled_fallback_single_source_books_below_min" in text:
        return text
    marker = '    if metrics["sources_count"] <= 0:\n        reasons.append("missing_sources")\n'
    inject = marker + (
        '    if env_bool("CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES", True):\n'
        '        min_sources = max(1, env_int("CONTROLLED_FALLBACK_MIN_INDEPENDENT_SOURCES", 2))\n'
        '        if int(metrics.get("sources_count") or 0) < min_sources:\n'
        '            reasons.append(f"controlled_fallback_sources_below_min:{int(metrics.get(\"sources_count\") or 0)}/{min_sources}")\n'
        '    if env_bool("CONTROLLED_FALLBACK_REJECT_SINGLE_SOURCE_UNLESS_3_BOOKS", True):\n'
        '        single_source_min_books = max(1, env_int("CONTROLLED_FALLBACK_SINGLE_SOURCE_MIN_BOOKS", 3))\n'
        '        if int(metrics.get("sources_count") or 0) <= 1 and int(metrics.get("books_count") or 0) < single_source_min_books:\n'
        '            reasons.append(f"controlled_fallback_single_source_books_below_min:{int(metrics.get(\"books_count\") or 0)}/{single_source_min_books}")\n'
    )
    if marker in text:
        text = text.replace(marker, inject, 1)
    return text


# ---------------------------------------------------------------------------
# Detailed report: never use stale day-inventory summary after merge.
# ---------------------------------------------------------------------------

def _patch_detailed_report(text: str) -> str:
    text = text.replace('            "api_football",\n', '')
    text = text.replace('        "api_football",\n', '')
    if '"sportlogic",' not in text:
        text = text.replace('            "odds_api_io",\n', '            "odds_api_io",\n            "sportlogic",\n')
        text = text.replace('        "odds_api_io",\n', '        "odds_api_io",\n        "sportlogic",\n')
    old = 'def day_inventory_summary() -> dict[str, Any]:\n    return load_json(".data/exports/latest-day-inventory-summary.json", {})\n'
    new = '''def day_inventory_summary() -> dict[str, Any]:
    summary = load_json(".data/exports/latest-day-inventory-summary.json", {})
    if not isinstance(summary, dict):
        summary = {}
    counts = summary.setdefault("counts", {})
    if not isinstance(counts, dict):
        counts = {}
        summary["counts"] = counts

    merge = load_json(".data/exports/latest-day-inventory-coverage-merge.json", {})
    audit = load_json(".data/exports/latest-day-inventory-coverage-audit.json", {})
    debug = load_json(".logs/debug-last-run.json", {})
    runtime_counts = merge.get("runtime_counts") if isinstance(merge, dict) else {}
    if not isinstance(runtime_counts, dict):
        runtime_counts = {}
    if not runtime_counts and isinstance(debug, dict):
        dbg_summary = debug.get("summary") if isinstance(debug.get("summary"), dict) else debug
        runtime_counts = {
            "matches_seen": as_int(dbg_summary.get("matches_seen")),
            "matches_with_odds": as_int(dbg_summary.get("matches_with_offers")),
            "matches_with_context": as_int(dbg_summary.get("contexts_built")),
            "matches_ready_for_model": as_int(dbg_summary.get("contexts_built")),
        }

    merge_total = as_int(merge.get("matches_total")) if isinstance(merge, dict) else 0
    merge_odds = as_int(merge.get("matches_with_odds")) if isinstance(merge, dict) else 0
    merge_context = as_int(merge.get("matches_with_context")) if isinstance(merge, dict) else 0
    merge_ready = as_int(merge.get("matches_ready_for_model")) if isinstance(merge, dict) else 0
    if not merge_ready and isinstance(audit, dict):
        merge_ready = as_int(audit.get("matches_ready_for_model"))
    if not merge_total and isinstance(audit, dict):
        merge_total = as_int(audit.get("matches_total"))
    if not merge_odds and isinstance(audit, dict):
        merge_odds = as_int(audit.get("matches_with_odds"))
    if not merge_context and isinstance(audit, dict):
        merge_context = as_int(audit.get("matches_with_context"))

    runtime_total = as_int(runtime_counts.get("matches_seen"))
    runtime_odds = as_int(runtime_counts.get("matches_with_odds"))
    runtime_context = as_int(runtime_counts.get("matches_with_context"))
    runtime_ready = as_int(runtime_counts.get("matches_ready_for_model"))

    counts["matches_total"] = max(as_int(counts.get("matches_total")), merge_total, runtime_total)
    counts["matches_with_odds"] = max(as_int(counts.get("matches_with_odds")), merge_odds, runtime_odds)
    counts["matches_with_context"] = max(as_int(counts.get("matches_with_context")), merge_context, runtime_context)
    counts["matches_ready_for_model"] = max(as_int(counts.get("matches_ready_for_model")), merge_ready, runtime_ready)
    summary["runtime_counts_applied"] = bool(runtime_counts)
    summary["coverage_merge_counts_applied"] = bool(merge_total or merge_odds or merge_context or merge_ready)
    return summary
'''
    if old in text:
        text = text.replace(old, new, 1)
    return text


# ---------------------------------------------------------------------------
# Budget/runtime policy: SportLogic grant; no low-quota stale recovery.
# ---------------------------------------------------------------------------

def _patch_harizon_runtime_policy(text: str) -> str:
    if '"ENABLE_SPORTLOGIC": "true"' not in text:
        text = _replace_once(
            text,
            '        "BZZOIRO_MAX_PAGES": policy_value("BZZOIRO_MAX_PAGES", "80"),\n',
            '        "BZZOIRO_MAX_PAGES": policy_value("BZZOIRO_MAX_PAGES", "80"),\n'
            '        "ENABLE_SPORTLOGIC": "true",\n'
            '        "SPORTLOGIC_ENABLED": "true",\n'
            '        "SPORTLOGIC_BASE_URL": "https://api.sportlogic.io/api/v1",\n'
            '        "SPORTLOGIC_HEADER_NAME": "X-API-Key",\n'
            '        "SPORTLOGIC_PER_RUN_MAX": policy_value("SPORTLOGIC_PER_RUN_MAX", "40"),\n'
            '        "SPORTLOGIC_MATCH_LIMIT": policy_value("SPORTLOGIC_MATCH_LIMIT", "120"),\n'
            '        "SPORTLOGIC_ODDS_MATCH_LIMIT": policy_value("SPORTLOGIC_ODDS_MATCH_LIMIT", "32"),\n'
            '        "SPORTLOGIC_TIMEOUT_SECONDS": policy_value("SPORTLOGIC_TIMEOUT_SECONDS", "20"),\n'
            '        "CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES": "true",\n',
        )
    text = text.replace('"FOOTBALL_DATA_CONTEXT_MATCH_LIMIT": os.getenv("FOOTBALL_DATA_CONTEXT_MATCH_LIMIT") or "72"', '"FOOTBALL_DATA_CONTEXT_MATCH_LIMIT": os.getenv("FOOTBALL_DATA_CONTEXT_MATCH_LIMIT") or "48"')
    return text


def _patch_provider_budget(text: str) -> str:
    if "'sportlogic'" not in text.split("HARIZON_CRITICAL_PROVIDERS", 1)[1].split("}", 1)[0]:
        text = text.replace("    'openfootball_public',\n}", "    'openfootball_public',\n    'sportlogic',\n}", 1)
    text = text.replace("    'football_data',\n", "", 1)
    text = text.replace("    'thesportsdb',\n", "", 1)
    text = text.replace("    'football_data': 8,\n", "", 1)
    text = text.replace("    'thesportsdb': 12,\n", "", 1)
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
            'SPORTLOGIC_MATCH_LIMIT': '120',
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
            "env": {
                "ENABLE_SPORTLOGIC": "true",
                "SPORTLOGIC_ENABLED": "true",
                "SPORTLOGIC_BASE_URL": "https://api.sportlogic.io/api/v1",
                "SPORTLOGIC_HEADER_NAME": "X-API-Key",
                "SPORTLOGIC_PER_RUN_MAX": "40",
                "SPORTLOGIC_MATCH_LIMIT": "120",
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
        for name in ("football_data", "thesportsdb"):
            if name in providers:
                providers[name]["safe_daily_budget"] = min(int(providers[name].get("safe_daily_budget") or 72), 72 if name == "football_data" else 144)
                providers[name]["per_run_max"] = min(int(providers[name].get("per_run_max") or 4), 4 if name == "football_data" else 8)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        return


def _patch_workflow(text: str) -> str:
    if "SPORTLOGIC_API_KEY:" not in text:
        text = text.replace("      SPORTSBOOK_RAPIDAPI_KEY: ${{ secrets.SPORTSBOOK_RAPIDAPI_KEY }}\n", "      SPORTSBOOK_RAPIDAPI_KEY: ${{ secrets.SPORTSBOOK_RAPIDAPI_KEY }}\n      SPORTLOGIC_API_KEY: ${{ secrets.SPORTLOGIC_API_KEY }}\n", 1)
    if "SPORTLOGIC_BASE_URL:" not in text:
        text = text.replace("      SPORTLOGIC_TIMEOUT_SECONDS: \"20\"\n", "      SPORTLOGIC_TIMEOUT_SECONDS: \"20\"\n      SPORTLOGIC_BASE_URL: https://api.sportlogic.io/api/v1\n      SPORTLOGIC_HEADER_NAME: X-API-Key\n      CONTROLLED_FALLBACK_REQUIRE_INDEPENDENT_SOURCES: \"true\"\n", 1)
    return text


# ---------------------------------------------------------------------------
# Import hook and application entrypoint.
# ---------------------------------------------------------------------------

def apply_all() -> None:
    _patch_budget_config_file()
    _patch_file("app/providers/sportlogic_provider.py", _patch_sportlogic_provider)
    _patch_file("scripts/publish_controlled_fallback.py", _patch_controlled_fallback)
    _patch_file("scripts/build_detailed_run_report.py", _patch_detailed_report)
    _patch_file("scripts/apply_harizon_runtime_policy.py", _patch_harizon_runtime_policy)
    _patch_file("scripts/apply_provider_request_budget.py", _patch_provider_budget)
    _patch_file(".github/workflows/run-bot.yml", _patch_workflow)


def _apply_import_patches() -> None:
    utils_mod = sys.modules.get("app.utils")
    if utils_mod is not None:
        try:
            utils_mod.normalize_probability_percent = _normalize_probability_percent_patched
        except Exception:
            pass


def install_import_hook() -> None:
    original_import = builtins.__import__

    def patched_import(name, globals=None, locals=None, fromlist=(), level=0):
        module = original_import(name, globals, locals, fromlist, level)
        if name == "app.utils" or name.startswith("app.utils."):
            _apply_import_patches()
        return module

    builtins.__import__ = patched_import
    _apply_import_patches()


try:
    apply_all()
    install_import_hook()
except Exception:
    pass
