# Start here

Этот пакет предназначен для локальной замены поверх репозитория.

Что он делает:
- включает single-run snapshot режим;
- добавляет внутренний integrity-слой для кандидатов;
- усиливает сохранение raw trace для odds/probability;
- добавляет workflow для одного прогона и одного bundle.

Порядок:
1. Распаковать архив в корень репозитория.
2. Проверить diff в GitHub Desktop.
3. Commit и push.
4. Запустить workflow `Run bot • internal pipeline integrity`.
5. Скачать `internal-pipeline-bundle.zip`.
