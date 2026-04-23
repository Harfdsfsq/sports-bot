# Политика publish gate

## Main publish: допускаются только clean candidates
Обязательные условия:
- `quality_status == passed_quality`
- `single_source == false`
- `integrity_flags == 0`
- `quality_score >= 70`
- `signal_score >= 65`
- `confidence >= 62`
- `edge_pct >= 3.5`
- `selected_odds <= 2.35`
- `books_count >= 2`

## Запрет на main publish
Кандидат не должен публиковаться, если:
- fallback / emergency / historical relief
- single-source
- non-core + high odds
- cup + high variance
- integrity mismatch
- duplicated reasoning
- несколько разных model probabilities в writeup

## Shadow publish
Разрешено для:
- исследовательских btts
- borderline totals
- non-core
- cup
- single-source
- candidate после strict internal checks, но ниже main quality

## Hard reject
- integrity mismatch
- invalid odds/probability relation
- edge/EV contradiction
- broken line normalization
- unsupported total line without supported mapping
