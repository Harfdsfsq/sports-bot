from __future__ import annotations

import asyncio
import json
import os
import sys
from typing import Any

from app.config import get_settings
from app.services.runner import PredictionRunner


def _parse_bool(value: str) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _parse_int(value: str) -> int:
    return int(float(str(value).strip()))


def _apply_runtime_env_overrides(settings: Any) -> Any:
    """
    Runtime safety net for schedule-specific env vars from GitHub Actions.

    This makes the CLI respect the workflow contract:
    - forecasts at 08:00 / 12:00 / 16:00 / 20:00 MSK
    - daily report at 22:00 MSK
    """

    overrides: list[tuple[str, str, Any]] = [
        ("prediction_publication_enabled", "PREDICTION_PUBLICATION_ENABLED", _parse_bool),
        ("run_report_enabled", "RUN_REPORT_ENABLED", _parse_bool),
        ("daily_report_enabled", "DAILY_REPORT_ENABLED", _parse_bool),
        ("daily_report_send_telegram", "DAILY_REPORT_SEND_TELEGRAM", _parse_bool),
        ("daily_report_hour_local", "DAILY_REPORT_HOUR_LOCAL", _parse_int),
        ("daily_report_target_offset_days", "DAILY_REPORT_TARGET_OFFSET_DAYS", _parse_int),
        ("daily_report_min_bets", "DAILY_REPORT_MIN_BETS", _parse_int),
        ("daily_report_resend_on_change", "DAILY_REPORT_RESEND_ON_CHANGE", _parse_bool),
    ]

    for attr_name, env_name, parser in overrides:
        raw = os.getenv(env_name)
        if raw is None or str(raw).strip() == "":
            continue
        try:
            value = parser(raw)
        except Exception:
            continue
        object.__setattr__(settings, attr_name, value)

    return settings


async def _main() -> int:
    settings = _apply_runtime_env_overrides(get_settings())
    runner = PredictionRunner(settings)
    if len(sys.argv) >= 2 and sys.argv[1] == "run-once":
        summary = await runner.run_once()
        print(json.dumps(summary, ensure_ascii=False, indent=2))
        return 0
    print("Usage: python -m app.cli run-once")
    return 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_main()))
