from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _to_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        return float(value)
    except Exception:
        return None


def _clamp_probability(value: float | None) -> float | None:
    if value is None:
        return None
    if value < 0.0 or value > 1.0:
        return None
    return value


def _first_float(*values: Any) -> float | None:
    for value in values:
        parsed = _to_float(value)
        if parsed is not None:
            return parsed
    return None


def _prob_from_odds(odds: float | None) -> float | None:
    if odds is None or odds <= 1.0:
        return None
    return 1.0 / odds


def _odds_from_prob(prob: float | None) -> float | None:
    prob = _clamp_probability(prob)
    if prob is None or prob <= 0.0:
        return None
    return 1.0 / prob


def _mismatch(a: float | None, b: float | None) -> float | None:
    if a is None or b is None:
        return None
    return abs(a - b)


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def canonicalize_candidate(candidate: dict[str, Any]) -> dict[str, Any]:
    source_summary = dict(candidate.get("source_summary") or {})
    diagnostics = dict(candidate.get("diagnostics") or {})

    selected_odds = _first_float(
        source_summary.get("selected_price"),
        candidate.get("odds"),
    )
    selected_implied_probability = _prob_from_odds(selected_odds)

    market_probability = _clamp_probability(_first_float(
        candidate.get("market_probability"),
        candidate.get("consensus_probability"),
        candidate.get("implied_probability"),
    ))
    fair_odds_from_market = _odds_from_prob(market_probability)

    canonical_adjusted_probability = _clamp_probability(_first_float(
        candidate.get("adjusted_probability"),
        source_summary.get("adjusted_probability"),
        candidate.get("final_probability"),
        candidate.get("model_probability"),
    ))

    final_probability = _clamp_probability(_first_float(
        candidate.get("final_probability"),
        candidate.get("adjusted_probability"),
        source_summary.get("adjusted_probability"),
    ))

    implied_mismatch = _mismatch(selected_implied_probability, _clamp_probability(_to_float(candidate.get("implied_probability"))))
    adjusted_mismatch = _mismatch(canonical_adjusted_probability, _clamp_probability(_to_float(source_summary.get("adjusted_probability"))))
    final_mismatch = _mismatch(canonical_adjusted_probability, final_probability)

    canonical = dict(candidate)
    canonical["canonical_selected_odds"] = selected_odds
    canonical["canonical_selected_implied_probability"] = selected_implied_probability
    canonical["canonical_market_probability"] = market_probability
    canonical["canonical_fair_odds_from_market"] = fair_odds_from_market
    canonical["canonical_adjusted_probability"] = canonical_adjusted_probability
    canonical["canonical_final_probability"] = final_probability

    integrity = {
        "implied_mismatch": implied_mismatch,
        "adjusted_mismatch": adjusted_mismatch,
        "final_mismatch": final_mismatch,
        "selected_price_present": _to_float(source_summary.get("selected_price")) is not None,
        "market_probability_present": market_probability is not None,
        "canonical_adjusted_probability_present": canonical_adjusted_probability is not None,
        "is_suspicious": False,
        "reasons": [],
    }

    if implied_mismatch is not None and implied_mismatch > 0.02:
        integrity["is_suspicious"] = True
        integrity["reasons"].append(f"implied_mismatch:{implied_mismatch:.4f}")
    if adjusted_mismatch is not None and adjusted_mismatch > 0.02:
        integrity["is_suspicious"] = True
        integrity["reasons"].append(f"adjusted_mismatch:{adjusted_mismatch:.4f}")
    if final_mismatch is not None and final_mismatch > 0.02:
        integrity["is_suspicious"] = True
        integrity["reasons"].append(f"final_mismatch:{final_mismatch:.4f}")

    canonical["candidate_integrity"] = integrity
    diagnostics["candidate_integrity"] = integrity
    canonical["diagnostics"] = diagnostics
    return canonical


def load_latest_picks(repo_root: Path) -> list[dict[str, Any]]:
    data = _load_json(repo_root / ".data/exports/latest-picks.json", [])
    return [dict(item) for item in data if isinstance(item, dict)]


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
