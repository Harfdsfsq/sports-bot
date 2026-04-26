# Репозиторная чистка generated-файлов

## Главная проблема

Workflow раньше коммитил generated-output:

```bash
git add -A -f .data .logs artifacts/...
```

Из-за `-f` даже `.gitignore` не спасал. Поэтому в репозиторий попадали:

```text
.logs/
.data/exports/
.data/provider_cache/
artifacts/
run-bot-bundle.zip
latest-*.json
latest-*.csv
```

Это раздувает историю и делает GitHub Desktop тяжёлым.

## Что изменено

### 1. `.gitignore`

Теперь игнорируются все generated-файлы, но сохраняются нужные cross-run state-файлы:

```text
.data/state.json
.data/fallback-sent-index.json
.data/provider_quota_governor_state.json
.data/provider_quota_state.json
.data/daily-ops-report-sent.json
.data/calibration-profile.json
```

### 2. Workflow commit steps

В `run-bot.yml` и `daily-report.yml` теперь коммитятся только persistent state-файлы.

Больше не коммитятся:

```text
.logs/
artifacts/
.data/exports/
.data/provider_cache/
.data/history/
```

### 3. Одноразовая локальная чистка

Добавлен dry-run скрипт:

```bash
python scripts/cleanup_generated_repo_files.py
```

Он ничего не удаляет, а пишет список кандидатов в:

```text
repo_cleanup_report.json
```

После проверки можно применить:

```bash
python scripts/cleanup_generated_repo_files.py --apply
```

Если хочешь также удалить legacy/no-op patcher:

```bash
python scripts/cleanup_generated_repo_files.py --apply --include-legacy-patchers
```

## Что НЕ чистить

Не удалять:

```text
app/
scripts/build_*.py
scripts/publish_controlled_fallback.py
scripts/apply_provider_quota_governor.py
config/*.env
config/telegram_i18n_aliases.json
.github/workflows/*.yml
```

## Как применять

1. Распаковать архив в корень `sports-bot`.
2. Открыть GitHub Desktop.
3. Проверить diff по:
   - `.gitignore`
   - `.github/workflows/run-bot.yml`
   - `.github/workflows/daily-report.yml`
   - `scripts/cleanup_generated_repo_files.py`
   - `docs/REPO_CLEANUP_README.md`
4. Сначала запустить dry-run:
   ```bash
   python scripts/cleanup_generated_repo_files.py
   ```
5. Проверить `repo_cleanup_report.json`.
6. Применить:
   ```bash
   python scripts/cleanup_generated_repo_files.py --apply
   ```
7. Проверить diff в GitHub Desktop.
8. Commit + Push вручную.

## Важно

Этот фикс не переписывает историю Git. Он перестаёт коммитить мусор дальше и удаляет generated-файлы из текущего дерева. Чтобы уменьшить размер всей истории репозитория, нужен отдельный опасный этап через `git filter-repo`/BFG; его сейчас не делаем.


## Что исправлено в v2

Последний лог показал, что старый commit-step ещё выполнялся:

```bash
git add -A -f .data .logs artifacts/run-bot artifacts/controlled-fallback-report.json
```

Он попытался закоммитить `.data/exports` и `.logs`, затем `git push` упал с `fetch first`.

В v2 workflow напрямую заменён на:

```text
Commit persistent bot state only
```

Коммитятся только state-файлы, а перед push добавлен безопасный:

```bash
git pull --rebase --autostash origin main || true
```

Также `history-guard-audit` больше не печатает traceback в общий лог; stderr уходит в:

```text
.data/exports/latest-history-guard-audit.err
```


## Что исправлено в stable runtime fix

Последний run падал не в прогнозах, а в последнем git-шаге:

```text
CONFLICT (content): .data/provider_quota_governor_state.json
CONFLICT (content): .data/state.json
fatal: You are not currently on a branch
exit code 128
```

Теперь workflow не делает `git pull --rebase` поверх уже созданного state-коммита. Вместо этого используется:

```bash
python scripts/sync_persistent_state.py || true
```

Скрипт:

1. копирует свежие state-файлы во временную папку;
2. делает `git fetch origin main`;
3. делает `git reset --hard origin/main`;
4. возвращает свежие state-файлы;
5. коммитит только persistent state;
6. пушит в `main`;
7. при конфликте/ошибке пробует ещё раз;
8. если push всё равно не вышел — пишет warning и возвращает exit code `0`.

То есть bot run больше не падает из-за state-sync.

Также manual `workflow_dispatch` теперь отправляет подробный Telegram no-pick отчёт. Push по-прежнему не спамит Telegram.
