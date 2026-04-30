"""Runtime hotfixes for sports-bot.

Imported automatically by Python before app modules.  Keep this file defensive:
it must never break CLI startup.  It handles three runtime fixes:

1) Preserve the probability normalizer hotfix.
2) Remove api-football from runtime without requiring a large runner rewrite.
3) Wire SportLogic into offers/context and clean human reports/inventory counts.
"""

from __future__ import annotations

import builtins
import re
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent


def _normalize_probability_percent_patched(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    had_percent_sign = False
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        lowered = text.lower()
        if lowered in {"n/a", "na", "none", "null", "-", "--", "unknown"}:
            return None
        had_percent_sign = "%" in text
        text = text.replace("%", "").replace(",", ".").strip()
        match = re.search(r"[-+]?\d*\.?\d+", text)
        if not match:
            return None
        try:
            number = float(match.group(0))
        except ValueError:
            return None
    else:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
    if had_percent_sign or number > 1.0:
        number /= 100.0
    if number < 0.0:
        number = 0.0
    elif number > 1.0:
        number = 1.0
    return number


def _replace_once(text: str, old: str, new: str) -> tuple[str, bool]:
    if old not in text:
        return text, False
    return text.replace(old, new, 1), True


def _patch_file(path: Path, patcher: Any) -> None:
    try:
        text = path.read_text(encoding="utf-8")
        updated = patcher(text)
        if isinstance(updated, str) and updated != text:
            path.write_text(updated, encoding="utf-8")
    except Exception:
        return


def _patch_runner_text(text: str) -> str:
    # Fully remove api-football from runtime loading/API calls.
    text = text.replace(
        "        self.api_football = self._safe_provider('app.providers.api_football', 'ApiFootballContextProvider')\n",
        "        self.api_football = None  # api-football removed from Harizon runtime\n",
        1,
    )

    # SportLogic instance.
    if "self.sportlogic" not in text:
        text, _ = _replace_once(
            text,
            "        self.allsportsapi = self._safe_provider('app.providers.allsportsapi', 'AllSportsApiOddsProvider')\n",
            "        self.allsportsapi = self._safe_provider('app.providers.allsportsapi', 'AllSportsApiOddsProvider')\n"
            "        self.sportlogic = self._safe_provider('app.providers.sportlogic_provider', 'SportLogicProvider')\n",
        )

    # Provider lookup.
    if "'sportlogic': self.sportlogic" not in text:
        text, _ = _replace_once(
            text,
            "            'allsportsapi': self.allsportsapi,\n            'bookies_bootstrap': self.bookies_bootstrap,\n",
            "            'allsportsapi': self.allsportsapi,\n"
            "            'sportlogic': self.sportlogic,\n"
            "            'bookies_bootstrap': self.bookies_bootstrap,\n",
        )

    # Auth readiness.
    if "key == 'sportlogic'" not in text:
        text, _ = _replace_once(
            text,
            "        if key == 'allsportsapi':\n            return bool(getattr(self.settings, 'allsportsapi_api_key', None))\n        return True\n",
            "        if key == 'allsportsapi':\n            return bool(getattr(self.settings, 'allsportsapi_api_key', None))\n"
            "        if key == 'sportlogic':\n"
            "            return bool(getattr(self.settings, 'sportlogic_api_key', None) or os.getenv('SPORTLOGIC_API_KEY') or os.getenv('SPORTLOGIC_KEY') or os.getenv('SPORTLOGIC_TOKEN'))\n"
            "        return True\n",
        )

    # Enable flag.
    if "provider_name == 'sportlogic'" not in text:
        text, _ = _replace_once(
            text,
            "        if provider_name == 'odds_api_io':\n            return bool(getattr(self.settings, 'enable_odds_api_io', default))\n",
            "        if provider_name == 'odds_api_io':\n            return bool(getattr(self.settings, 'enable_odds_api_io', default))\n"
            "        if provider_name == 'sportlogic':\n"
            "            return str(os.getenv('ENABLE_SPORTLOGIC', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'} and str(os.getenv('SPORTLOGIC_ENABLED', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'}\n",
        )

    # Safe provider loading.
    if "module_name.endswith('sportlogic_provider')" not in text:
        text, _ = _replace_once(
            text,
            "        if module_name.endswith('allsportsapi') and not self._provider_enabled('allsportsapi', default=False):\n            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')\n            return None\n",
            "        if module_name.endswith('allsportsapi') and not self._provider_enabled('allsportsapi', default=False):\n"
            "            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')\n"
            "            return None\n"
            "        if module_name.endswith('sportlogic_provider') and not self._provider_enabled('sportlogic', default=True):\n"
            "            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')\n"
            "            return None\n",
        )

    # Odds offers gather.
    if "sportlogic_offers" not in text:
        text, _ = _replace_once(
            text,
            "                (allsportsapi_offers, allsportsapi_stats, allsportsapi_preview),\n            ) = await asyncio.gather(\n",
            "                (allsportsapi_offers, allsportsapi_stats, allsportsapi_preview),\n"
            "                (sportlogic_offers, sportlogic_stats, sportlogic_preview),\n"
            "            ) = await asyncio.gather(\n",
        )
        text, _ = _replace_once(
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
        text, _ = _replace_once(
            text,
            "                'allsportsapi': allsportsapi_offers,\n            }\n",
            "                'allsportsapi': allsportsapi_offers,\n"
            "                'sportlogic': sportlogic_offers,\n"
            "            }\n",
        )

    # Context targets/gather.
    if "provider_targets['sportlogic']" not in text:
        text, _ = _replace_once(
            text,
            "                'gnews': self._select_provider_context_matches(context_target_matches, 'gnews', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n            }\n",
            "                'gnews': self._select_provider_context_matches(context_target_matches, 'gnews', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n"
            "                'sportlogic': self._select_provider_context_matches(context_target_matches, 'sportlogic', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n"
            "            }\n",
        )
    if "sportlogic_contexts" not in text:
        text, _ = _replace_once(
            text,
            "                (gnews_contexts, gnews_stats, gnews_preview),\n            ) = await asyncio.gather(\n",
            "                (gnews_contexts, gnews_stats, gnews_preview),\n"
            "                (sportlogic_contexts, sportlogic_context_stats, sportlogic_context_preview),\n"
            "            ) = await asyncio.gather(\n",
        )
        text, _ = _replace_once(
            text,
            "                self._fetch_provider(self.gnews, 'fetch_context', provider_targets['gnews'], empty_data={}),\n            )\n",
            "                self._fetch_provider(self.gnews, 'fetch_context', provider_targets['gnews'], empty_data={}),\n"
            "                self._fetch_provider(self.sportlogic, 'fetch_context', provider_targets['sportlogic'], empty_data={}),\n"
            "            )\n",
        )
        text, _ = _replace_once(
            text,
            "                'gnews': gnews_contexts,\n            }\n",
            "                'gnews': gnews_contexts,\n"
            "                'sportlogic': sportlogic_contexts,\n"
            "            }\n",
        )

    # Source stats so the detailed report shows real SportLogic work.
    if "'sportlogic': sportlogic_stats" not in text:
        text, _ = _replace_once(
            text,
            "                'allsportsapi': allsportsapi_stats,\n                'futrixmetrics': futrixmetrics_stats,\n",
            "                'allsportsapi': allsportsapi_stats,\n"
            "                'sportlogic': sportlogic_stats,\n"
            "                'futrixmetrics': futrixmetrics_stats,\n",
        )
    if "'sportlogic_context': sportlogic_context_stats" not in text:
        text, _ = _replace_once(
            text,
            "                'gnews': gnews_stats,\n                'weather': weather_stats,\n",
            "                'gnews': gnews_stats,\n"
            "                'sportlogic_context': sportlogic_context_stats,\n"
            "                'weather': weather_stats,\n",
        )
    return text


def _patch_detailed_report_text(text: str) -> str:
    # Human reports should not list api-football after it was removed.
    text = text.replace('            "api_football",\n', '')
    text = text.replace('        "api_football",\n', '')
    if '"sportlogic",' not in text:
        text = text.replace('            "odds_api_io",\n', '            "odds_api_io",\n            "sportlogic",\n')
        text = text.replace('        "odds_api_io",\n', '        "odds_api_io",\n        "sportlogic",\n')

    old_fn = '''def day_inventory_summary() -> dict[str, Any]:
    return load_json(".data/exports/latest-day-inventory-summary.json", {})
'''
    new_fn = '''def day_inventory_summary() -> dict[str, Any]:
    summary = load_json(".data/exports/latest-day-inventory-summary.json", {})
    if not isinstance(summary, dict):
        summary = {}
    counts = summary.setdefault("counts", {})
    if not isinstance(counts, dict):
        counts = {}
        summary["counts"] = counts

    merge = load_json(".data/exports/latest-day-inventory-coverage-merge.json", {})
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

    runtime_total = as_int(runtime_counts.get("matches_seen"))
    runtime_odds = as_int(runtime_counts.get("matches_with_odds"))
    runtime_context = as_int(runtime_counts.get("matches_with_context"))
    runtime_ready = as_int(runtime_counts.get("matches_ready_for_model"))
    if runtime_total:
        counts["matches_total"] = max(as_int(counts.get("matches_total")), runtime_total)
    if runtime_odds:
        counts["matches_with_odds"] = max(as_int(counts.get("matches_with_odds")), runtime_odds)
    if runtime_context:
        counts["matches_with_context"] = max(as_int(counts.get("matches_with_context")), runtime_context)
    if runtime_ready:
        counts["matches_ready_for_model"] = max(as_int(counts.get("matches_ready_for_model")), runtime_ready)
    summary["runtime_counts_applied"] = bool(runtime_counts)
    return summary
'''
    if old_fn in text:
        text = text.replace(old_fn, new_fn, 1)
    return text


def _patch_merge_inventory_text(text: str) -> str:
    if "SUMMARY_PATH" not in text:
        text = text.replace(
            "EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-day-inventory-coverage-merge.json'\n",
            "EXPORT_PATH = ROOT / '.data' / 'exports' / 'latest-day-inventory-coverage-merge.json'\n"
            "SUMMARY_PATH = ROOT / '.data' / 'exports' / 'latest-day-inventory-summary.json'\n",
            1,
        )
    if "write_json(SUMMARY_PATH, inventory_summary)" not in text:
        marker = "    write_json(EXPORT_PATH, summary)\n"
        inject = '''    inventory_summary = {
        'bootstrap_provider': 'runtime_coverage_merge',
        'build_status': 'ok',
        'date_local': target_date,
        'matches_for_day': counts['matches_total'],
        'matches_total_raw': counts['matches_total'],
        'counts': counts,
        'runtime_counts': runtime_counts,
        'updated_at_utc': now_utc.isoformat(),
        'saved_paths': {
            'date_path': str(inventory_path),
            'latest_path': '.data/day_inventory/latest.json',
            'current_path': '.data/day_inventory/current.json',
            'today_path': '.data/day_inventory/today.json',
            'summary_path': str(SUMMARY_PATH),
        },
    }
    write_json(SUMMARY_PATH, inventory_summary)
'''
        if marker in text:
            text = text.replace(marker, inject + marker, 1)
    return text


def _patch_workflow_text(text: str) -> str:
    # Expose SportLogic key/flags to GitHub Actions.  Without this line GitHub
    # secrets remain unavailable to the Python process.
    if "SPORTLOGIC_API_KEY:" not in text:
        text = text.replace(
            "      SPORTSBOOK_RAPIDAPI_KEY: ${{ secrets.SPORTSBOOK_RAPIDAPI_KEY }}\n",
            "      SPORTSBOOK_RAPIDAPI_KEY: ${{ secrets.SPORTSBOOK_RAPIDAPI_KEY }}\n"
            "      SPORTLOGIC_API_KEY: ${{ secrets.SPORTLOGIC_API_KEY }}\n"
            "      ENABLE_SPORTLOGIC: \"true\"\n"
            "      SPORTLOGIC_ENABLED: \"true\"\n"
            "      SPORTLOGIC_PER_RUN_MAX: \"80\"\n"
            "      SPORTLOGIC_MATCH_LIMIT: \"80\"\n"
            "      SPORTLOGIC_ODDS_MATCH_LIMIT: \"40\"\n",
            1,
        )
    return text


def _apply_file_patches() -> None:
    _patch_file(ROOT / "app" / "services" / "runner.py", _patch_runner_text)
    _patch_file(ROOT / "scripts" / "build_detailed_run_report.py", _patch_detailed_report_text)
    _patch_file(ROOT / "scripts" / "merge_run_coverage_into_day_inventory.py", _patch_merge_inventory_text)
    _patch_file(ROOT / ".github" / "workflows" / "run-bot.yml", _patch_workflow_text)


def _apply_import_patches() -> None:
    utils_mod = sys.modules.get("app.utils")
    if utils_mod is not None:
        try:
            utils_mod.normalize_probability_percent = _normalize_probability_percent_patched
        except Exception:
            pass
    provider_mod = sys.modules.get("app.providers.api_football")
    if provider_mod is not None:
        try:
            provider_mod.normalize_probability_percent = _normalize_probability_percent_patched
        except Exception:
            pass


_original_import = builtins.__import__


def _patched_import(name, globals=None, locals=None, fromlist=(), level=0):
    module = _original_import(name, globals, locals, fromlist, level)
    if name == "app.utils" or name.startswith("app.utils.") or name == "app.providers.api_football":
        _apply_import_patches()
    elif name.startswith("app.providers") and "api_football" in name:
        _apply_import_patches()
    return module


try:
    _apply_file_patches()
except Exception:
    pass

builtins.__import__ = _patched_import
_apply_import_patches()
