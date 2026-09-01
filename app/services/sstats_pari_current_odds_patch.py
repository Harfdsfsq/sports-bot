from __future__ import annotations

from typing import Any

_INSTALLED = False


def _unwrap(payload: Any) -> Any:
    current = payload
    for _ in range(5):
        if not isinstance(current, dict):
            break
        nested = next(
            (
                current.get(key)
                for key in ("data", "result", "response")
                if isinstance(current.get(key), dict)
            ),
            None,
        )
        if nested is None:
            break
        current = nested
    return current


def _flatten(rows: list[Any]) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for item in rows:
        if not isinstance(item, dict):
            continue
        nested = item.get("odds") or item.get("outcomes")
        if not isinstance(nested, list):
            flattened.append(item)
            continue
        market_name = str(item.get("marketName") or item.get("name") or "").strip()
        for outcome in nested:
            if not isinstance(outcome, dict):
                continue
            row = dict(outcome)
            outcome_name = str(row.get("name") or row.get("label") or "").strip()
            if market_name and outcome_name and market_name.lower() not in outcome_name.lower():
                row["name"] = f"{market_name}: {outcome_name}"
            flattened.append(row)
    return flattened


def extract_current_odds(payload: Any) -> list[dict[str, Any]]:
    """Return prematch market rows without depending on monkey-patch order."""
    data = _unwrap(payload)
    if not isinstance(data, dict):
        return []
    containers = [data]
    match_info = data.get("matchInfo") or data.get("match_info")
    if isinstance(match_info, dict):
        containers.append(match_info)
    for container in containers:
        for key in (
            "currentOdds",
            "current_odds",
            "prematchOdds",
            "preMatchOdds",
            "odds",
            "Odds",
            "coefficients",
            "outcomes",
            "markets",
        ):
            value = container.get(key)
            if isinstance(value, list):
                return _flatten(value)
    return []


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    from app.providers import sstats_pari_odds, sstats_pari_parser
    from app.services import sstats_pari_runtime_repair
    from app.services.sstats_pari_rate_limit_patch import install as install_rate_limit

    # sstats_pari_runtime_repair replaces parser.extract_odds during its own install.
    # Patch every live reference with this self-contained extractor afterwards.
    sstats_pari_parser.extract_odds = extract_current_odds
    sstats_pari_odds.extract_odds = extract_current_odds
    sstats_pari_runtime_repair._extract_odds = extract_current_odds
    rate_limit = install_rate_limit()
    _INSTALLED = True
    return {
        "status": "installed",
        "runtime_extractor": "self_contained_current_odds",
        "current_odds_supported": True,
        "nested_market_rows_supported": True,
        "patches_runtime_repair_reference": True,
        "rate_limit": rate_limit,
        "publication_contract_relaxed": False,
    }


__all__ = ["extract_current_odds", "install"]
