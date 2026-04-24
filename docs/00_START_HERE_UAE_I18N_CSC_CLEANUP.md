# UAE i18n + CSC / country suffix cleanup

## Что исправляет

Последний Telegram показал:

```text
Al-Nasr Dubai CSC — Al Jazira (UAE)
🎯 Ставка: Фора 1(0) — Al-Nasr Dubai CSC
United Arab Emirates - Arabian Gulf League
```

После патча будет:

```text
Аль-Наср Дубай — Аль-Джазира
🎯 Ставка: Фора 1(0) — Аль-Наср Дубай
ОАЭ - Арабская лига Залива
```

## Что добавлено

1. Алиасы для клубов ОАЭ:
   - Al-Nasr Dubai CSC
   - Al Jazira (UAE)
   - Al Ain
   - Al Wasl
   - Al Wahda
   - Shabab Al Ahli Dubai
   - Ajman, Baniyas, Khor Fakkan, Kalba

2. Поддержка суффикса `CSC` как клубного суффикса.

3. Очистка страновых хвостов:
   - `(UAE)`
   - `(United Arab Emirates)`
   - `(Saudi Arabia)`
   - `(Poland)`
   - `(Austria)`

4. Перевод турнира:
   - `United Arab Emirates - Arabian Gulf League`
   - `UAE - Arabian Gulf League`

## Как применить

1. Распакуй архив в корень репозитория.
2. Проверь diff в GitHub Desktop.
3. Commit + push.
4. Запусти обычный `Run bot` с profile `balanced`.
