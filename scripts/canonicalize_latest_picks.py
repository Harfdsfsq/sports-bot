from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

ROOT = Path('.')
EXPORTS = ROOT / '.data' / 'exports'
ART = ROOT / 'artifacts'
ART.mkdir(parents=True, exist_ok=True)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def to_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return default


def canonicalize_item(item: dict[str, Any]) -> dict[str, Any]:
    source_summary = dict(item.get('source_summary') or {})
    odds = to_float(item.get('odds'))
    market_probability = clamp(to_float(item.get('market_probability'), 0.0), 0.0, 1.0)
    raw_model_probability = clamp(to_float(item.get('model_probability'), 0.0), 0.0, 1.0)
    adjusted_probability = clamp(to_float(item.get('adjusted_probability'), 0.0), 0.0, 1.0)
    source_adjusted = clamp(to_float(source_summary.get('adjusted_probability'), adjusted_probability), 0.0, 1.0)

    selected_odds = odds
    selected_implied_probability = 0.0 if odds <= 0 else round(1.0 / odds, 6)
    fair_odds_from_market = 0.0 if market_probability <= 0 else round(1.0 / market_probability, 6)
    canonical_adjusted_probability = round((adjusted_probability + source_adjusted) / 2.0, 6)
    edge_pct = round((canonical_adjusted_probability - market_probability) * 100.0, 4)
    ev_pct = round(((selected_odds * canonical_adjusted_probability) - 1.0) * 100.0, 4) if selected_odds > 0 else 0.0

    integrity_flags: list[str] = []
    if selected_odds > 0 and abs(selected_implied_probability - to_float(item.get('implied_probability'), 0.0)) > 0.02:
        integrity_flags.append('odds_implied_mismatch')
    if abs(canonical_adjusted_probability - source_adjusted) > 0.02:
        integrity_flags.append('adjusted_probability_mismatch')
    if edge_pct < 0 and ev_pct > 0:
        integrity_flags.append('edge_ev_conflict')
    if fair_odds_from_market > 0 and selected_odds / fair_odds_from_market > 1.20:
        integrity_flags.append('odds_to_fair_ratio_high')

    return {
        **item,
        'selected_odds': selected_odds,
        'selected_implied_probability': selected_implied_probability,
        'fair_odds_from_market': fair_odds_from_market,
        'raw_model_probability': raw_model_probability,
        'canonical_adjusted_probability': canonical_adjusted_probability,
        'canonical_edge_pct': edge_pct,
        'canonical_ev_pct': ev_pct,
        'integrity_flags': integrity_flags,
    }


def main() -> int:
    latest_picks = load_json(EXPORTS / 'latest-picks.json', [])
    if not isinstance(latest_picks, list):
        latest_picks = []
    canonical = [canonicalize_item(dict(item)) for item in latest_picks if isinstance(item, dict)]
    suspicious = [item for item in canonical if item.get('integrity_flags')]
    (ART / 'latest-canonical-picks.json').write_text(json.dumps(canonical, ensure_ascii=False, indent=2), encoding='utf-8')
    (ART / 'latest-candidate-integrity.json').write_text(json.dumps({
        'total_candidates': len(canonical),
        'suspicious_candidates': len(suspicious),
        'items': suspicious,
    }, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'canonical_candidates': len(canonical), 'suspicious_candidates': len(suspicious)}, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
