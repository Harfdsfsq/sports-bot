# Candidate pool widening fix

## Что исправляет

Последний запуск проверил только 1 резервного кандидата. Это произошло потому, что внутренний `CandidateFactory._filter_and_rank()` выбрасывал почти все near-miss варианты ещё до того, как внешний контролируемый резерв мог их проверить.

Этот пакет добавляет pre-filter rescue pool:

1. Перед `_filter_and_rank()` сохраняется расширенный список кандидатов.
2. Runner пишет его в:
   - `.data/exports/latest-rescue-candidates.json`
   - `artifacts/run-bot/latest-rescue-candidates.json`
3. `publish_controlled_fallback.py` берёт кандидатов не только из debug, но и из rescue-файла.
4. Финальный фильтр всё ещё запрещает отрицательную контрольную ценность.

## Почему это безопаснее, чем просто ослабить quality

Внутренний бот по-прежнему не публикует Telegram напрямую:

- `PREDICTION_PUBLICATION_ENABLED=false`
- `RUN_REPORT_ENABLED=false`

Публикацией занимается только внешний контролируемый резерв. Он заново считает:

- implied probability от выбранного коэффициента;
- контрольный edge;
- контрольный EV;
- дубли;
- ограничения по рынкам и коэффициентам.

Если EV или edge отрицательные, ставка не отправляется.

## Что должно измениться в следующем логе

Было:

```text
Проверено резервных кандидатов: 1
Пул кандидатов:
• debug_candidates_before_quality: 1
```

Ожидаем после фикса:

```text
Проверено резервных кандидатов: 10–100+
Пул кандидатов:
• latest-rescue-candidates.json: ...
• debug_candidates_before_quality: ...
```

## Как применить

1. Распаковать архив в корень репозитория.
2. Проверить diff в GitHub Desktop.
3. Commit + push.
4. Запустить обычный `Run bot` с profile `balanced`.
5. Прислать `run-bot-current` artifact.
