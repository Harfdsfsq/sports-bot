# Acceptance criteria

## Phase 1 success
- 0 опубликованных кандидатов с odds/implied mismatch > 0.02
- 0 кандидатов с `edge_pct < 0` и `ev_pct > 0`
- 0 кандидатов с двумя разными adjusted probability в canonical trace

## Phase 2 success
- 0 main publishes с quality fallback / last resort
- 0 main publishes с books_count < 2
- 0 main publishes с single-source risk flag
- не меньше 80% опубликованных сигналов проходят strict clean gate

## Readiness to expand
Расширять рынки только если за 30–50 settled bets:
- ROI >= 0
- CLV не отрицательный
- suspicious candidates в main publish = 0
