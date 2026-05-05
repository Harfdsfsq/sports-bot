from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import provider_smoke_all_v2 as smoke

# Wikimedia blocks generic bot traffic unless the User-Agent is clearly
# identifiable.  Keep this centralized for both Wikidata REST and SPARQL probes.
smoke.USER_AGENT = "HARIZON-sports-bot-provider-smoke/2.1 (https://github.com/Harfdsfsq/sports-bot; diagnostics)"

_original_status = smoke._status


def _status_v2(http_status: int | None, rows_count: int, error: str | None, key_present: bool, body: str) -> tuple[str, str]:
    body_low = str(body or "").lower()
    if http_status == 403 and ("robot policy" in body_low or "w.wiki/4wjs" in body_low or "bot-traffic" in body_low):
        return "BOT_POLICY", "http 403; provider robot policy / User-Agent / request-volume restriction"
    return _original_status(http_status, rows_count, error, key_present, body)


smoke._status = _status_v2

from scripts import provider_smoke_diagnostics as diagnostics  # noqa: E402

diagnostics.FIXABLE_WARNING_STATUSES.add("BOT_POLICY")

_original_weakness = diagnostics._weakness_for
_original_recommendation = diagnostics._recommendation_for


def _weakness_for_v2(row: dict[str, Any]) -> str:
    if str(row.get("status") or "") == "BOT_POLICY":
        return "provider_robot_policy_block"
    return _original_weakness(row)


def _recommendation_for_v2(row: dict[str, Any]) -> str:
    if str(row.get("status") or "") == "BOT_POLICY":
        return "Указать строгий User-Agent/From, снизить частоту, включить кэш; для массового маппинга использовать dumps/local cache."
    return _original_recommendation(row)


diagnostics._weakness_for = _weakness_for_v2
diagnostics._recommendation_for = _recommendation_for_v2

main = diagnostics.main


if __name__ == "__main__":
    raise SystemExit(main())
