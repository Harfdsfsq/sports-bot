from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import Settings
from app.services.day_inventory import DayInventoryStore
from app.services.runner import PredictionRunner

UTC = timezone.utc
ENV_BOOTSTRAP_KEY = 'MATCH_BOOTSTRAP_PROVIDER'


def app_tz(settings: Settings):
    try:
        return ZoneInfo(str(getattr(settings, 'app_timezone', '') or 'Europe/Moscow'))
    except Exception:
        return UTC


def target_local_date(settings: Settings) -> str:
    explicit = str(os.getenv('DAY_INVENTORY_TARGET_DATE') or '').strip()
    if explicit:
        return explicit
    return datetime.now(UTC).astimezone(app_tz(settings)).date().isoformat()


def maybe_override_bootstrap_provider(settings: Settings) -> tuple[str | None, str | None, str | None]:
    original_setting = str(getattr(settings, 'match_bootstrap_provider', '') or '').strip() or None
    original_env = str(os.getenv(ENV_BOOTSTRAP_KEY, '') or '').strip() or None
    override = str(os.getenv('DAY_INVENTORY_BOOTSTRAP_PROVIDER') or '').strip() or None
    if override:
        if hasattr(settings, 'match_bootstrap_provider'):
            setattr(settings, 'match_bootstrap_provider', override)
        os.environ[ENV_BOOTSTRAP_KEY] = override
    return original_setting, original_env, override


def restore_bootstrap_provider(settings: Settings, original_setting: str | None, original_env: str | None) -> None:
    if hasattr(settings, 'match_bootstrap_provider'):
        setattr(settings, 'match_bootstrap_provider', original_setting or '')
    if original_env:
        os.environ[ENV_BOOTSTRAP_KEY] = original_env
    else:
        os.environ.pop(ENV_BOOTSTRAP_KEY, None)


async def fetch_inventory_matches(runner: PredictionRunner) -> tuple[list, dict]:
    bootstrap_matches, bootstrap_meta = await runner._fetch_matches()  # noqa: SLF001
    deduped_matches = runner._dedupe_matches(bootstrap_matches)  # noqa: SLF001
    return deduped_matches, bootstrap_meta


async def main_async() -> int:
    settings = Settings()
    store = DayInventoryStore(timezone_name=str(getattr(settings, 'app_timezone', 'Europe/Moscow') or 'Europe/Moscow'))
    local_date = target_local_date(settings)
    source_meta: dict[str, object] = {}

    original_setting, original_env, override_provider = maybe_override_bootstrap_provider(settings)
    runner = PredictionRunner(settings)
    matches: list = []

    try:
        try:
            matches, bootstrap_meta = await fetch_inventory_matches(runner)
            source_meta['primary_provider'] = str((bootstrap_meta or {}).get('provider') or getattr(settings, 'match_bootstrap_provider', '') or '')
            source_meta['requested_bootstrap_provider'] = override_provider or original_setting
            source_meta['attempts'] = dict((bootstrap_meta or {}).get('attempts') or {})
            source_meta['stats'] = dict((bootstrap_meta or {}).get('stats') or {})
            source_meta['preview'] = dict((bootstrap_meta or {}).get('preview') or {})
        except Exception as exc:
            if override_provider and original_setting:
                restore_bootstrap_provider(settings, original_setting, original_env)
                runner = PredictionRunner(settings)
                matches, bootstrap_meta = await fetch_inventory_matches(runner)
                source_meta['primary_provider'] = str((bootstrap_meta or {}).get('provider') or getattr(settings, 'match_bootstrap_provider', '') or '')
                source_meta['requested_bootstrap_provider'] = override_provider
                source_meta['fallback_from'] = override_provider
                source_meta['fallback_reason'] = f'{type(exc).__name__}: {exc}'
                source_meta['attempts'] = dict((bootstrap_meta or {}).get('attempts') or {})
                source_meta['stats'] = dict((bootstrap_meta or {}).get('stats') or {})
                source_meta['preview'] = dict((bootstrap_meta or {}).get('preview') or {})
            else:
                raise

        matches_for_day = [
            match
            for match in matches
            if store.local_date_for_dt(match.commence_time) == local_date
        ]
        existing = store.load_inventory(local_date)
        payload = store.build_payload(
            local_date=local_date,
            matches=matches_for_day,
            source_meta=source_meta,
            existing=existing,
        )
        paths = store.save_inventory(payload)

        result = {
            'date_local': local_date,
            'build_status': 'ok',
            'bootstrap_provider': source_meta.get('primary_provider'),
            'requested_bootstrap_provider': source_meta.get('requested_bootstrap_provider'),
            'matches_total_raw': len(matches),
            'matches_for_day': len(matches_for_day),
            'saved_paths': paths,
            'counts': dict(payload.get('counts') or {}),
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except Exception as exc:
        summary_path = store.save_failure_summary(
            local_date=local_date,
            error_text=f'{type(exc).__name__}: {exc}',
            source_meta=source_meta,
            bootstrap_provider=str(source_meta.get('primary_provider') or getattr(settings, 'match_bootstrap_provider', '') or ''),
        )
        result = {
            'date_local': local_date,
            'build_status': 'error',
            'error': f'{type(exc).__name__}: {exc}',
            'summary_path': summary_path,
            'source_meta': source_meta,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 1
    finally:
        restore_bootstrap_provider(settings, original_setting, original_env)


def main() -> int:
    return asyncio.run(main_async())


if __name__ == '__main__':
    raise SystemExit(main())
