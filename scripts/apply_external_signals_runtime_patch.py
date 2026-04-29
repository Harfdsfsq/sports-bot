from __future__ import annotations

from pathlib import Path

ROOT = Path('.').resolve()
TARGET = ROOT / 'app' / 'services' / 'runner.py'
PATCH_VERSION = 'v3-external-signals-context-no-os-dependency'


def replace_once(text: str, old: str, new: str) -> tuple[str, bool]:
    if old not in text:
        return text, False
    return text.replace(old, new, 1), True


def main() -> int:
    text = TARGET.read_text(encoding='utf-8')
    changed = False

    # Remove fragile os.getenv usage from runner.py. The provider itself reads env.
    old_line = "        if provider_name == 'external_signals':\n            return str(os.getenv('ENABLE_EXTERNAL_SIGNALS', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'}\n        return bool(default)"
    new_line = "        if provider_name == 'external_signals':\n            return bool(getattr(self.settings, 'enable_external_signals', default))\n        return bool(default)"
    if old_line in text:
        text = text.replace(old_line, new_line, 1)
        changed = True

    if 'self.external_signals' not in text:
        text, did = replace_once(
            text,
            "        self.gnews = self._safe_provider('app.providers.gnews', 'GNewsContextProvider')\n",
            "        self.gnews = self._safe_provider('app.providers.gnews', 'GNewsContextProvider')\n        self.external_signals = self._safe_provider('app.providers.external_signals', 'ExternalSignalsContextProvider')\n",
        )
        changed = changed or did

    if "'external_signals': self.external_signals" not in text:
        text, did = replace_once(
            text,
            "            'gnews': self.gnews,\n            'odds_api_io': self.odds_api_io,",
            "            'gnews': self.gnews,\n            'external_signals': self.external_signals,\n            'odds_api_io': self.odds_api_io,",
        )
        changed = changed or did

    if "if provider_name == 'external_signals'" not in text:
        text, did = replace_once(
            text,
            "        if provider_name == 'gnews':\n            return bool(getattr(self.settings, 'enable_gnews_context', default))\n        return bool(default)",
            "        if provider_name == 'gnews':\n            return bool(getattr(self.settings, 'enable_gnews_context', default))\n        if provider_name == 'external_signals':\n            return bool(getattr(self.settings, 'enable_external_signals', default))\n        return bool(default)",
        )
        changed = changed or did

    if "module_name.endswith('external_signals')" not in text:
        text, did = replace_once(
            text,
            "        if module_name.endswith('gnews') and not self._provider_enabled('gnews', default=True):\n            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')\n            return None\n        try:",
            "        if module_name.endswith('gnews') and not self._provider_enabled('gnews', default=True):\n            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')\n            return None\n        if module_name.endswith('external_signals') and not self._provider_enabled('external_signals', default=True):\n            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')\n            return None\n        try:",
        )
        changed = changed or did

    if "provider_targets['external_signals']" not in text:
        text, did = replace_once(
            text,
            "                'gnews': self._select_provider_context_matches(context_target_matches, 'gnews', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n            }",
            "                'gnews': self._select_provider_context_matches(context_target_matches, 'gnews', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n                'external_signals': self._select_provider_context_matches(context_target_matches, 'external_signals', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n            }",
        )
        changed = changed or did

    if 'external_signals_contexts' not in text:
        text, did = replace_once(
            text,
            "                (gnews_contexts, gnews_stats, gnews_preview),\n            ) = await asyncio.gather(",
            "                (gnews_contexts, gnews_stats, gnews_preview),\n                (external_signals_contexts, external_signals_stats, external_signals_preview),\n            ) = await asyncio.gather(",
        )
        changed = changed or did
        text, did = replace_once(
            text,
            "                self._fetch_provider(self.gnews, 'fetch_context', provider_targets['gnews'], empty_data={}),\n            )",
            "                self._fetch_provider(self.gnews, 'fetch_context', provider_targets['gnews'], empty_data={}),\n                self._fetch_provider(self.external_signals, 'fetch_context', provider_targets['external_signals'], empty_data={}),\n            )",
        )
        changed = changed or did
        text, did = replace_once(
            text,
            "                'gnews': gnews_contexts,\n            }",
            "                'gnews': gnews_contexts,\n                'external_signals': external_signals_contexts,\n            }",
        )
        changed = changed or did
        text, did = replace_once(
            text,
            "                'gnews': gnews_stats,\n                'weather': weather_stats,",
            "                'gnews': gnews_stats,\n                'external_signals': external_signals_stats,\n                'weather': weather_stats,",
        )
        changed = changed or did

    TARGET.write_text(text, encoding='utf-8')
    print(f'{PATCH_VERSION}: patched={changed} target={TARGET}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
