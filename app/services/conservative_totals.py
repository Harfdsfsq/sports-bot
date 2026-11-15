"""Independent frozen paper challenger. Never publishes or authorizes live bets.

Only the fixed 2.5 line is calibrated and selected. Historical development rows
train the calibrator; only fixtures frozen after declaration enter evaluation.
"""
from __future__ import annotations

import ast
import hashlib
import json
import math
from collections import Counter
from copy import deepcopy
from datetime import timedelta
from pathlib import Path
from statistics import mean

from app.services.daily_match_registry import parse_time
from app.services.fixture_identity import same_team_identity
from app.utils import normalize_bookmaker_name

ROOT = Path(__file__).resolve().parents[2]
MODELS = ("current_goals", "opponent", "shrunk", "overdispersed")


def policy():
    rules = json.loads((ROOT / "config/conservative_totals.json").read_text())
    if rules["point"] != 2.5:
        raise ValueError("This protocol supports only the fixed non-refunding 2.5 line")
    return rules


def fingerprint(rules, base_hash):
    source = ast.dump(ast.parse(Path(__file__).read_text()), include_attributes=False)
    return hashlib.sha256((source + json.dumps(rules, sort_keys=True) + base_hash).encode()).hexdigest()


def probability(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and 0 < value < 1


def logit(value):
    return math.log(value / (1 - value))


def prediction(raw, market, alpha, intercept):
    z = logit(market) + alpha * (logit(raw) - logit(market)) + intercept
    return 1 / (1 + math.exp(-max(-20, min(20, z))))


def anchor(row, point):
    a = row.get("anchor") or {}
    if a.get("point") != point or not all(probability(a.get(k)) for k in (*MODELS, "market")):
        return None
    # These projections are correlated models, not independent data providers.
    raw = mean(a[k] for k in ("shrunk", "overdispersed"))
    return raw, a["market"]


def verified_result(row, now):
    created, kickoff, resolved = (parse_time(row.get(k)) for k in ("created_at", "kickoff", "resolved_at"))
    total = row.get("total")
    ids = (row.get("result_evidence") or {}).get("provider_ids") or {}
    return (row.get("status") == "resolved" and created is not None and kickoff is not None
            and resolved is not None and created < kickoff < resolved <= now
            and kickoff - created >= timedelta(minutes=30)
            and isinstance(total, (int, float)) and not isinstance(total, bool)
            and math.isfinite(total) and 0 <= total <= 30 and float(total).is_integer()
            and bool(ids) and all(isinstance(v, list) and len(v) == 1 and v[0] for v in ids.values()))


def same_fixture(a, b):
    first, second = parse_time(a.get("kickoff")), parse_time(b.get("kickoff"))
    return (first is not None and second is not None and abs(first - second) <= timedelta(minutes=15)
            and same_team_identity(a.get("home"), [b.get("home")])
            and same_team_identity(a.get("away"), [b.get("away")]))


def fit(rows, now, rules, base_hash):
    usable, seen = [], []
    for row in sorted(rows, key=lambda r: r.get("created_at", "")):
        if row.get("protocol") != "priced-totals-v1" or row.get("model_hash") != base_hash:
            continue
        if any(same_fixture(row, old) for old in seen):
            continue
        seen.append(row)
        if not verified_result(row, now):
            continue
        values = anchor(row, rules["point"])
        if values is None:
            continue
        usable.append((parse_time(row["kickoff"]), row["id"], *values, float(row["total"] > rules["point"])))
    usable.sort()
    report = {"status": "insufficient_history", "count": len(usable), "trained_before": now.isoformat(),
              "alpha": 0.0, "intercept": 0.0}
    if len(usable) < rules["min_calibration"]:
        return report
    boundary = usable[int(len(usable) * .75)][0]
    train, valid = [r for r in usable if r[0] < boundary], [r for r in usable if r[0] >= boundary]
    report.update(train_count=len(train), validation_count=len(valid), split_at=boundary.isoformat())
    if len(train) < rules["min_training"] or len(valid) < rules["min_validation"]:
        return report
    alpha, intercept = 0.0, 0.0
    for _ in range(250):
        ga, gb = .08 * alpha, .1 * intercept
        for _, _, raw, market, y in train:
            error = prediction(raw, market, alpha, intercept) - y
            ga += error * (logit(raw) - logit(market)) / len(train)
            gb += error / len(train)
        alpha = max(0.0, min(.5, alpha - .3 * ga))
        intercept = max(-.25, min(.25, intercept - .3 * gb))

    def scores(corrected):
        pairs = [(prediction(raw, market, alpha, intercept) if corrected else market, y)
                 for _, _, raw, market, y in valid]
        return (mean((p - y) ** 2 for p, y in pairs),
                mean(-math.log(max(1e-12, p if y else 1 - p)) for p, y in pairs))

    brier, loss = scores(True)
    market_brier, market_loss = scores(False)
    accepted = brier < market_brier and loss < market_loss
    report.update(status="validated" if accepted else "market_not_beaten", alpha=alpha, intercept=intercept,
                  brier=brier, market_brier=market_brier, log_loss=loss, market_log_loss=market_loss,
                  training_hash=hashlib.sha256(json.dumps(train, default=str).encode()).hexdigest(),
                  validation_hash=hashlib.sha256(json.dumps(valid, default=str).encode()).hexdigest())
    return report


def choose(row, profile, rules, now):
    if profile.get("status") != "validated":
        return None, "calibration_not_validated"
    values = anchor(row, rules["point"])
    if values is None:
        return None, "no_fixed_anchor"
    evidence = row.get("evidence") or {}
    sample, weight = evidence.get("best_team_sample"), evidence.get("supported_weight_share")
    if (not isinstance(sample, (int, float)) or isinstance(sample, bool)
            or not math.isfinite(sample) or sample < rules["min_team_sample"]
            or not isinstance(weight, (int, float)) or isinstance(weight, bool) or not math.isfinite(weight)
            or not rules["min_supported_weight"] <= weight <= 1):
        return None, "insufficient_sporting_evidence"
    raw, market = values
    forecasts = [row["anchor"][k] for k in MODELS]
    if max(forecasts) - min(forecasts) > rules["max_model_spread"]:
        return None, "model_disagreement"
    p = prediction(raw, market, profile["alpha"], profile["intercept"])
    if abs(p - market) > rules["max_market_gap"]:
        return None, "market_disagreement"
    side, chance = ("over", p) if p >= .5 else ("under", 1 - p)
    if chance < rules["min_probability"]:
        return None, "probability_below_threshold"
    lines = [line for line in row.get("lines", []) if line.get("point") == rules["point"]]
    if len(lines) != 1:
        return None, "no_fixed_line"
    books = {}
    for book in lines[0].get("books", []):
        name, quotes = normalize_bookmaker_name(book.get("book", "")), book.get("quotes") or {}
        if not name or set(quotes) != {"over", "under"}:
            continue
        if any(not isinstance(q.get("price"), (int, float)) or not math.isfinite(q["price"]) or q["price"] <= 1
               or (at := parse_time(q.get("observed_at"))) is None
               or not -timedelta(seconds=5) <= now - at <= timedelta(minutes=15) for q in quotes.values()):
            continue
        margin = sum(1 / q["price"] for q in quotes.values())
        if not 1 <= margin <= 1.2:
            continue
        if name in books:
            return None, "duplicate_book_identity"
        books[name] = quotes
    if len(books) < 2:
        return None, "two_fresh_books_required"
    actual_market = mean((1 / q["over"]["price"]) / sum(1 / v["price"] for v in q.values()) for q in books.values())
    if abs(actual_market - market) > .001:
        return None, "anchor_quote_mismatch"
    # A fixed stress margin, not a confidence interval or a guarantee.
    stressed = max(0, chance - rules["probability_stress"])
    options = []
    for name, quotes in books.items():
        odds = quotes[side]["price"]
        if rules["min_odds"] <= odds <= rules["max_odds"] and stressed * odds - 1 >= rules["min_stressed_ev"]:
            options.append({"book": name, "odds": odds, "point": rules["point"], "side": side,
                            "probability": chance, "stressed_probability": stressed,
                            "stressed_ev": stressed * odds - 1, "quote_observed_at": quotes[side]["observed_at"]})
    return (max(options, key=lambda p: (p["odds"], p["book"])), "selected") if options else (None, "no_robust_value")


def run(history, now):
    rules = policy()
    baseline = history.data.get("prospective_validation") or {}
    if not baseline.get("code_hash"):
        return {"status": "baseline_unavailable", "live_ready": False}
    code_hash = fingerprint(rules, baseline["code_hash"])
    state = history.data.get("conservative_totals")
    if not state or state["model_hash"] != code_hash:
        if state:
            history.data.setdefault("previous_conservative_totals", []).append(deepcopy(state))
        state = {"protocol": rules["protocol"], "model_hash": code_hash, "declared_at": now.isoformat(),
                 "rules": deepcopy(rules), "baseline_model_hash": baseline["code_hash"], "observations": []}
        history.data["conservative_totals"] = state
    sources = history.data.get("priced_totals_trials", [])
    by_id = {r["id"]: r for r in sources}
    # Revalidate even settled observations; conflicting upstream results remove
    # their provisional score, never their original forecast or selection.
    for frozen in state["observations"]:
        source = by_id.get(frozen["source_id"], {})
        if (same_fixture(frozen, source) and source.get("created_at") == frozen["created_at"]
                and verified_result(source, now)):
            frozen.update(status="resolved", total=source["total"], resolved_at=source["resolved_at"])
        else:
            frozen["status"] = "pending"
            for key in ("total", "resolved_at"):
                frozen.pop(key, None)
    profile = fit(sources, now, rules, baseline["code_hash"])
    new = 0
    for source in sorted(sources, key=lambda r: r.get("created_at", "")):
        created, kickoff = parse_time(source.get("created_at")), parse_time(source.get("kickoff"))
        if (created is None or kickoff is None or created < parse_time(state["declared_at"])
                or not -timedelta(seconds=5) <= now - created <= timedelta(minutes=15)
                or not timedelta(minutes=30) <= kickoff - now <= timedelta(hours=4)
                or source.get("status") != "pending" or source.get("protocol") != "priced-totals-v1"
                or source.get("model_hash") != baseline["code_hash"]
                or any(same_fixture(source, old) for old in state["observations"])):
            continue
        pick, reason = choose(source, profile, rules, now)
        values = anchor(source, rules["point"])
        forecast = None
        if values is not None and profile.get("status") == "validated":
            raw, market = values
            forecast = {"over": prediction(raw, market, profile["alpha"], profile["intercept"]), "market": market}
        state["observations"].append({"source_id": source["id"], "home": source["home"], "away": source["away"],
                                      "kickoff": source["kickoff"], "created_at": source["created_at"],
                                      "frozen_at": now.isoformat(),
                                      "model_hash": code_hash, "calibration": deepcopy(profile),
                                      "forecast": forecast, "pick": pick, "reason": reason, "status": "pending"})
        new += 1
    chosen = [r for r in state["observations"] if r["pick"]]
    settled = [r for r in chosen if r["status"] == "resolved"]
    pnl, wins, losses = 0., 0, 0
    for row in settled:
        pick = row["pick"]
        won = (row["total"] > pick["point"]) == (pick["side"] == "over")
        wins += won
        losses += not won
        pnl += pick["odds"] - 1 if won else -1
    paired = [r for r in state["observations"] if r["status"] == "resolved" and r.get("forecast")]
    history.save()
    return {"protocol": state["protocol"], "model_hash": code_hash, "declared_at": state["declared_at"],
            "mode": "paper_only", "live_ready": False, "automatic_live_promotion": False,
            "observed": len(state["observations"]), "new": new, "selected": len(chosen),
            "settled": len(settled), "pending": len(chosen) - len(settled), "wins": wins, "losses": losses,
            "paper_pnl": round(pnl, 4), "roi_pct": 100 * pnl / len(settled) if settled else None,
            "hit_rate_pct": 100 * wins / len(settled) if settled else None,
            "paired_count": len(paired),
            "brier": mean((r["forecast"]["over"] - (r["total"] > rules["point"])) ** 2 for r in paired) if paired else None,
            "market_brier": mean((r["forecast"]["market"] - (r["total"] > rules["point"])) ** 2 for r in paired) if paired else None,
            "abstentions": dict(Counter(r["reason"] for r in state["observations"] if not r["pick"])),
            "calibration": profile, "rules": rules}
