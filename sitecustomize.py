"""Repository-wide startup hook for Harizon sports-bot.

Python imports this module automatically before app code when the repository
root is on sys.path.  It applies per-run-only API limits, centralized runtime
patches, and keeps SportLogic runner wiring that must happen before app.cli is
imported.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _replace_once(text: str, old: str, new: str) -> str:
    return text.replace(old, new, 1) if old in text else text


def _patch_runner_sportlogic() -> None:
    path = ROOT / "app" / "services" / "runner.py"
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return
    original = text

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
        text = _replace_once(
            text,
            "                'allsportsapi': allsportsapi_offers,\n            }\n",
            "                'allsportsapi': allsportsapi_offers,\n"
            "                'sportlogic': sportlogic_offers,\n"
            "            }\n",
        )

    if "provider_targets['sportlogic']" not in text:
        text = _replace_once(
            text,
            "                'gnews': self._select_provider_context_matches(context_target_matches, 'gnews', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n            }\n",
            "                'gnews': self._select_provider_context_matches(context_target_matches, 'gnews', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n"
            "                'sportlogic': self._select_provider_context_matches(context_target_matches, 'sportlogic', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n"
            "            }\n",
        )

    if "sportlogic_contexts" not in text:
        text = _replace_once(
            text,
            "                (gnews_contexts, gnews_stats, gnews_preview),\n            ) = await asyncio.gather(\n",
            "                (gnews_contexts, gnews_stats, gnews_preview),\n"
            "                (sportlogic_contexts, sportlogic_context_stats, sportlogic_context_preview),\n"
            "            ) = await asyncio.gather(\n",
        )
        text = _replace_once(
            text,
            "                self._fetch_provider(self.gnews, 'fetch_context', provider_targets['gnews'], empty_data={}),\n            )\n",
            "                self._fetch_provider(self.gnews, 'fetch_context', provider_targets['gnews'], empty_data={}),\n"
            "                self._fetch_provider(self.sportlogic, 'fetch_context', provider_targets['sportlogic'], empty_data={}),\n"
            "            )\n",
        )
        text = _replace_once(
            text,
            "                'gnews': gnews_contexts,\n            }\n",
            "                'gnews': gnews_contexts,\n"
            "                'sportlogic': sportlogic_contexts,\n"
            "            }\n",
        )

    if "'sportlogic': sportlogic_stats" not in text:
        text = _replace_once(
            text,
            "                'allsportsapi': allsportsapi_stats,\n                'futrixmetrics': futrixmetrics_stats,\n",
            "                'allsportsapi': allsportsapi_stats,\n"
            "                'sportlogic': sportlogic_stats,\n"
            "                'futrixmetrics': futrixmetrics_stats,\n",
        )
    if "'sportlogic_context': sportlogic_context_stats" not in text:
        text = _replace_once(
            text,
            "                'gnews': gnews_stats,\n                'weather': weather_stats,\n",
            "                'gnews': gnews_stats,\n"
            "                'sportlogic_context': sportlogic_context_stats,\n"
            "                'weather': weather_stats,\n",
        )

    if text != original:
        try:
            path.write_text(text, encoding="utf-8")
        except Exception:
            pass


try:
    from scripts import api_max_usage_patch

    api_max_usage_patch.apply_api_max_usage_patch()
    _patch_runner_sportlogic()
    from scripts.runtime_startup_patches import apply_all, install_import_hook

    apply_all()
    install_import_hook()
except Exception:
    # Startup hooks must never break bot execution.
    pass
