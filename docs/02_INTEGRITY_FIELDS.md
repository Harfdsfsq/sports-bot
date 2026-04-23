# Какие поля стоит считать каноническими

Рекомендуемый смысл полей:
- `selected_odds` — реальный odds для публикации;
- `selected_implied_probability` — всегда `1 / selected_odds`;
- `market_probability` — probability от консенсуса;
- `fair_odds_from_market` — `1 / market_probability`;
- `adjusted_probability` — единственный canonical probability для EV/edge;
- `final_probability` — alias canonical probability или техническое поле, но не отдельный источник истины.

Если одно из этих полей расходится, кандидат должен получать integrity-flag.
