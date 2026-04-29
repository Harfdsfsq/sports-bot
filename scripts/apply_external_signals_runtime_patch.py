from __future__ import annotations

from pathlib import Path

ROOT = Path('.').resolve()
TARGET = ROOT / 'app' / 'services' / 'runner.py'
PATCH_VERSION = 'v1-external-signals-context'


def main() -> int:
    text = TARGET.read_text(encoding='utf-8')
    if 'self.external_signals' in text:
        print(f'{PATCH_VERSION}: already applied')
        return 0
    changed = False

    marker = "        self.gnews = self._safe_provider('app.providers.gnews', 'GNewsContextProvider')\n"
    insert = marker + "        self.external_signals = self._safe_provider('app.providers.external_signals', 'ExternalSignalsContextProvider')\n"
    if marker in text:
        text = text.replace(marker, insert, 1)
        changed = True

    marker = "            'gnews': self.gnews,\n            'odds_api_io': self.odds_api_io,"
    insert = "            'gnews': self.gnews,\n            'external_signals': self.external_signals,\n            'odds_api_io': self.odds_api_io,"
    if marker in text:
        text = text.replace(marker, insert, 1)
        changed = True

    marker = "        if provider_name == 'gnews':\n            return bool(getattr(self.settings, 'enable_gnews_context', default))\n        return bool(default)"
    insert = "        if provider_name == 'gnews':\n            return bool(getattr(self.settings, 'enable_gnews_context', default))\n        if provider_name == 'external_signals':\n            return str(os.getenv('ENABLE_EXTERNAL_SIGNALS', 'true')).strip().lower() in {'1', 'true', 'yes', 'on'}\n        return bool(default)"
    if marker in text:
        text = text.replace(marker, insert, 1)
        changed = True

    marker = "        if module_name.endswith('gnews') and not self._provider_enabled('gnews', default=True):\n            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')\n            return None\n        try:"
    insert = "        if module_name.endswith('gnews') and not self._provider_enabled('gnews', default=True):\n            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')\n            return None\n        if module_name.endswith('external_signals') and not self._provider_enabled('external_signals', default=True):\n            self._mark_provider_status(provider_name, enabled=False, loaded=False, reason='disabled_by_config')\n            return None\n        try:"
    if marker in text:
        text = text.replace(marker, insert, 1)
        changed = True

    marker = "                'gnews': self._select_provider_context_matches(context_target_matches, 'gnews', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n            }"
    insert = "                'gnews': self._select_provider_context_matches(context_target_matches, 'gnews', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n                'external_signals': self._select_provider_context_matches(context_target_matches, 'external_signals', fallback_matches=filtered_matches, offers_by_match=merged_offers),\n            }"
    if marker in text:
        text = text.replace(marker, insert, 1)
        changed = True

    marker = "                (gnews_contexts, gnews_stats, gnews_preview),\n            ) = await asyncio.gather("
    insert = "                (gnews_contexts, gnews_stats, gnews_preview),\n                (external_signals_contexts, external_signals_stats, external_signals_preview),\n            ) = await asyncio.gather("
    if marker in text:
        text = text.replace(marker, insert, 1)
        changed = True

    marker = "                self._fetch_provider(self.gnews, 'fetch_context', provider_targets['gnews'], empty_data={}),\n            )"
    insert = "                self._fetch_provider(self.gnews, 'fetch_context', provider_targets['gnews'], empty_data={}),\n                self._fetch_provider(self.external_signals, 'fetch_context', provider_targets['external_signals'], empty_data={}),\n            )"
    if marker in text:
        text = text.replace(marker, insert, 1)
        changed = True

    marker = "                'gnews': gnews_contexts,\n            }"
    insert = "                'gnews': gnews_contexts,\n                'external_signals': external_signals_contexts,\n            }"
    if marker in text:
        text = text.replace(marker, insert, 1)
        changed = True

    marker = "                'gnews': gnews_stats,\n                'weather': weather_stats,"
    insert = "                'gnews': gnews_stats,\n                'external_signals': external_signals_stats,\n                'weather': weather_stats,"
    if marker in text:
        text = text.replace(marker, insert, 1)
        changed = True

    if changed:
        if 'import os\n' not in text:
            text = text.replace('import json\n', 'import json\nimport os\n', 1)
        TARGET.write_text(text, encoding='utf-8')
        print(f'{PATCH_VERSION}: patched {TARGET}')
    else:
        print(f'{PATCH_VERSION}: no changes; target markers not found')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
