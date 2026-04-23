# Fixed run package

Этот пакет не добавляет новый режим на каждый раз. Он стандартизирует один workflow:

- `Run bot • fixed run`

И один комплект latest-артефактов:

- `artifacts/fixed-run/latest-canonical-picks.json`
- `artifacts/fixed-run/latest-candidate-integrity.json`
- `artifacts/fixed-run/latest-odds-integrity-report.json`
- `artifacts/fixed-run/latest-run.json`
- `artifacts/fixed-run-bundle.zip`

Каждый новый запуск перезаписывает эти файлы.
