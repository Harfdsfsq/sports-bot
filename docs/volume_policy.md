# Volume policy v11

v11 исправляет ошибку v10: `state.shadow_bets` больше не считается опубликованными прогнозами для дневного cap.

## Что было

v10 видел diagnostic/watchlist rows в `state.shadow_bets` и считал их как реальные прогнозы. В результате при `state_shadow_bets=6` и `daily_hard_cap=5` governor выставлял:

```text
CONTROLLED_FALLBACK_ENABLED=false
MAX_PICKS_PER_RUN=0
```

После этого controlled fallback не проверял резерв (`evaluated=[]`, `candidates_seen=0`), а detailed report писал `Резерв проверил: 0`.

## Что стало

Для дневного лимита считаются только реальные публикации:

- `.data/fallback-sent-index.json`;
- `.data/state.json -> bets`;
- `.data/state.json -> published_candidates`.

`state.shadow_bets` остаётся в диагностике, но не блокирует публикации.

Остальная volume-логика `target_3 / target_5 / target_7` сохранена.

---

# Volume policy v10/v11 base

Цель v10: повысить дневной объём прогнозов до управляемого диапазона без отключения hard-guard'ов.

## Режим по умолчанию

`config/volume_policy.json` по умолчанию включает:

```json
"mode": "target_3"
```

Цель режима:

- target: 3 прогноза в день;
- soft cap: 4 прогноза в день;
- hard cap: 5 прогнозов в день;
- максимум 2 прогноза за один run;
- максимум 1 прогноз на матч;
- Tier C разрешён только как micro-stake.

## Что сохраняется жёстким

v10 не отключает:

- отрицательную контрольную ценность;
- конфликт направления с xG;
- xG hard reject;
- DNB outlier guard;
- запрет слабого 1-book proxy как обычного Telegram-прогноза;
- дедупликацию по матчу;
- caps по stake/exposure.

## Изменение проходности

Tier A остаётся строгим. Основной прирост объёма идёт через:

- чуть более проходной Tier B;
- micro Tier C с маленькой ставкой;
- дневной volume governor, который не даёт бесконтрольно набрать прогнозы в одном дне.

### target_3

- Tier B: confidence 64.5, quality 60, edge 3.2 п.п., EV 6.5%, books 2.
- Tier C: confidence 68, quality 58, edge 3.5 п.п., EV 7.5%, odds <= 2.25, books 2.
- Tier C stake cap: 2.5.

### target_5

Режим добавлен в конфиг, но не является дефолтом. Включать только после нескольких дней стабильной работы target_3.

### target_7

Агрессивный learning-volume режим. Включать только после достаточной выборки закрытых ставок и приемлемых ROI/CLV.

## Workflow

`run-bot.yml` запускает `scripts/apply_volume_policy.py` после autorun policy и до основного запуска бота.

Скрипт пишет:

- `.data/volume-governor-state.json`
- `.data/exports/latest-volume-governor.json`

и добавляет runtime env в `$GITHUB_ENV`.

## Persistent state

`sync_persistent_state.py` сохраняет:

- `.data/autorun-state.json`
- `.data/volume-governor-state.json`

Это нужно, чтобы watchdog и дневной governor помнили состояние между GitHub Actions jobs.

## Переключение режима вручную

Через `workflow_dispatch` можно выбрать:

- `target_3`
- `target_5`
- `target_7`

Для schedule используется режим из workflow env, по умолчанию `target_3`.
