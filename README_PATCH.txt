Что в архиве:
- app/config.py
- app/providers/bookies_api.py
- app/services/runner.py
- .github/workflows/run-bot.yml

Что сделано:
1. Добавлена интеграция с bookiesapi.com на основе старого Google Apps Script:
   - login + token
   - task=predatapage для поиска событий
   - task=allodds, fallback на task=odds
2. Bookies API подключен в runner как третий источник коэффициентов.
3. В workflow прокинуты BOOKIES_API_* secrets и исправлен Telegram secret.
4. Расписание уменьшено до 1 запуска раз в 3 часа, чтобы меньше жечь лимит The Odds API.
5. В debug summary добавлен блок bookies_api.

Как применить:
- заменить эти файлы в репозитории
- проверить, что секрет BOOKIES_API_ENABLED имеет значение true
- запустить workflow вручную
