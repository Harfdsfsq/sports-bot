# Phase 1–2 implementation notes

## 1. Canonical candidate model
Минимальный canonical payload для публикации:

- `selected_odds`
- `selected_implied_probability`
- `market_probability`
- `consensus_probability`
- `fair_odds_from_market`
- `raw_model_probability`
- `canonical_adjusted_probability`
- `edge_pct`
- `ev_pct`
- `books_count`
- `sources_count`
- `quality_status`
- `quality_score`
- `risk_flags[]`
- `integrity_flags[]`

## 2. Integrity hard-reject
Reject candidate, если выполняется хотя бы одно:

- `abs((1 / selected_odds) - selected_implied_probability) > 0.02`
- `abs(canonical_adjusted_probability - source_summary.adjusted_probability) > 0.02`
- `edge_pct < 0 and ev_pct > 0`
- `selected_odds / fair_odds_from_market > 1.20` без отдельной валидации

## 3. Main publish gate
Публикация в главный канал разрешена только если:

- `quality_status == passed_quality`
- `quality_score >= 70`
- `books_count >= 2`
- `sources_count >= 1`
- нет `fallback`, `last_resort`, `historical_relief`
- нет `single_source_rejected`
- рынок входит в allow-list

## 4. Allow-list рынков для main
- `totals`
- `dnb`
- `h2h` только в core-лигах и только в диапазоне odds 1.75–2.40

`btts` и `spreads` по умолчанию оставлять в shadow до стабилизации ROI.
