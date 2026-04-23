# Политика main-канала

В основной канал уходит только первый кандидат из `latest-picks.json`, если он одновременно:
- family в белом списке;
- quality >= 70;
- signal score >= 65;
- confidence >= 64;
- edge >= 3.5 п.п.;
- odds <= 2.35;
- books >= 2;
- без fallback / emergency / historical-relief;
- без single-source, если включён жёсткий режим;
- без odds/probability mismatch.

Если не проходит — бот ничего не публикует в основной канал.
