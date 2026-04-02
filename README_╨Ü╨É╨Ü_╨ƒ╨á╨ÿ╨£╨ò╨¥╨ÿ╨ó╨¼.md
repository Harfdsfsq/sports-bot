1. Распакуй архив.
2. Скопируй содержимое папок `.github`, `app`, `apps_script`, `docs` в корень локального репозитория с заменой файлов.
3. Закоммить изменения через GitHub Desktop.
4. Проверь, что в GitHub Secrets/Variables есть значения из workflow.
5. Для Google Sheets:
   - бот начнёт писать `.data/sheet-export.json` и `.data/sheet-export.csv`;
   - вставь `apps_script/sheet_sync.gs` в Apps Script;
   - задай `SHEET_ID`, `SHEET_NAME`, `RAW_JSON_URL` в Script Properties;
   - один раз выполни `create15MinTrigger()`.
