# Обычный Run bot

Это пакет под обычный workflow `Run bot`.

## Что внутри
- `.github/workflows/run-bot.yml`
- `config/balanced_output.env`
- `config/conservative_passability.env`
- `config/calibration-profile.example.json`

## Как применить
1. Распакуй архив в корень локального репозитория.
2. Проверь изменения в GitHub Desktop.
3. Commit и push.
4. В GitHub Actions запусти обычный workflow **Run bot**.

## Что выбрать
- `balanced` — основной профиль для повседневных ручных запусков.
- `conservative` — если нужно меньше, но чище прогнозов.
- `default` — без наложения профиля.

## Что важно
Этот архив не включает fixed-run, canonical или дополнительные экспериментальные workflow.
