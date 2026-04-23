# Что это закрывает

Пакет фокусируется на трёх проблемах:
1. рассинхрон `odds / implied_probability / fair_odds`;
2. рассинхрон `adjusted_probability` между объектом кандидата и `source_summary`;
3. необходимость хранить raw bucket/offers trace в рамках одного run.

Это следующий шаг после внешнего `main-clean` фильтра: теперь данные собираются так, чтобы было проще чинить сам pipeline.
