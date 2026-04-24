# DNB outlier guard + Egypt i18n

## Что показал свежий прогноз

```text
Talaea El Gaish — Kahrabaa Ismailia
Фора 2(0)
коэффициент 2.75
xG: 0.64 : 2.35
DNB без ничьей: 90.0%
xG EV: +124.0%
```

Такой профиль выглядит слишком красиво. При `quality_score_source=proxy` и `sources_count=1` это чаще риск ошибки xG, матчинга команд или аномального рынка, а не надёжный прогноз.

## Что исправляет пакет

Добавлен DNB outlier guard для сигналов:

```text
quality_score_source = proxy
sources_count < 2
```

Теперь такой DNB режется, если:

```env
CONTROLLED_FALLBACK_DNB_MAX_ABS_MODEL_XG_GAP_PP=28.0
CONTROLLED_FALLBACK_DNB_MAX_XG_EV_UNCONDITIONAL_PCT=70.0
CONTROLLED_FALLBACK_DNB_MAX_XG_EDGE_PP=32.0
CONTROLLED_FALLBACK_DNB_MAX_NO_PUSH_PROBABILITY_PCT=82.0
```

Текущий кейс Kahrabaa должен получить стопоры:

```text
dnb_xg_model_gap_outlier
dnb_xg_ev_outlier
dnb_xg_edge_outlier
dnb_xg_probability_outlier
```

## Почему не запрещаем все DNB

DNB остаётся рабочим рынком. Например, прошлый кейс Duisburg был допустимым, потому что xG подтверждал его, но не был аномальным:

```text
DNB без ничьей: ~49.4%
xG EV: +23.8%
gap: -7.9 п.п.
```

## Добавлен перевод Египта

```text
Talaea El Gaish — Kahrabaa Ismailia
```

станет:

```text
Талаеа Эль-Гаиш — Кахраба Исмаилия
```

## Как применить

1. Распакуй архив в корень репозитория.
2. Проверь diff в GitHub Desktop.
3. Commit + push.
4. Запусти `Run bot` с profile `balanced`.
5. Пришли новый `run-bot-current`.
