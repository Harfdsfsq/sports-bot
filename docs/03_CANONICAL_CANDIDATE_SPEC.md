# Спецификация canonical candidate fields

## Обязательные поля
| Поле | Смысл |
|---|---|
| `selected_odds` | Реальный odds, который идёт в публикацию |
| `selected_implied_probability` | Всегда `1 / selected_odds` |
| `market_probability` | Probability от consensus market |
| `fair_odds_from_market` | Всегда `1 / market_probability` |
| `raw_model_probability` | Сырая модельная вероятность до shrink/calibration |
| `adjusted_probability` | Единственная canonical probability для EV/edge |
| `final_probability` | Alias `adjusted_probability` |
| `edge_pct` | Считается от `adjusted_probability - market_probability` |
| `ev_pct` | Считается строго от `selected_odds` и `adjusted_probability` |

## Инварианты
```text
selected_implied_probability = 1 / selected_odds
fair_odds_from_market = 1 / market_probability
final_probability = adjusted_probability
edge_pct = (adjusted_probability - market_probability) * 100
ev_pct = ((selected_odds * adjusted_probability) - 1) * 100
```

## Что хранить дополнительно
- `selected_bookmaker`
- `selected_source`
- `selected_price`
- `raw_bucket_offers`
- `raw_line`
- `normalized_line`
- `price_used_for_ev`
- `probability_used_for_ev`

## Что считать блокирующей ошибкой
- нарушение любого инварианта
- пустой canonical probability
- отрицательный или нереальный odds
- конфликт между publish odds и stored selected price
