# Single-run policy

Все решения одного прогона должны базироваться на одном snapshot:
- те же матчи;
- те же offers;
- те же contexts;
- та же normalization map;
- тот же audit bundle.

Идея простая: один workflow run = один набор входных данных = один bundle для анализа.
