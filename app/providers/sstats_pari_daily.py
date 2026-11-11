"""Bounded native PARI quotes: current pre-match prices, never closing history."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import httpx

from app.providers.sstats_pari_parser import extract_list, parse_offers, unwrap
from app.services.api_budget import BudgetedAsyncClient
from app.services.fixture_identity import direct_fixture_identity


def info(row):
    return row.get("matchInfo", row) if isinstance(row, dict) else {}


def identity(row):
    r = info(row)

    def name(v):
        return v.get("name") if isinstance(v, dict) else v

    return {"home": name(r.get("homeTeam")), "away": name(r.get("awayTeam")), "commence_time": r.get("startDate")}


def prematch(row):
    r = info(row)
    return r.get("status") in {"NotStarted", "WillStartSoon"} and not r.get("isDeleted")


class SStatsPariDailyProvider:
    def __init__(self, settings):
        self.api_key = str(getattr(settings, "sstats_api_key", "") or "")
        self.used = 0
        self.limit = min(16, max(0, int(os.getenv("DAILY_PARI_REQUEST_LIMIT", "12"))))

    async def confirm_offers(self, matches):
        return await self.fetch_offers(matches, confirmation=True)

    async def fetch_offers(self, matches, *, confirmation=False):
        stats = {
            "enabled": os.getenv("DAILY_PARI_ENABLED", "false").lower() == "true",
            "api_key_present": bool(self.api_key),
            "requests": 0,
            "response_errors": 0,
            "fixtures_matched": 0,
            "offers_parsed": 0,
            "skip_reasons": {},
        }
        result = {}
        if not stats["enabled"] or not self.api_key or not self.limit:
            return result, stats, {}
        now = datetime.now(UTC)
        targets = [m for m in matches if timedelta(minutes=30) <= m.commence_time - now <= timedelta(hours=4)]
        targets.sort(key=lambda m: m.commence_time)
        blocked = False
        ceiling = self.limit if confirmation or self.limit <= 4 else self.limit - 4

        def skip(reason):
            stats["skip_reasons"][reason] = stats["skip_reasons"].get(reason, 0) + 1

        async with BudgetedAsyncClient(timeout=8) as client:

            async def get(path, params=None):
                nonlocal blocked
                if blocked or self.used >= ceiling:
                    stats["budget_exhausted"] = self.used >= ceiling
                    return None, None
                self.used += 1
                stats["requests"] += 1
                try:
                    response = await client.get(
                        "https://api.sstats.net" + path, params={**(params or {}), "apikey": self.api_key}
                    )
                    stats.setdefault("http_statuses", []).append(response.status_code)
                    if response.status_code in {401, 402, 403, 429}:
                        blocked = True
                        stats["blocked_status"] = response.status_code
                    if response.status_code != 200:
                        stats["response_errors"] += 1
                        return None, None
                    return response.json(), datetime.now(UTC)
                except (TimeoutError, OSError, ValueError, httpx.HTTPError) as exc:
                    stats["response_errors"] += 1
                    stats["last_error_type"] = type(exc).__name__
                    return None, None

            rows = []
            for day in sorted({m.commence_time.date().isoformat() for m in targets}):
                offset, seen = 0, set()
                for _ in range(4):
                    payload, _ = await get(
                        "/Pari/matches",
                        {
                            "date": day,
                            "timezone": 0,
                            "upcoming": "true",
                            "includeOdds": "false",
                            "limit": 1000,
                            "offset": offset,
                        },
                    )
                    if payload is None:
                        break
                    page = extract_list(payload)
                    signature = tuple(str(info(r).get("eventId")) for r in page)
                    if not page or signature in seen:
                        break
                    seen.add(signature)
                    rows.extend(page)
                    offset += len(page)
                    # Documented schema capitalizes TotalCount. Do not trust count,
                    # which describes only this page, or a server-capped short page.
                    total = payload.get("TotalCount", payload.get("totalCount")) if isinstance(payload, dict) else None
                    try:
                        if total is not None and offset >= int(total):
                            break
                    except (ValueError, TypeError):
                        skip("invalid_total_count")
                        break
            selected = []
            for match in targets:
                found = [
                    r for r in rows if prematch(r) and direct_fixture_identity(match, identity(r), tolerance_hours=0.25)
                ]
                if len(found) != 1:
                    skip("no_unique_fixture")
                    continue
                selected.append((match, found[0]))
            for match, event in selected[:8]:
                sid = str(info(event).get("eventId") or "")
                if not sid.isdecimal() or int(sid) <= 0:
                    skip("invalid_event_id")
                    continue
                payload, observed = await get("/Pari/match/" + sid)
                detail = unwrap(payload)
                if (
                    not isinstance(detail, dict)
                    or str(info(detail).get("eventId")) != sid
                    or not prematch(detail)
                    or not direct_fixture_identity(match, identity(detail), tolerance_hours=0.25)
                ):
                    skip("detail_identity_or_status")
                    continue
                quotes = detail.get("currentOdds")
                if not isinstance(quotes, list):
                    skip("no_current_odds")
                    continue
                stats["fixtures_matched"] += 1
                parsed = parse_offers(
                    [q for q in quotes if isinstance(q, dict) and not q.get("isBlocked") and not q.get("isDeleted")],
                    match,
                    sid,
                )
                # Parser names are supplementary; explicit provider period fields
                # must also pass the native full-match contract.
                from app.services.market_period import is_full_match_market

                raw = {str(q.get("id")): q for q in quotes if isinstance(q, dict)}
                parsed = [o for o in parsed if is_full_match_market(raw.get(str(o.metadata.get("raw_outcome_id")), {}))]
                for offer in parsed:
                    offer.metadata.update(
                        fetched_at_utc=observed.isoformat(), current_prematch=True, independent_odds_source=False
                    )
                if parsed:
                    result[match.match_key] = parsed
                    stats["offers_parsed"] += len(parsed)
        return result, stats, {}
