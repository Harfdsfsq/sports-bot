# Тест-план и QA

## Unit tests
### candidate integrity
- `selected_implied_probability == 1 / selected_odds`
- `fair_odds_from_market == 1 / market_probability`
- `final_probability == adjusted_probability`
- EV и edge считаютcя из canonical probability

### publish gate
- fallback candidate не публикуется
- single-source candidate не публикуется
- integrity mismatch candidate не публикуется
- clean candidate проходит

### writeup validation
- одна ставка = одна canonical probability
- нет дублирующих bullets
- нет конфликтующих чисел внутри одного текста

## Integration tests
- fixed-run с mock data
- fixed-run с odds API present
- empty-window run
- run с unsupported total line
- run с suspicious candidate должен завершаться без main publish

## Regression checklist
- suspicious ratio
- avg odds
- published count
- passed_quality count
- share of fallback candidates
