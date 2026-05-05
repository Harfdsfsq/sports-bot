from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provider_smoke_all_v4 as smoke  # noqa: E402
from scripts import provider_smoke_diagnostics as diagnostics  # noqa: E402

diagnostics.smoke = smoke
diagnostics.FIXABLE_WARNING_STATUSES.add("BOT_POLICY")

_original_weakness = diagnostics._weakness_for
_original_recommendation = diagnostics._recommendation_for


def _weakness_for(row):
    if str(row.get("status") or "") == "BOT_POLICY":
        return "provider_robot_policy_block"
    return _original_weakness(row)


def _recommendation_for(row):
    if str(row.get("status") or "") == "BOT_POLICY":
        return "Указать строгий User-Agent/From, снизить частоту, включить кэш; для массового маппинга использовать dumps/local cache."
    provider = str(row.get("provider") or "")
    if provider in {"oddsfeed_rapidapi", "sportsbook_rapidapi"} and str(row.get("status") or "") == "MISSING_CONFIG":
        return "Не гонять в all до выбора endpoint. Запусти manual discovery и потом пропиши *_RAPIDAPI_PATH."
    return _original_recommendation(row)


diagnostics._weakness_for = _weakness_for
diagnostics._recommendation_for = _recommendation_for

main = diagnostics.main


if __name__ == "__main__":
    raise SystemExit(main())
