# Dedupe shadow policy fix

## Что исправляет

Последний run показал, что controlled fallback проверил 5 кандидатов, но несколько были заблокированы как:

- `duplicate_state:shadow_bets`
- `duplicate_state:bets`

Проблема: `shadow_bets` — это диагностическое наблюдение, а не реальная опубликованная ставка. Старый dedupe блокировал shadow так же жёстко, как Telegram-публикации, из-за чего fallback не мог опубликовать кандидата, который ранее только трекался в shadow.

## Новая политика dedupe

Блокируется:

- `fallback-sent-index` — всегда;
- реальные опубликованные `bets`, если `telegram_sent=true`;
- реальные pending-ставки с положительным stake;
- `published_candidates`, если они реально отправлялись.

Не блокируется по умолчанию:

- `shadow_bets`;
- `generated` / dry-run строки без `telegram_sent=true`.

## Новые env-переменные

```env
CONTROLLED_FALLBACK_DEDUPE_STATE_BETS=true
CONTROLLED_FALLBACK_DEDUPE_STATE_PUBLISHED=true
CONTROLLED_FALLBACK_DEDUPE_STATE_SHADOW=false
```

Если захочешь снова сделать shadow жёстким блокером:

```env
CONTROLLED_FALLBACK_DEDUPE_STATE_SHADOW=true
```

## Как применять

1. Распаковать архив в корень репозитория.
2. Проверить diff в GitHub Desktop.
3. Commit + push.
4. Запустить обычный `Run bot` с profile `balanced`.

## Ожидаемый эффект

Если кандидат был только в shadow, но сейчас проходит controlled fallback по EV/edge, он сможет быть опубликован как Tier A/B/C.

Если кандидат уже реально отправлялся в Telegram, он всё равно будет заблокирован через `fallback-sent-index` или реальные `bets`.
