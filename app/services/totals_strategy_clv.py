"""Isolated closing-quote journal for priced experiments, never live learning."""

from datetime import timedelta
from statistics import mean

from app.services.daily_match_registry import parse_time, read_json, write_json
from app.services.market_period import is_full_match_market

PATH = ".data/daily_quality/priced-totals-clv.json"


def register(rows, now):
    journal = read_json(PATH, {})
    for row in rows:
        if parse_time(row["kickoff"]) <= now:
            continue
        for name, pick in row["picks"].items():
            key = row["id"] + "|" + name
            if pick and key not in journal:
                journal[key] = {
                    **pick,
                    "model": name,
                    "match_key": row["match_key"],
                    "kickoff": row["kickoff"],
                    "created_at": row["created_at"],
                    "status": "pending",
                }
    write_json(PATH, journal)


def requested():
    return {
        (r["match_key"], parse_time(r["kickoff"])) for r in read_json(PATH, {}).values() if r["status"] == "pending"
    }


def update(matches, offers, now):
    journal = read_json(PATH, {})
    actual = {(m.match_key, m.commence_time) for m in matches}
    for row in journal.values():
        if row["status"] != "pending":
            continue
        kickoff = parse_time(row["kickoff"])
        if (row["match_key"], kickoff) in actual and timedelta(0) < kickoff - now <= timedelta(minutes=45):
            quotes = []
            for offer in offers.get(row["match_key"], []):
                at = parse_time(offer.metadata.get("fetched_at_utc"))
                if (
                    offer.family == "totals"
                    and offer.point == row["point"]
                    and is_full_match_market(offer)
                    and offer.bookmaker.strip().lower() == row["book"]
                    and offer.selection.lower() == row["side"]
                    and at
                    and parse_time(row["created_at"]) < at <= now < kickoff
                    and kickoff - at <= timedelta(minutes=45)
                    and now - at <= timedelta(seconds=60)
                    and 1 < offer.price < 100
                ):
                    quotes.append((offer.price, at))
            if quotes:
                price, observed = min(quotes)
                row.update(reference_price=price, reference_at=observed.isoformat())
        if now >= kickoff:
            if row.get("reference_price"):
                row.update(status="measured", clv_pct=(row["odds"] / row["reference_price"] - 1) * 100)
            else:
                row.update(status="missing_quote")
    write_json(PATH, journal)
    return summary(journal)


def summary(journal=None):
    journal = read_json(PATH, {}) if journal is None else journal
    measured = [r["clv_pct"] for r in journal.values() if r["status"] == "measured"]
    return {
        "measured": len(measured),
        "pending": sum(r["status"] == "pending" for r in journal.values()),
        "missing": sum(r["status"] == "missing_quote" for r in journal.values()),
        "average_pct": mean(measured) if measured else None,
        "population": "priced_totals_only",
    }
