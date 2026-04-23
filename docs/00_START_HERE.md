# HARIZON Phase 1–2 starter patch-pack

Этот пакет — стартовый набор для первых двух фаз ремонта:

## Phase 1 — Data Integrity
- привести candidate-поля к каноническому виду
- отделить `selected_odds` от `fair_odds_from_market`
- считать `selected_implied_probability` строго как `1 / selected_odds`
- не пускать кандидата дальше при mismatch `odds / implied / adjusted / EV`

## Phase 2 — Main Publish Gate
- не публиковать fallback / relief / last-resort сигналы в основной канал
- main publish только для clean-pass кандидатов
- single-source, non-core, suspicious candidates уходят в shadow

## Что делать
1. Распаковать архив в корень репозитория.
2. Взять `config/phase12_fixed_run.env.example` как основу профиля.
3. Подключить workflow `Run bot • phase12 fixed run`.
4. Проверить артефакты:
   - `phase12-bundle.zip`
   - `latest-candidate-integrity.json`
   - `latest-canonical-picks.json`
   - `publish-gate-report.json`
