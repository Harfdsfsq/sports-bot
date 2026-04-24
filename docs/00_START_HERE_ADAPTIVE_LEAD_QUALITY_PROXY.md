# Adaptive lead + quality proxy fix

## Что показал последний отчёт

Свежий rescue-pool уже работает:

```text
Проверено резервных кандидатов: 245
latest_rescue_candidates: 245
```

Но безопасный кандидат мог не пройти по двум причинам:

1. Внешний резерв использовал жёсткий `MIN_KICKOFF_LEAD_MINUTES=20`, хотя ручной запуск уже задаёт adaptive lead `10`.
2. Pre-filter кандидаты ещё не проходили quality-layer, поэтому в резерве у них `quality_score=0.0`, даже если EV, confidence и books нормальные.

## Что исправляет пакет

1. Внешний резерв теперь использует adaptive lead:
   - если `MANUAL_LATE_MODE_ENABLED=true`,
   - берётся минимум из `MIN_KICKOFF_LEAD_MINUTES`, `MANUAL_LATE_MIN_KICKOFF_LEAD_MINUTES`, `MANUAL_LATE_ADAPTIVE_MIN_KICKOFF_LEAD_MINUTES`.

2. Для свежих pre-filter кандидатов добавлена резервная оценка качества:
   - используется только если raw `quality_score=0`;
   - считается из confidence, publication score, books, EV и edge;
   - ограничена сверху `CONTROLLED_FALLBACK_PROXY_MAX_QUALITY=76`.

3. Отрицательный EV всё ещё запрещён.

4. В Telegram причины отказа с динамическими кодами теперь переводятся:
   - `family_not_allowed:spreads` → `рынок не разрешён: фора`;
   - `match_time_outside_window` → `матч вне текущего окна публикации`.

## Как применить

1. Распакуй архив в корень репозитория.
2. Проверь diff в GitHub Desktop.
3. Commit + push.
4. Запусти обычный `Run bot` с profile `balanced`.
