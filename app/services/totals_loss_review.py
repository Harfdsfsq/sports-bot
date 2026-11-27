"""Compare frozen opponent picks with a market-supported subset in paper mode."""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import timedelta
from statistics import mean

from app.services.conservative_totals import same_fixture, verified_result
from app.services.daily_match_registry import parse_time, read_json
from app.services.totals_strategy_clv import PATH as CLOSING_QUOTES_PATH
from app.services.totals_strategy_lab import PROTOCOL as SOURCE_PROTOCOL
from app.utils import normalize_bookmaker_name

PROTOCOL = "market-supported-totals-v1"
MIN_REFERENCE_PROBABILITY = 0.5


def fresh_price(quote, created):
    price = quote.get("price")
    observed = parse_time(quote.get("observed_at"))
    if (
        isinstance(price, (int, float))
        and math.isfinite(price)
        and price > 1
        and observed is not None
        and -timedelta(seconds=5) <= created - observed <= timedelta(minutes=15)
    ):
        return price
    return None


def assess(row):
    """Use only the quote snapshot recorded before kickoff."""
    pick = (row.get("picks") or {}).get("opponent")
    created = parse_time(row.get("created_at"))
    kickoff = parse_time(row.get("kickoff"))
    if not pick or created is None or kickoff is None or not timedelta(minutes=30) <= kickoff - created <= timedelta(hours=4):
        return None
    point, side = pick.get("point"), pick.get("side")
    if (
        not isinstance(point, (int, float))
        or not 1.5 <= point <= 5.5
        or not (point * 2).is_integer()
        or side not in {"over", "under"}
    ):
        return None
    picked_book = normalize_bookmaker_name(pick.get("book") or "")
    price = fresh_price({"price": pick.get("odds"), "observed_at": pick.get("quote_observed_at")}, created)
    if not picked_book or price is None or not 1.5 <= price <= 3.2:
        return None

    receipt = {"point": point, "side": side, "odds": price, "book": picked_book}
    lines = [line for line in row.get("lines", []) if line.get("point") == point]
    if len(lines) != 1:
        return {**receipt, "reason": "missing_exact_line"}
    books = {}
    seen = set()
    for book in lines[0].get("books", []):
        name = normalize_bookmaker_name(book.get("book") or "")
        if not name:
            continue
        if name in seen:
            return {**receipt, "reason": "duplicate_book_identity"}
        seen.add(name)
        quotes = book.get("quotes") or {}
        over = fresh_price(quotes.get("over") or {}, created)
        under = fresh_price(quotes.get("under") or {}, created)
        if over is None or under is None:
            continue
        margin = 1 / over + 1 / under
        if 1 <= margin <= 1.2:
            books[name] = (1 / (over if side == "over" else under) / margin, quotes[side])
    if picked_book not in books:
        return {**receipt, "reason": "missing_selected_book_pair"}
    _, original = books.pop(picked_book)
    if original["price"] != price or parse_time(original["observed_at"]) != parse_time(pick["quote_observed_at"]):
        return {**receipt, "reason": "selected_quote_mismatch"}
    if not books:
        return {**receipt, "reason": "missing_reference_book"}
    reference = mean(p for p, _ in books.values())
    return {
        **receipt,
        "reference_probability": reference,
        "reference_books": sorted(books),
        "reason": "selected" if reference >= MIN_REFERENCE_PROBABILITY else "market_against_pick",
    }


def outcomes(settled):
    counts = Counter()
    pnl = 0.0
    gross_winning_payout = 0.0
    for pick, total in settled:
        delta = (total - pick["point"]) * (1 if pick["side"] == "over" else -1)
        if delta > 0:
            counts["wins"] += 1
            pnl += pick["odds"] - 1
            gross_winning_payout += pick["odds"]
        elif delta < 0:
            counts["losses"] += 1
            pnl -= 1
        else:
            counts["pushes"] += 1
    decisive = counts["wins"] + counts["losses"]
    return {
        **dict.fromkeys(("wins", "losses", "pushes"), 0),
        **counts,
        "settled": len(settled),
        "paper_pnl": round(pnl, 4),
        "roi_pct": 100 * pnl / len(settled) if settled else None,
        "roi_at_lower_entry_odds_pct": {
            str(decrease): 100 * (pnl - gross_winning_payout * decrease / 100) / len(settled) if settled else None
            for decrease in (0.5, 1.0)
        },
        "hit_rate_pct_excluding_pushes": 100 * counts["wins"] / decisive if decisive else None,
    }


def compare(settled):
    baseline = outcomes(settled)
    filtered = outcomes([(p, g) for p, g in settled if p["reason"] == "selected"])
    return {
        "baseline": baseline,
        "filtered": filtered,
        "losses_avoided": baseline["losses"] - filtered["losses"],
        "wins_skipped": baseline["wins"] - filtered["wins"],
        "pushes_skipped": baseline["pushes"] - filtered["pushes"],
    }


def closing_price_change(decision, receipt):
    pick = decision["pick"]
    at = parse_time(receipt.get("reference_at"))
    kickoff = parse_time(decision["kickoff"])
    created = parse_time(decision["created_at"])
    price = receipt.get("reference_price")
    if (
        receipt.get("status") == "measured"
        and parse_time(receipt.get("kickoff")) == kickoff
        and parse_time(receipt.get("created_at")) == created
        and at and created and kickoff and created < at < kickoff
        and kickoff - at <= timedelta(minutes=45)
        and normalize_bookmaker_name(receipt.get("book") or "") == pick["book"]
        and receipt.get("point") == pick["point"] and receipt.get("side") == pick["side"]
        and isinstance(price, (int, float)) and math.isfinite(price) and price > 1
    ):
        return (pick["odds"] / price - 1) * 100
    return None


def run(history, now, *, closing_quotes=None):
    baseline = history.data.get("prospective_validation") or {}
    cohort, model_hash = baseline.get("cohort_id"), baseline.get("code_hash")
    if not cohort or not model_hash:
        return {"status": "baseline_unavailable", "mode": "paper_only"}
    state = history.data.get("totals_loss_review")
    if not state or (state["protocol"], state["source_cohort"], state["source_model_hash"]) != (PROTOCOL, cohort, model_hash):
        if state:
            history.data.setdefault("previous_totals_loss_reviews", []).append(deepcopy(state))
        state = {
            "protocol": PROTOCOL,
            "source_cohort": cohort,
            "source_model_hash": model_hash,
            "declared_at": now.isoformat(),
            "decisions": {},
        }
        history.data["totals_loss_review"] = state
    declared = parse_time(state["declared_at"])
    decisions = state["decisions"]
    rows = [
        row for row in history.data.get("priced_totals_trials", [])
        if row.get("protocol") == SOURCE_PROTOCOL
        and row.get("validation_cohort_id") == cohort
        and row.get("model_hash") == model_hash
    ]
    sources = defaultdict(list)
    for row in rows:
        sources[row["id"]].append(row)
    new = 0
    for row in sorted(rows, key=lambda row: row.get("created_at", "")):
        if row["id"] in decisions or len(sources[row["id"]]) != 1 or row.get("status") != "pending":
            continue
        created, kickoff = parse_time(row.get("created_at")), parse_time(row.get("kickoff"))
        if (
            created is None or kickoff is None or created < declared
            or not timedelta(0) <= now - created <= timedelta(minutes=15)
            or not timedelta(minutes=30) <= kickoff - now <= timedelta(hours=4)
            or any(same_fixture(row, old) for old in decisions.values())
        ):
            continue
        pick = assess(row)
        if pick is None:
            continue
        decisions[row["id"]] = {
            "created_at": row["created_at"], "kickoff": row["kickoff"],
            "home": row["home"], "away": row["away"], "frozen_at": now.isoformat(),
            "pick": pick,
        }
        new += 1

    settled = []
    measurements = {"baseline": [], "filtered": []}
    pending_reasons = Counter()
    closing_quotes = read_json(CLOSING_QUOTES_PATH, {}) if closing_quotes is None else closing_quotes
    for key, decision in decisions.items():
        source = sources.get(key, [])
        selected = decision["pick"]["reason"] == "selected"
        if len(source) != 1 or any(source[0].get(field) != decision[field]
                                   for field in ("created_at", "kickoff", "home", "away")):
            if selected:
                pending_reasons["missing_or_ambiguous_source"] += 1
            continue
        row = source[0]
        if verified_result(row, now):
            settled.append((decision["pick"], row["total"]))
            change = closing_price_change(decision, closing_quotes.get(key + "|opponent") or {})
            if change is not None:
                measurements["baseline"].append(change)
                if selected:
                    measurements["filtered"].append(change)
        elif selected:
            kickoff = parse_time(decision["kickoff"])
            reason = "awaiting_match_or_result"
            if kickoff and now - kickoff >= timedelta(hours=2):
                reason = row.get("resolution_reason") or "awaiting_verified_score"
                status = str(row.get("provider_result_status", "")).lower()
                if reason == "result_not_found" and status in {"postponed", "cancelled", "abandoned", "not started"}:
                    reason = "provider_" + status.replace(" ", "_")
            pending_reasons[reason] += 1
    history.save()
    reasons = Counter(d["pick"]["reason"] for d in decisions.values())
    result = compare(settled)
    result["baseline"]["pending"] = len(decisions) - result["baseline"]["settled"]
    result["filtered"]["pending"] = reasons["selected"] - result["filtered"]["settled"]
    for group, values in measurements.items():
        count = result[group]["settled"]
        result[group]["closing_price"] = {
            "measured": len(values),
            "coverage_pct": 100 * len(values) / count if count else None,
            "mean_clv_pct": mean(values) if values else None,
        }
    return {
        "protocol": PROTOCOL, "source_cohort": cohort, "declared_at": state["declared_at"],
        "mode": "paper_only", "status": "collecting", "new": new, "observed": len(decisions),
        "min_reference_probability": MIN_REFERENCE_PROBABILITY, "decisions": dict(reasons),
        "filtered_pending_reasons": dict(pending_reasons), **result,
    }
