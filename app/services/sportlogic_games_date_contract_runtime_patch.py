from __future__ import annotations

"""Patch SportLogic /games query contract discovered by diagnostics.

The v6 contract probe proved that the documented-looking
`date_from/date_to/status=scheduled` shape returns HTTP 200 with an empty data
array, while `/games?date=YYYY-MM-DD&status=scheduled&per_page=100` returns
current rows.  This patch changes only the fixture query parameter order used by
SportLogicProvider; it does not relax publication, value, xG, timing, or
price-integrity guards.
"""

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
REPORT = Path(".data/exports/latest-sportlogic-games-date-contract-patch.json")
_INSTALLED = False


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).strip()))
    except Exception:
        return default


def _truthy(value: Any, default: bool = True) -> bool:
    if value in (None, ""):
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "force"}


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _date_first_game_query_params(self: Any, date_key: str) -> list[dict[str, Any]]:
    try:
        day = datetime.fromisoformat(str(date_key)).date()
        next_day = (datetime.combine(day, datetime.min.time(), tzinfo=UTC) + timedelta(days=1)).date().isoformat()
    except Exception:
        next_day = str(date_key)
    per_page = max(5, min(100, _as_int(os.getenv("SPORTLOGIC_GAMES_PER_PAGE") or os.getenv("SPORTLOGIC_PER_PAGE"), 100)))

    # Proven by latest-sportlogic-api-diagnostic.json: this is the first variant
    # that returned non-empty rows from api.sportlogic.io/api/v1/games.
    variants: list[dict[str, Any]] = [
        {"date": str(date_key), "status": "scheduled", "per_page": per_page},
        {"date": str(date_key), "per_page": per_page},
    ]

    # Keep the older shapes as fallbacks for non-standard deployments, but they
    # should no longer consume the first useful requests.
    variants.extend([
        {"date_from": str(date_key), "date_to": next_day, "status": "scheduled", "per_page": per_page},
        {"date_from": str(date_key), "date_to": next_day, "per_page": per_page},
    ])
    if _truthy(os.getenv("SPORTLOGIC_GAMES_DATE_FROM_ONLY_FALLBACK", "true"), True):
        variants.append({"date_from": str(date_key), "status": "scheduled", "per_page": per_page})
    return variants


def install() -> None:
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True
    os.environ.setdefault("SPORTLOGIC_GAMES_QUERY_CONTRACT", "date_param_first")
    os.environ.setdefault("SPORTLOGIC_CONTRACT_PROBE_AUTH_MODES", "x-api-key")
    os.environ.setdefault("SPORTLOGIC_CONTRACT_PROBE_MAX_ATTEMPTS", "8")
    try:
        from app.providers.sportlogic_provider import SportLogicProvider
        SportLogicProvider._game_query_params = _date_first_game_query_params
        SportLogicProvider._harizon_date_contract_patched = True
        status = "installed"
    except Exception as exc:
        status = "install_failed"
        _write({
            "created_at_utc": datetime.now(UTC).isoformat(),
            "status": status,
            "error": str(exc)[:300],
            "policy": "sportlogic_games_date_param_first",
        })
        return
    _write({
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": status,
        "policy": "sportlogic_games_date_param_first",
        "discovered_from": "latest-sportlogic-api-diagnostic rows_total>0 for /games?date=YYYY-MM-DD&status=scheduled",
        "publication_safety": {
            "price_integrity_guard": "unchanged",
            "line_movement_guard": "unchanged",
            "timing_guard": "unchanged",
            "xg_quality_value_guards": "unchanged",
        },
    })
