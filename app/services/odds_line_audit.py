"""Bounded metadata-only investigation of empty bookmaker snapshots."""

from __future__ import annotations

import fcntl
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.services.daily_match_registry import parse_time, read_json, write_json
from app.services.fixture_identity import direct_fixture_identity


def quote_identity_reason(payload, match, event_id):
    if str(payload.get("id")) != str(event_id):
        return "event_id_mismatch"
    if payload.get("status") != "pending":
        return "event_not_pending"
    if not direct_fixture_identity(match, {
        "home": payload.get("home"), "away": payload.get("away"),
        "commence_time": payload.get("date"),
    }, tolerance_hours=0.25):
        return "fixture_identity_mismatch"
    return None


def _reserve(event_id, now):
    """Reserve before I/O; failed requests count, including across processes."""
    path = Path(".data/daily_quality/odds-line-audit.json")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = read_json(path, {})
        state = state if isinstance(state, dict) else {}
        if not isinstance(state.get("receipts"), dict):
            state["receipts"] = {}
        receipt = state["receipts"].get(str(event_id), {})
        receipt = receipt if isinstance(receipt, dict) else {}
        observed = parse_time(receipt.get("observed_at"))
        if observed and timedelta(0) <= now - observed < timedelta(minutes=30):
            return receipt, False
        if state.get("day") != now.date().isoformat():
            state["day"], state["used"] = now.date().isoformat(), 0
        if not isinstance(state.get("used"), int) or isinstance(state.get("used"), bool):
            return None, False
        if state.get("used", 0) >= 16:
            return None, False
        state["used"] = state.get("used", 0) + 1
        write_json(path, state)
        return None, True


def _save(event_id, receipt):
    path = Path(".data/daily_quality/odds-line-audit.json")
    with path.with_suffix(".lock").open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        state = read_json(path, {})
        state = state if isinstance(state, dict) else {}
        if not isinstance(state.get("receipts"), dict):
            state["receipts"] = {}
        state["receipts"][str(event_id)] = receipt
        state["receipts"] = dict(list(state["receipts"].items())[-300:])
        write_json(path, state)


async def audit_empty_lines(provider, client, accounts, mapping, diagnostics, stats):
    """Never produce offers, change selected bookmakers or bypass plan limits."""
    report = stats["line_availability_audit"] = {"requests": 0, "cache_hits": 0, "reasons": {}}
    if not accounts:
        return
    account = accounts[0]
    for key, diagnostic in diagnostics.items():
        if diagnostic.get("reason") != "no_quotes_from_selected_books" or key not in mapping:
            continue
        match, event_id = mapping[key]["match"], mapping[key]["event"]["id"]
        now = datetime.now(UTC)
        if not timedelta(minutes=30) <= match.commence_time - now <= timedelta(hours=4):
            continue
        if report["requests"] >= 4:
            continue
        if not provider._request_budget_allows(stats, account_name=account["name"]):
            break
        receipt, reserved = _reserve(event_id, now)
        if receipt:
            receipt = dict(receipt)
            if receipt.get("fixture") and quote_identity_reason(receipt["fixture"], match, event_id):
                receipt.update(reason="cached_fixture_identity_mismatch", identity_verified=False)
            report["cache_hits"] += 1
        elif reserved:
            provider._record_request(account_name=account["name"])
            report["requests"] += 1
            receipt = {"event_id": event_id, "observed_at": now.isoformat()}
            try:
                response = await client.get(f"{provider.base_url}/events/{event_id}", params={"apiKey": account["api_key"]}, timeout=4)
                receipt["http_status"] = response.status_code
                if response.status_code == 200:
                    payload = response.json()
                    if not isinstance(payload, dict):
                        receipt["reason"] = "invalid_metadata_response"
                    else:
                        reason = quote_identity_reason(payload, match, event_id)
                        receipt["fixture"] = {k: payload.get(k) for k in ("id", "home", "away", "date", "status")}
                        count = payload.get("bookmakerCount")
                        receipt.update(status=payload.get("status"), identity_verified=reason is None)
                        if reason:
                            receipt["reason"] = reason
                        elif isinstance(count, int) and not isinstance(count, bool) and count >= 0:
                            receipt.update(bookmaker_count=count, reason="selected_books_gap" if count else "no_bookmakers_listed")
                        else:
                            receipt["reason"] = "bookmaker_count_unknown"
                else:
                    receipt["reason"] = "metadata_unavailable"
            except (httpx.HTTPError, ValueError, OSError):
                receipt["reason"] = "metadata_request_error"
            _save(event_id, receipt)
            # No repeated probes after auth, quota or service failure.
            if receipt.get("http_status") in {401, 403, 429} or receipt.get("http_status", 0) >= 500 or receipt["reason"] == "metadata_request_error":
                diagnostic["availability"] = receipt
                break
        if receipt:
            diagnostic["availability"] = receipt
            if receipt.get("http_status") in {401, 403, 429} or receipt.get("http_status", 0) >= 500:
                break
    report["reasons"] = dict(Counter(d["availability"]["reason"] for d in diagnostics.values() if d.get("availability")))
