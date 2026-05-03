from __future__ import annotations

"""Official SharpAPI REST defaults.

SharpAPI docs expose REST odds at https://api.sharpapi.io/api/v1/odds with
X-API-Key auth. This patch keeps the schema-tolerant provider but points it at
the documented endpoint unless the operator explicitly overrides endpoints.
"""

import os
from datetime import datetime, timezone
from typing import Any

PATCH_MARKER = "_harizon_sharpapi_official_api_patch_v1"
UTC = timezone.utc


def _api_key() -> str:
    return str(
        os.getenv("SHARPAPI_API_KEY")
        or os.getenv("SHARPAPI_KEY")
        or os.getenv("SHARP_API_KEY")
        or ""
    ).strip()


def install() -> bool:
    try:
        from app.providers.sharpapi import SharpApiOddsProvider
    except Exception:
        return False
    if getattr(SharpApiOddsProvider, PATCH_MARKER, False):
        return False

    original_init = SharpApiOddsProvider.__init__

    def init_patched(self: Any, settings: Any) -> None:
        original_init(self, settings)
        self.base_url = str(os.getenv("SHARPAPI_BASE_URL") or "https://api.sharpapi.io").rstrip("/")
        self.api_key = _api_key()
        self.rapidapi_host = ""
        self.official_api = True

    def headers_patched(self: Any) -> dict[str, str]:
        headers = {"Accept": "application/json"}
        if str(getattr(self, "api_key", "") or "").strip():
            headers["X-API-Key"] = str(self.api_key).strip()
            headers["Authorization"] = f"Bearer {str(self.api_key).strip()}"
        return headers

    def candidate_endpoints_patched(self: Any, now: datetime, until: datetime) -> list[tuple[str, dict[str, Any]]]:
        custom = [item.strip() for item in str(os.getenv("SHARPAPI_ODDS_ENDPOINTS") or "").split(",") if item.strip()]
        endpoints = custom or ["/api/v1/odds"]
        # SharpAPI docs show league-centric requests, e.g. ?league=NBA. For soccer,
        # allow env override and default to Soccer while still sending date filters
        # that harmlessly help if the account supports them.
        league = str(os.getenv("SHARPAPI_LEAGUE") or os.getenv("SHARPAPI_DEFAULT_LEAGUE") or "Soccer").strip()
        params: dict[str, Any] = {
            "league": league,
            "sport": "soccer",
            "from": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "to": until.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
            "dateFrom": now.date().isoformat(),
            "dateTo": until.date().isoformat(),
            "limit": str(getattr(self, "match_limit", 80) or 80),
        }
        return [(endpoint if endpoint.startswith("/") else f"/{endpoint}", params) for endpoint in endpoints]

    SharpApiOddsProvider.__init__ = init_patched
    SharpApiOddsProvider._headers = headers_patched
    SharpApiOddsProvider._candidate_endpoints = candidate_endpoints_patched
    setattr(SharpApiOddsProvider, PATCH_MARKER, True)
    return True
