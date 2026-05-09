# cron-job.org setup — build-day-inventory 00:00 MSK

Этот job запускает отдельный GitHub Actions workflow `build-day-inventory.yml`. Он только собирает полный список матчей дня и сохраняет inventory. Прогнозы, линии, контекст и публикация остаются в обычном `run-bot` workflow.

## 1. GitHub token

Создай fine-grained token в GitHub:

```text
Settings → Developer settings → Personal access tokens → Fine-grained tokens → Generate new token
```

Доступ:

```text
Repository access: Only selected repositories → Harfdsfsq/sports-bot
Permissions:
  Actions: Read and write
  Contents: Read-only is enough for dispatch, but Read and write is ok if used for related automation
```

Сохрани token. Он будет нужен в cron-job.org header `Authorization`.

## 2. Cron-job.org job

Создай новый cronjob:

```text
cron-job.org → Cronjobs → Create cronjob
```

Настройки:

```text
Title: HARIZON daily inventory 00:00 MSK
URL: https://api.github.com/repos/Harfdsfsq/sports-bot/actions/workflows/build-day-inventory.yml/dispatches
Schedule timezone: Europe/Moscow
Schedule: every day at 00:00
Request method: POST
Timeout: 30 seconds or больше, если доступно
Save responses: enabled
```

Headers:

```text
Accept: application/vnd.github+json
Authorization: Bearer YOUR_GITHUB_FINE_GRAINED_TOKEN
X-GitHub-Api-Version: 2022-11-28
Content-Type: application/json
User-Agent: cron-job-org-harizon-day-inventory
```

Body:

```json
{
  "ref": "main",
  "inputs": {
    "target_date": "",
    "bootstrap_provider": "odds_api_io",
    "force_provider_merge": "true"
  }
}
```

`target_date` пустой = workflow сам берёт текущую дату в `Europe/Moscow`.

## 3. Проверка

После сохранения нажми test/run now.

Ожидаемый ответ GitHub API:

```text
204 No Content
```

Потом проверь:

```text
GitHub → Harfdsfsq/sports-bot → Actions → build-day-inventory
```

После успешного запуска должны обновиться файлы:

```text
.data/day_inventory/YYYY-MM-DD.json
.data/day_inventory/today.json
.data/day_inventory/current.json
.data/day_inventory/latest.json
.data/cache/day_inventory/YYYY-MM-DD.json
.data/exports/latest-day-inventory-summary.json
.data/exports/latest-build-day-inventory.log
```

## 4. Обычный run-bot

Обычный `run-bot.yml` запускай отдельным cron-job.org job каждые 2 часа. Он должен дергать:

```text
https://api.github.com/repos/Harfdsfsq/sports-bot/actions/workflows/run-bot.yml/dispatches
```

Body для обычного run:

```json
{
  "ref": "main",
  "inputs": {
    "mode": "normal"
  }
}
```

Рекомендуемый график MSK:

```text
02:00, 04:00, 06:00, 08:00, 10:00, 12:00, 14:00, 16:00, 18:00, 20:00, 22:00
```

00:00 MSK лучше оставить под `build-day-inventory`, чтобы сначала собрать полный пул матчей дня.
