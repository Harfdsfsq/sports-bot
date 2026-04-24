# Final stability + accuracy fix

## Что показал свежий прогноз

```text
Alemannia Aachen — MSV Duisburg
Фора 2(0) — MSV Duisburg
Линии: 2
Источники: 1
quality_score_source: proxy
```

Это уже лучше, чем single-book Tier C, потому что есть 2 букмекерские линии. Но остаётся риск: обе линии пришли через один data-provider, а качество всё ещё резервное.

## Что исправляет пакет

### 1. Усиленный фильтр для proxy + 1 source

Теперь если сигнал имеет `quality_score_source=proxy` и `sources_count < 2`, он должен пройти усиленные условия:

```env
CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP=3.0
CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT=7.0
CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE=68.0
```

В conservative профиле пороги выше.

### 2. DNB теперь проверяется не только направлением xG

Добавлены xG fair-odds метрики для DNB:

- `dnb_xg_no_push_edge_pp`
- `dnb_xg_ev_no_push_pct`
- `dnb_xg_ev_unconditional_pct`

Публикация DNB теперь требует:

```env
CONTROLLED_FALLBACK_DNB_MIN_XG_EDGE_PP=3.0
CONTROLLED_FALLBACK_DNB_MIN_XG_EV_UNCONDITIONAL_PCT=4.0
```

То есть ставка на Фору 1/2(0) должна иметь не только model EV, но и независимое xG-подтверждение.

### 3. Telegram честнее показывает DNB

Было:

```text
🔎 DNB-проверка: без ничьей 49.4% | разрыв -7.9 п.п.
```

Станет:

```text
🔎 DNB-проверка: без ничьей 49.4% | xG EV +...% | разрыв -7.9 п.п.
```

### 4. Перевод Германии

```text
Alemannia Aachen — MSV Duisburg
Germany - 3. Liga
```

станет:

```text
Алеманния Ахен — Дуйсбург
Германия - Третья лига
```

## Что будет с текущим типом прогноза

Прогноз вроде `Alemannia Aachen — MSV Duisburg / Фора 2(0)` сможет пройти только если одновременно:

- 2 линии;
- EV >= 7% для proxy+1source;
- edge >= 3 п.п.;
- confidence >= 68%;
- DNB xG EV положительный и выше минимума.

Слабые похожие DNB больше не пройдут.

## Как применить

1. Распакуй архив в корень репозитория.
2. Проверь diff в GitHub Desktop.
3. Commit + push.
4. Запусти `Run bot` с profile `balanced`.
5. Пришли новый `run-bot-current`.
