# Checklist готовности к бою

## Integrity
- [ ] `odds ↔ implied_probability` без mismatch
- [ ] `adjusted_probability` в candidate и source_summary совпадает
- [ ] `edge_pct` и `ev_pct` не конфликтуют
- [ ] `fair_odds_from_market` корректен

## Publish
- [ ] fallback не идёт в main
- [ ] single-source не идёт в main
- [ ] quality_last_resort не идёт в main
- [ ] main publish report чистый

## Strategy
- [ ] main рынки ограничены
- [ ] avg main odds <= 2.35
- [ ] нет high-variance мусора в главном канале

## Monitoring
- [ ] fixed-run bundle собирается
- [ ] alerts настроены
- [ ] odds source активен
- [ ] publish window согласован с реальным расписанием

## Readiness thresholds
- [ ] 14 дней без published suspicious candidate
- [ ] rolling ROI не хуже 0% на диагностическом окне
- [ ] suspicious ratio < 2%
- [ ] хотя бы 90% main-кандидатов имеют books >= 2 и не single-source
