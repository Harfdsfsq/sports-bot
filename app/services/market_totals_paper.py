"""Forward-only match total 2.5 experiment with a held-out execution book."""

from __future__ import annotations

import hashlib
import math
from collections import Counter, defaultdict
from datetime import timedelta
from pathlib import Path
from statistics import mean

from app.services.conservative_totals import probability, verified_result
from app.services.daily_match_registry import parse_time, read_json
from app.services.totals_loss_review import closing_price_change, outcomes
from app.services.totals_strategy_clv import PATH as QUOTE_PATH
from app.services.totals_strategy_clv import register
from app.services.totals_strategy_lab import calibrated, cluster_interval
from app.utils import normalize_bookmaker_name

PROTOCOL = "market-residual-totals25-v1"
MODEL = "market_residual_25"
REPORT_PATH = ".data/exports/latest-market-totals-paper.json"
POINT = 2.5
MIN_REFERENCE_BOOKS = 2
MIN_TEAM_SAMPLE = 6
MIN_PROBABILITY = 0.55
MIN_ODDS, MAX_ODDS = 1.50, 2.20
PRICE_STRESS, PROBABILITY_STRESS, MIN_STRESSED_EV = 0.01, 0.02, 0.01


def opportunity(row):
    """Choose the same quote for model and control using market information only."""
    created, kickoff = parse_time(row.get("created_at")), parse_time(row.get("kickoff"))
    if not created or not kickoff or not timedelta(minutes=30) <= kickoff - created <= timedelta(hours=4):
        return None
    lines = [line for line in row.get("lines", []) if line.get("point") == POINT]
    if len(lines) != 1:
        return None
    line = lines[0]
    books = {}
    for book in line.get("books", []):
        name = normalize_bookmaker_name(book.get("book") or "")
        quotes = book.get("quotes") or {}
        if not name or set(quotes) != {"over", "under"}:
            continue
        prices = {side: q.get("price") for side, q in quotes.items()}
        stamps = {side: parse_time(q.get("observed_at")) for side, q in quotes.items()}
        if any(
            not isinstance(price, (int, float)) or not math.isfinite(price) or not 1 < price < 100
            for price in prices.values()
        ) or not all(stamp and timedelta(0) <= created - stamp <= timedelta(minutes=15) for stamp in stamps.values()):
            continue
        if max(stamps.values()) - min(stamps.values()) > timedelta(seconds=60):
            continue
        margin = sum(1 / price for price in prices.values())
        if not 1 <= margin <= 1.2:
            continue
        if name in books:
            return None
        books[name] = (prices, stamps, (1 / prices["over"]) / margin)
    if len(books) <= MIN_REFERENCE_BOOKS:
        return None
    oldest_quote = min(stamp for _, stamps, _ in books.values() for stamp in stamps.values())
    options = []
    for name, (prices, stamps, _) in books.items():
        reference = {other: packet[2] for other, packet in books.items() if other != name}
        market = mean(reference.values())
        for side, price in prices.items():
            if MIN_ODDS <= price <= MAX_ODDS:
                chance = market if side == "over" else 1 - market
                options.append({
                    "book": name, "odds": price, "side": side, "point": POINT, "market_over": market,
                    "reference_books": sorted(reference), "quote_observed_at": stamps[side].isoformat(),
                    "market_ev": chance * price - 1,
                })
    if not options:
        return None
    choice = max(options, key=lambda option: (option["market_ev"], -option["odds"], option["book"], option["side"]))
    raw = (line.get("predictions") or {}).get("shrunk", {}).get("over")
    if not probability(raw):
        return None
    return {**choice, "sporting_over": raw, "oldest_quote_at": oldest_quote.isoformat()}


def fit(examples):
    """Fit sport weight on older results and test it on a later, untouched slice."""
    report = {"status": "insufficient_history", "alpha": 0.0, "intercept": 0.0, "count": len(examples)}
    if len(examples) < 160:
        return report
    ordered = sorted(examples)
    boundary = ordered[int(len(ordered) * 0.75)][0]
    train = [r for r in ordered if r[1] < boundary]
    valid = [r for r in ordered if r[0] >= boundary]
    report.update(train_count=len(train), validation_count=len(valid), split_at=boundary.isoformat())
    if len(train) < 100 or len(valid) < 40:
        return report

    def score(rows, alpha):
        errors = []
        for _, _, _, sporting, market, y in rows:
            p = calibrated(sporting, market, {"alpha": alpha, "intercept": 0.0})
            errors.append((-math.log(p if y else 1 - p), (p - y) ** 2))
        return mean(x[0] for x in errors), mean(x[1] for x in errors)

    alpha = min((0.0, 0.10, 0.25, 0.50), key=lambda x: score(train, x)[0] + .004 * x * x)
    loss, brier = score(valid, alpha)
    market_loss, market_brier = score(valid, 0.0)
    useful = alpha > 0 and loss < market_loss - .0005 and brier < market_brier
    return {
        **report, "status": "validated_correction" if useful else "market_fallback", "alpha": alpha if useful else 0.0,
        "proposed_alpha": alpha, "validation_log_loss": loss, "market_log_loss": market_loss,
        "validation_brier": brier, "market_brier": market_brier,
    }


def selection(quote, over):
    chance = over if quote["side"] == "over" else 1 - over
    ev = max(0.0, chance - PROBABILITY_STRESS) * quote["odds"] * (1 - PRICE_STRESS) - 1
    if chance < MIN_PROBABILITY or ev < MIN_STRESSED_EV:
        return None
    return {**quote, "probability": chance, "stressed_ev": ev}


def run(history, now, *, capture_started_at=None):
    data = history.data
    baseline = data.get("prospective_validation", {})
    base_hash = baseline.get("code_hash")
    if not base_hash:
        return {"protocol": PROTOCOL, "mode": "paper_only", "status": "baseline_unavailable"}
    fingerprint = hashlib.sha256(Path(__file__).read_bytes() + base_hash.encode()).hexdigest()
    state = data.get("market_totals_paper")
    if not state or state["model_hash"] != fingerprint:
        if state:
            data.setdefault("previous_market_totals_paper", []).append(state)
        state = {"model_hash": fingerprint, "declared_at": (capture_started_at or now).isoformat(), "decisions": {}}
        data["market_totals_paper"] = state
    declared_at = parse_time(state["declared_at"])
    decisions = state["decisions"]
    source_rows = defaultdict(list)
    for row in data.get("priced_totals_trials", []):
        if row.get("protocol") == "priced-totals-v1" and row.get("model_hash") == base_hash:
            source_rows[row["id"]].append(row)
    examples = []
    for key, rows in source_rows.items():
        if len(rows) != 1 or not verified_result(rows[0], now):
            continue
        row = rows[0]
        quote = opportunity(row)
        if quote:
            examples.append((
                parse_time(row["created_at"]), parse_time(row["resolved_at"]), key,
                quote["sporting_over"], quote["market_over"], float(row["total"] > POINT),
            ))
    profile = fit(examples)
    new, registrations = 0, []
    for key, rows in source_rows.items():
        if len(rows) != 1 or key in decisions:
            continue
        row = rows[0]
        created, kickoff = parse_time(row.get("created_at")), parse_time(row.get("kickoff"))
        if (row.get("status") != "pending" or not created or not kickoff or created < declared_at
                or not timedelta(0) <= now - created <= timedelta(minutes=15)
                or not timedelta(minutes=30) <= kickoff - now <= timedelta(hours=4)):
            continue
        quote = opportunity(row)
        if quote and now - parse_time(quote["oldest_quote_at"]) > timedelta(minutes=15):
            quote = None
        evidence = row.get("evidence") or {}
        sample, weight = evidence.get("best_team_sample"), evidence.get("supported_weight_share")
        supported = (isinstance(sample, (int, float)) and sample >= MIN_TEAM_SAMPLE
                     and isinstance(weight, (int, float)) and .6 <= weight <= 1)
        decision = {k: row[k] for k in ("home", "away", "kickoff", "created_at", "match_key")}
        decision.update(frozen_at=now.isoformat(), pick=None, market_pick=None, forecast=None)
        if not quote:
            decision["reason"] = "missing_independent_quote"
        elif not supported:
            decision["reason"] = "thin_sporting_evidence"
        else:
            market = quote["market_over"]
            over = calibrated(quote["sporting_over"], market, profile)
            decision.update(forecast={"over": over, "market": market}, alpha=profile["alpha"])
            decision["market_pick"] = selection(quote, market)
            decision["pick"] = selection(quote, over)
            decision["reason"] = "selected" if decision["pick"] else "stressed_edge_below_floor"
            if decision["pick"]:
                registrations.append({**decision, "id": key + "-" + fingerprint[:12],
                                      "picks": {MODEL: decision["pick"]}})
        decisions[key] = decision
        new += 1
    if registrations:
        register(registrations, now)
    history.save()
    results, control, scores, closing = [], [], [], []
    days = defaultdict(list)
    journal = read_json(QUOTE_PATH, {})
    for key, decision in decisions.items():
        source = source_rows.get(key, [])
        if len(source) != 1:
            continue
        row = source[0]
        if not verified_result(row, now) or any(row.get(k) != decision[k] for k in ("home", "away", "kickoff", "created_at")):
            continue
        total = row["total"]
        if decision["forecast"]:
            y = float(total > POINT)
            scores.append(tuple((decision["forecast"][k] - y) ** 2 for k in ("over", "market")))
        if decision["market_pick"]:
            control.append((decision["market_pick"], total))
        pick = decision["pick"]
        if pick:
            results.append((pick, total))
            won = (total > POINT) == (pick["side"] == "over")
            days[decision["kickoff"][:10]].append(pick["odds"] - 1 if won else -1)
            value = closing_price_change(decision, journal.get(key + "-" + fingerprint[:12] + "|" + MODEL, {}))
            if value is not None:
                closing.append(value)
    selected = sum(bool(d["pick"]) for d in decisions.values())
    return {
        "protocol": PROTOCOL, "mode": "paper_only", "live_ready": False, "model_hash": fingerprint,
        "declared_at": state["declared_at"], "observed": len(decisions), "new": new, "selected": selected,
        "pending": selected - len(results), "challenger": outcomes(results), "market_control": outcomes(control),
        "paired_count": len(scores), "brier": mean(x[0] for x in scores) if scores else None,
        "market_brier": mean(x[1] for x in scores) if scores else None, "roi_interval_95": cluster_interval(days),
        "closing_price": {"measured": len(closing), "mean_clv_pct": mean(closing) if closing else None},
        "calibration": profile, "decisions": dict(Counter(d["reason"] for d in decisions.values())),
    }
