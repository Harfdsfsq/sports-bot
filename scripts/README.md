# Scripts

## load_env_safe.sh
Безопасная загрузка env-файла в GitHub Actions:
- игнорирует комментарии
- игнорирует пустые строки
- пишет в `$GITHUB_ENV` только `KEY=VALUE`
