# Odds integrity policy

The audit flags candidates when any of the following are true:

- `odds` and `implied_probability` do not match;
- `odds` is too far from `fair_odds` / market fair odds;
- `odds` and `selected_price` differ;
- `adjusted_probability` differs from `source_summary.adjusted_probability`;
- `adjusted_probability` differs from `final_probability`;
- `edge_pct` is negative while `ev_pct` is positive.

This is meant to catch cases like:
- correct line + wrong selected odds;
- wrong consensus probability attached to the candidate;
- EV computed from a different probability than edge.
