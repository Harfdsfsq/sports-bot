from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
UTC = timezone.utc
from typing import Any

import httpx

from app.config import Settings
from app.schemas import Match, Offer
from app.utils import (
    is_simulated_or_esports_event,
    normalize_bookmaker_name,
    parse_datetime,
    score_event_match,
)


class OddsApiIoProvider:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = "https://api.odds-api.io/v3"
        self._bootstrap_events_cache: list[dict[str, Any]] = []
        self.max_http_requests = max(0, int(getattr(settings, "odds_api_io_per_run_max", 8) or 0))
        self._requests_used = 0


    async def fetch_matches(self) -> tuple[list[Match], dict[str, Any], dict[str, Any]]:
        stats: dict[str, Any] = {
            "enabled": True,
            "api_key_present": bool(getattr(self.settings, "odds_api_io_key", None)),
            "event_requests": 0,
            "response_errors": 0,
            "events_fetched": 0,
            "matches_built": 0,
            "low_tier_skipped": 0,
            "simulated_skipped": 0,
            "event_http_statuses": [],
            "payload_shapes": [],
            "last_body_preview": None,
            "rate_limited": False,
            "max_http_requests_per_run": self.max_http_requests,
            "budget_exhausted": False,
        }
        preview: dict[str, Any] = {"sample_events": [], "sample_matches": []}
        api_key = getattr(self.settings, "odds_api_io_key", None)
        if not api_key:
            return [], stats, preview

        now = datetime.now(UTC)
        days_ahead = max(1, int(getattr(self.settings, "run_days_ahead", 4) or 4))
        until = now + timedelta(days=days_ahead)
        timeout = float(getattr(self.settings, "odds_api_io_timeout_seconds", 25.0) or 25.0)
        max_pages = max(1, int(getattr(self.settings, "odds_api_io_max_pages_per_sport", 4) or 4))
        page_limit = max(1, int(getattr(self.settings, "odds_api_io_page_limit", 100) or 100))
        matches: list[Match] = []
        seen_ids: set[int] = set()
        cached_events: list[dict[str, Any]] = []
        async with httpx.AsyncClient(timeout=timeout) as client:
            for page in range(1, max_pages + 1):
                if not self._request_budget_allows(stats):
                    break
                params = {
                    "apiKey": api_key,
                    "sport": "football",
                    "status": "pending,live",
                    "from": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "to": until.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                    "limit": page_limit,
                    "page": page,
                }
                stats["event_requests"] += 1
                self._requests_used += 1
                try:
                    response = await client.get(f"{self.base_url}/events", params=params)
                except Exception as exc:
                    stats["response_errors"] += 1
                    stats["last_body_preview"] = f"events request failed: {exc}"
                    continue
                stats["event_http_statuses"].append(response.status_code)
                stats["last_body_preview"] = response.text[:1500]
                if response.status_code == 429:
                    stats["response_errors"] += 1
                    stats["rate_limited"] = True
                    break
                if response.status_code != 200:
                    stats["response_errors"] += 1
                    continue
                payload = self._safe_json(response)
                if payload is None:
                    stats["response_errors"] += 1
                    continue
                shape = self._payload_shape(payload)
                if shape not in stats["payload_shapes"]:
                    stats["payload_shapes"].append(shape)
                items = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
                if page == 1 and items:
                    preview["sample_events"] = items[:3]
                if not items:
                    break
                cached_events.extend(items)
                before = len(seen_ids)
                for raw in items:
                    event = self._parse_event(raw)
                    if event is None:
                        if isinstance(raw, dict) and is_simulated_or_esports_event(
                            str(raw.get("home") or ""),
                            str(raw.get("away") or ""),
                            str((raw.get("league") or {}).get("name") or ""),
                        ):
                            stats["simulated_skipped"] += 1
                        continue
                    event_id = int(event.get("id") or 0)
                    if event_id in seen_ids:
                        continue
                    seen_ids.add(event_id)
                    tier = "low" if self._looks_low_tier(event.get("league") or "") else "mid"
                    if tier == "low" and not bool(getattr(self.settings, "allow_low_tier", False)):
                        stats["low_tier_skipped"] += 1
                        continue
                    match = Match(
                        source="odds_api_io",
                        source_event_id=str(event_id),
                        sport_key="soccer",
                        league_name=str(event.get("league") or ""),
                        home_team=str(event.get("home") or ""),
                        away_team=str(event.get("away") or ""),
                        commence_time=event["commence_time"],
                        home_team_norm="",
                        away_team_norm="",
                        league_key="",
                        tier=tier,
                        metadata={
                            "odds_api_io_id": event_id,
                            "competition": event.get("league"),
                            "raw_event": raw,
                        },
                    )
                    matches.append(match)
                stats["events_fetched"] = len(seen_ids)
                stats["matches_built"] = len(matches)
                if len(seen_ids) == before or len(items) < page_limit:
                    break
        self._bootstrap_events_cache = list(cached_events)
        preview["sample_matches"] = [
            {
                "match_key": item.match_key,
                "league_name": item.league_name,
                "home_team": item.home_team,
                "away_team": item.away_team,
                "commence_time": item.commence_time.isoformat(),
                "tier": item.tier,
            }
            for item in matches[:5]
        ]
        return matches, stats, preview

    async def fetch_offers(self, matches: list[Match]) -> tuple[dict[str, list[Offer]], dict[str, Any], dict[str, Any]]:
        accounts = self._odds_accounts()
        stats: dict[str, Any] = {
            "enabled": True,
            "api_key_present": bool(accounts),
            "api_key_2_present": bool(getattr(self.settings, "odds_api_io_key_2", None)),
            "account2_missing": not bool(getattr(self.settings, "odds_api_io_key_2", None)),
            "event_requests": 0,
            "odds_requests": 0,
            "response_errors": 0,
            "events_fetched": 0,
            "events_matched": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "unmatched_offer_events": 0,
            "markets_parsed": 0,
            "offers_parsed": 0,
            "event_http_statuses": [],
            "odds_http_statuses": [],
            "payload_shapes": [],
            "bookmakers_seen": 0,
            "last_body_preview": None,
            "simulated_skipped": 0,
            "requested_bookmakers": None,
            "requested_bookmakers_by_account": [
                {"account": account["name"], "bookmakers": account["bookmakers"]}
                for account in accounts
            ],
            "accounts": {
                account["name"]: {
                    "bookmakers": account["bookmakers"],
                    "api_key_present": bool(account.get("api_key")),
                    "odds_requests": 0,
                    "response_errors": 0,
                    "offers_parsed": 0,
                    "events_matched": 0,
                    "rate_limited": False,
                    "http_statuses": [],
                }
                for account in accounts
            },
            "bootstrap_events_reused": 0,
            "rate_limited": False,
            "max_http_requests_per_run": self.max_http_requests,
            "budget_exhausted": False,
        }
        preview: dict[str, Any] = {"sample_events": [], "sample_odds": []}

        if not accounts:
            return {}, stats, preview
        event_account = accounts[0]
        api_key = str(event_account["api_key"])

        soccer_matches = [
            match
            for match in matches
            if match.sport_key == "soccer" and not is_simulated_or_esports_event(match.home_team, match.away_team, match.league_name)
        ]
        if not soccer_matches:
            return {}, stats, preview

        now = datetime.now(UTC)
        days_ahead = max(1, int(getattr(self.settings, "run_days_ahead", 4) or 4))
        until = now + timedelta(days=days_ahead)
        target_books = ",".join(account["bookmakers"] for account in accounts if account.get("bookmakers"))
        stats["requested_bookmakers"] = target_books

        events: list[dict[str, Any]] = []
        seen_event_ids: set[int] = set()
        timeout = float(getattr(self.settings, "odds_api_io_timeout_seconds", 25.0) or 25.0)
        max_pages = max(1, int(getattr(self.settings, "odds_api_io_max_pages_per_sport", 4) or 4))
        page_limit = max(1, int(getattr(self.settings, "odds_api_io_page_limit", 100) or 100))

        async with httpx.AsyncClient(timeout=timeout) as client:
            if self._bootstrap_events_cache:
                events = [row for row in self._bootstrap_events_cache if isinstance(row, dict)]
                seen_event_ids = {int(row.get("id") or 0) for row in events if int(row.get("id") or 0)}
                stats["bootstrap_events_reused"] = len(seen_event_ids)
                stats["events_fetched"] = len(seen_event_ids)
                if events:
                    preview["sample_events"] = events[:3]
            else:
                for page in range(1, max_pages + 1):
                    if not self._request_budget_allows(stats):
                        break
                    params = {
                        "apiKey": api_key,
                        "sport": "football",
                        "status": "pending,live",
                        "from": now.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                        "to": until.replace(microsecond=0).isoformat().replace("+00:00", "Z"),
                        "limit": page_limit,
                        "page": page,
                    }
                    stats["event_requests"] += 1
                    self._requests_used += 1
                    try:
                        response = await client.get(f"{self.base_url}/events", params=params)
                    except Exception as exc:
                        stats["response_errors"] += 1
                        stats["last_body_preview"] = f"events request failed: {exc}"
                        continue

                    stats["event_http_statuses"].append(response.status_code)
                    stats["last_body_preview"] = response.text[:1500]
                    if response.status_code == 429:
                        stats["response_errors"] += 1
                        stats["rate_limited"] = True
                        break
                    if response.status_code != 200:
                        stats["response_errors"] += 1
                        continue

                    payload = self._safe_json(response)
                    if payload is None:
                        stats["response_errors"] += 1
                        continue
                    shape = self._payload_shape(payload)
                    if shape not in stats["payload_shapes"]:
                        stats["payload_shapes"].append(shape)

                    items = [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
                    if page == 1 and items:
                        preview["sample_events"] = items[:3]
                    if not items:
                        break

                    before = len(seen_event_ids)
                    for item in items:
                        event_id = item.get("id")
                        if not isinstance(event_id, int):
                            continue
                        if event_id in seen_event_ids:
                            continue
                        seen_event_ids.add(event_id)
                        events.append(item)
                    stats["events_fetched"] = len(events)
                    if len(seen_event_ids) == before:
                        break
                    if len(items) < page_limit:
                        break
            mapping: dict[str, dict[str, Any]] = {}
            for raw_event in events:
                event = self._parse_event(raw_event)
                if event is None:
                    if isinstance(raw_event, dict) and is_simulated_or_esports_event(
                        str(raw_event.get("home") or ""),
                        str(raw_event.get("away") or ""),
                        str((raw_event.get("league") or {}).get("name") or ""),
                    ):
                        stats["simulated_skipped"] += 1
                    continue
                matched = self._match_event(event, soccer_matches)
                if matched is None:
                    stats["unmatched_offer_events"] += 1
                    continue
                existing = mapping.get(matched.match_key)
                if existing is None or float(event.get("match_score") or 0.0) > float(existing["event"].get("match_score") or 0.0):
                    mapping[matched.match_key] = {"match": matched, "event": event}

            stats["events_matched"] = len(mapping)
            for item in mapping.values():
                quality = item["event"].get("match_quality")
                if quality == "exact":
                    stats["matched_exact"] += 1
                elif quality == "loose":
                    stats["matched_loose"] += 1
                elif quality == "fuzzy":
                    stats["matched_fuzzy"] += 1

            matched_items = list(mapping.values())
            matched_items.sort(key=lambda row: self._match_priority(row["match"], now))
            max_fetch = max(1, int(getattr(self.settings, "max_matches_for_odds_fetch", 40) or 40))
            if len(matched_items) > max_fetch:
                matched_items = matched_items[:max_fetch]
                stats["matches_limited_to"] = max_fetch

            offers_by_match: dict[str, list[Offer]] = defaultdict(list)
            bookmakers_seen: set[str] = set()
            for start in range(0, len(matched_items), 10):
                chunk = matched_items[start : start + 10]
                if not chunk:
                    continue
                event_id_list = [int(item["event"]["id"]) for item in chunk]
                for account in accounts:
                    event_list = await self._fetch_odds_multi_chunk(
                        client,
                        str(account["api_key"]),
                        event_id_list,
                        str(account["bookmakers"]),
                        stats,
                        account_name=str(account["name"]),
                    )
                    if stats.get("rate_limited"):
                        break
                    if len(preview["sample_odds"]) < 2 and event_list:
                        preview["sample_odds"].append(event_list[:2])
                    for event_payload in event_list:
                        event_id = int(event_payload.get("id") or 0)
                        row = next((item for item in chunk if int(item["event"]["id"]) == event_id), None)
                        if row is None:
                            continue
                        parsed = self._parse_event_odds(event_payload, row["match"])
                        if not parsed:
                            continue
                        for offer in parsed:
                            offer.metadata["odds_api_io_account"] = str(account["name"])
                            offer.metadata["requested_bookmakers"] = str(account["bookmakers"])
                        offers_by_match[row["match"].match_key].extend(parsed)
                        stats["offers_parsed"] += len(parsed)
                        stats["markets_parsed"] += len({(offer.bookmaker, offer.family, offer.market_name, offer.point) for offer in parsed})
                        bookmakers_seen.update(offer.bookmaker for offer in parsed)
                        account_stats = stats["accounts"].setdefault(str(account["name"]), {})
                        account_stats["offers_parsed"] = int(account_stats.get("offers_parsed") or 0) + len(parsed)
                        account_stats["events_matched"] = int(account_stats.get("events_matched") or 0) + 1
                if stats.get("rate_limited"):
                    break

            stats["bookmakers_seen"] = len(bookmakers_seen)
            stats["bookmakers_seen_names"] = sorted(bookmakers_seen)
            stats["matches_with_2plus_books"] = sum(
                1
                for offers in offers_by_match.values()
                if len({offer.bookmaker for offer in offers if str(offer.bookmaker or "").strip()}) >= 2
            )
            stats["matches_with_1_book"] = sum(
                1
                for offers in offers_by_match.values()
                if len({offer.bookmaker for offer in offers if str(offer.bookmaker or "").strip()}) == 1
            )
            offers_by_family: dict[str, int] = defaultdict(int)
            for offers in offers_by_match.values():
                for offer in offers:
                    offers_by_family[str(offer.family or "unknown")] += 1
            stats["offers_by_family"] = dict(sorted(offers_by_family.items()))
            return dict(offers_by_match), stats, preview


    async def _request_odds_multi(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        event_ids: list[int],
        target_books: str,
    ) -> httpx.Response | None:
        params = {
            "apiKey": api_key,
            "eventIds": ",".join(str(item) for item in event_ids),
            "bookmakers": target_books,
        }
        return await client.get(f"{self.base_url}/odds/multi", params=params)

    async def _fetch_odds_multi_chunk(
        self,
        client: httpx.AsyncClient,
        api_key: str,
        event_ids: list[int],
        target_books: str,
        stats: dict[str, Any],
        account_name: str = "account1",
    ) -> list[dict[str, Any]]:
        if not event_ids:
            return []
        account_stats = stats.setdefault("accounts", {}).setdefault(account_name, {})
        account_stats.setdefault("bookmakers", target_books)
        account_stats.setdefault("http_statuses", [])
        attempts = 0
        while attempts < 2:
            if not self._request_budget_allows(stats):
                return []
            attempts += 1
            stats["odds_requests"] += 1
            account_stats["odds_requests"] = int(account_stats.get("odds_requests") or 0) + 1
            self._requests_used += 1
            try:
                response = await self._request_odds_multi(client, api_key, event_ids, target_books)
            except Exception as exc:
                stats["response_errors"] += 1
                account_stats["response_errors"] = int(account_stats.get("response_errors") or 0) + 1
                stats["last_body_preview"] = f"odds request failed: {exc}"
                response = None
            if response is None:
                continue
            stats["odds_http_statuses"].append(response.status_code)
            account_stats["http_statuses"].append(response.status_code)
            stats["last_body_preview"] = response.text[:2000]
            if response.status_code == 429:
                stats["response_errors"] += 1
                account_stats["response_errors"] = int(account_stats.get("response_errors") or 0) + 1
                account_stats["rate_limited"] = True
                stats["rate_limited"] = True
                return []
            if response.status_code == 200:
                payload = self._safe_json(response)
                if payload is None:
                    stats["response_errors"] += 1
                    continue
                shape = self._payload_shape(payload)
                if shape not in stats["payload_shapes"]:
                    stats["payload_shapes"].append(shape)
                return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
            if response.status_code >= 500 and len(event_ids) > 1:
                mid = max(1, len(event_ids) // 2)
                left = await self._fetch_odds_multi_chunk(client, api_key, event_ids[:mid], target_books, stats, account_name=account_name)
                right = await self._fetch_odds_multi_chunk(client, api_key, event_ids[mid:], target_books, stats, account_name=account_name)
                return left + right
            stats["response_errors"] += 1
            account_stats["response_errors"] = int(account_stats.get("response_errors") or 0) + 1
            if response.status_code < 500:
                return []
        return []

    def _request_budget_allows(self, stats: dict[str, Any]) -> bool:
        if self.max_http_requests <= 0:
            stats["budget_exhausted"] = True
            return False
        if self._requests_used >= self.max_http_requests:
            stats["budget_exhausted"] = True
            return False
        return True

    def _bookmakers_param(self) -> str:
        return ",".join(account["bookmakers"] for account in self._odds_accounts()) or "Bet365,Unibet"

    def _bookmakers_param_from_values(self, preferred: list[str], fallback: list[str]) -> str:
        values: list[str] = []
        allowed = {
            "bet365": "Bet365",
            "unibet": "Unibet",
            "betfair": "Betfair Exchange",
            "betfairexchange": "Betfair Exchange",
            "sbobet": "Sbobet",
        }
        for item in preferred:
            raw = str(item or "").strip()
            if not raw:
                continue
            value = allowed.get(normalize_bookmaker_name(raw))
            if value and value not in values:
                values.append(value)
        return ",".join(values or fallback)

    def _odds_accounts(self) -> list[dict[str, str]]:
        account1_key = str(getattr(self.settings, "odds_api_io_key", "") or "").strip()
        account2_key = str(getattr(self.settings, "odds_api_io_key_2", "") or "").strip()
        account1_books = self._bookmakers_param_from_values(
            list(getattr(self.settings, "odds_api_io_bookmakers_account1", []) or [])
            or list(getattr(self.settings, "odds_api_io_bookmakers", []) or []),
            ["Bet365", "Unibet"],
        )
        account2_books = self._bookmakers_param_from_values(
            list(getattr(self.settings, "odds_api_io_bookmakers_account2", []) or []),
            ["Betfair Exchange", "Sbobet"],
        )
        accounts: list[dict[str, str]] = []
        if account1_key:
            accounts.append({"name": "account1", "api_key": account1_key, "bookmakers": account1_books})
        if account2_key:
            accounts.append({"name": "account2", "api_key": account2_key, "bookmakers": account2_books})
        return accounts

    @staticmethod
    def _safe_json(response: httpx.Response) -> Any | None:
        try:
            return response.json()
        except Exception:
            return None

    @staticmethod
    def _payload_shape(payload: Any) -> str:
        if isinstance(payload, list):
            return "list"
        if isinstance(payload, dict):
            return ",".join(sorted(map(str, payload.keys()))[:12])
        return type(payload).__name__

    def _parse_event(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        home = str(raw.get("home") or "").strip()
        away = str(raw.get("away") or "").strip()
        league = str((raw.get("league") or {}).get("name") or "").strip()
        if not home or not away:
            return None
        if is_simulated_or_esports_event(home, away, league):
            return None
        try:
            commence_time = parse_datetime(raw.get("date"))
        except Exception:
            return None
        return {
            "id": int(raw.get("id") or 0),
            "home": home,
            "away": away,
            "league": league,
            "commence_time": commence_time,
            "raw": raw,
        }

    def _match_event(self, event: dict[str, Any], matches: list[Match]) -> Match | None:
        best_match: Match | None = None
        best_score = 0.0
        best_quality: str | None = None
        exact_tol = float(getattr(self.settings, "match_start_tolerance_hours", 12) or 12)
        fuzzy_tol = float(getattr(self.settings, "fallback_match_start_tolerance_hours", 8) or 8)
        for match in matches:
            score, quality = score_event_match(
                sport=match.sport_key,
                match_home=match.home_team,
                match_away=match.away_team,
                match_start=match.commence_time,
                match_league=match.league_name,
                event_home=event["home"],
                event_away=event["away"],
                event_start=event["commence_time"],
                event_league=event["league"],
                exact_tolerance_hours=exact_tol,
                fuzzy_tolerance_hours=fuzzy_tol,
            )
            if score > best_score:
                best_score = score
                best_quality = quality
                best_match = match
        if best_match is None or best_score < 48.0:
            return None
        event["match_score"] = best_score
        event["match_quality"] = best_quality
        return best_match

    def _match_priority(self, match: Match, now: datetime) -> tuple[int, int, int, float, str, str]:
        publish_window = now + timedelta(hours=max(1, int(getattr(self.settings, "publish_window_hours", 48) or 48)))
        in_window = 0 if now <= match.commence_time <= publish_window else 1
        tier_rank = 0 if match.tier == "top" else 1 if match.tier == "mid" else 2
        kickoff_distance = abs((match.commence_time - now).total_seconds()) / 3600.0
        return (in_window, tier_rank, 0 if match.metadata.get("bet365_id") else 1, kickoff_distance, match.league_name.lower(), match.home_team.lower())

    def _parse_event_odds(self, payload: dict[str, Any], match: Match) -> list[Offer]:
        bookmakers = payload.get("bookmakers")
        offers: list[Offer] = []
        seen: set[tuple[str, str, str, float | None]] = set()

        def to_float(value: Any) -> float | None:
            try:
                if value in (None, ""):
                    return None
                return float(str(value).replace(",", "."))
            except Exception:
                return None

        def add_offer(
            bookmaker: str,
            family: str,
            selection: str,
            price_value: Any,
            point: Any = None,
            market_name: str = "",
            team_side: str | None = None,
        ) -> None:
            price = to_float(price_value)
            if price is None or price <= 1.0:
                return
            point_value = to_float(point)
            book = self._canonical_bookmaker(bookmaker)
            normalized_team_side = str(team_side or "").strip().lower() or None
            key = (book, family, selection, point_value, normalized_team_side)
            if key in seen:
                return
            seen.add(key)
            offers.append(
                Offer(
                    source="odds_api_io",
                    bookmaker=book,
                    family=family,
                    selection=selection,
                    price=price,
                    point=point_value,
                    team_side=normalized_team_side,
                    market_name=market_name,
                    market_key=family,
                    metadata={"odds_api_io": True},
                )
            )

        def parse_outcomes(bookmaker_name: str, market_key: str, outcomes: list[dict[str, Any]]) -> None:
            if not self._is_supported_market(market_key):
                return
            family = self._family_for_market(market_key)
            if family is None:
                return
            default_team_side = self._infer_team_total_side(market_key, None, match)
            for row in outcomes:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or row.get("label") or row.get("selection") or "").strip()
                point = self._line_from_value(
                    row.get("point")
                    or row.get("line")
                    or row.get("handicap")
                    or row.get("hdp")
                    or row.get("max")
                )
                price_value = row.get("price") or row.get("odds") or row.get("decimal") or row.get("value")
                if family == "h2h":
                    selection = self._map_h2h_selection(name, match)
                    add_offer(bookmaker_name, family, selection, price_value, None, market_key)
                    continue
                if family == "totals":
                    selection = "Over" if name.lower().startswith("over") else "Under" if name.lower().startswith("under") else name
                    add_offer(bookmaker_name, family, selection, price_value, point, market_key)
                    continue
                if family == "spreads":
                    selection = match.home_team if name.lower() in {"home", match.home_team.lower(), "1"} else match.away_team if name.lower() in {"away", match.away_team.lower(), "2"} else name
                    team_side = "home" if selection == match.home_team else "away" if selection == match.away_team else None
                    if team_side == "away" and point is not None:
                        point = -float(point)
                    add_offer(bookmaker_name, family, selection, price_value, point, market_key, team_side=team_side)
                    continue
                if family == "btts":
                    selection = self._normalize_yes_no(name)
                    if selection is not None:
                        add_offer(bookmaker_name, family, selection, price_value, None, market_key)
                    continue
                if family == "doubleChance":
                    selection = self._normalize_double_chance_selection(name, match)
                    if selection is not None:
                        add_offer(bookmaker_name, family, selection, price_value, None, market_key)
                    continue
                if family == "dnb":
                    selection = self._normalize_dnb_selection(name, match)
                    if selection is not None:
                        add_offer(bookmaker_name, family, selection, price_value, None, market_key)
                    continue
                if family == "teamTotals":
                    selection = "Over" if name.lower().startswith("over") else "Under" if name.lower().startswith("under") else name
                    team_side = self._infer_team_total_side(market_key, name, match) or default_team_side
                    if team_side is not None:
                        add_offer(bookmaker_name, family, selection, price_value, point, market_key, team_side=team_side)
                    continue

        def parse_market_rows(bookmaker_name: str, market_key: str, rows: list[dict[str, Any]]) -> None:
            if not self._is_supported_market(market_key):
                return
            family = self._family_for_market(market_key)
            if family is None:
                return
            for row in rows:
                if not isinstance(row, dict):
                    continue

                point = self._line_from_value(
                    row.get("point")
                    or row.get("line")
                    or row.get("handicap")
                    or row.get("hdp")
                    or row.get("max")
                )
                label = str(row.get("label") or row.get("name") or row.get("selection") or "").strip()
                price_value = row.get("price") or row.get("odds") or row.get("decimal") or row.get("value")

                if family == "h2h":
                    added = False
                    for selection, field in ((match.home_team, "home"), ("Draw", "draw"), (match.away_team, "away")):
                        price = row.get(field) or row.get(f"{field}_od")
                        if price not in (None, ""):
                            add_offer(bookmaker_name, family, selection, price, None, market_key)
                            added = True
                    if not added and label:
                        selection = self._map_h2h_selection(label, match)
                        add_offer(bookmaker_name, family, selection, price_value, None, market_key)
                    continue

                if family == "totals":
                    added = False
                    over_price = row.get("over") or row.get("over_od")
                    under_price = row.get("under") or row.get("under_od")
                    if over_price not in (None, ""):
                        add_offer(bookmaker_name, family, "Over", over_price, point, market_key)
                        added = True
                    if under_price not in (None, ""):
                        add_offer(bookmaker_name, family, "Under", under_price, point, market_key)
                        added = True
                    if not added and label:
                        selection = "Over" if label.lower().startswith("over") else "Under" if label.lower().startswith("under") else label
                        add_offer(bookmaker_name, family, selection, price_value, point, market_key)
                    continue

                if family == "spreads":
                    added = False
                    home_price = row.get("home") or row.get("home_od")
                    away_price = row.get("away") or row.get("away_od")
                    if home_price not in (None, ""):
                        add_offer(bookmaker_name, family, match.home_team, home_price, point, market_key, team_side="home")
                        added = True
                    if away_price not in (None, ""):
                        away_point = -point if point is not None else None
                        add_offer(bookmaker_name, family, match.away_team, away_price, away_point, market_key, team_side="away")
                        added = True
                    if not added and label:
                        selection = match.home_team if label.lower() in {"home", match.home_team.lower(), "1"} else match.away_team if label.lower() in {"away", match.away_team.lower(), "2"} else label
                        team_side = "home" if selection == match.home_team else "away" if selection == match.away_team else None
                        normalized_point = point
                        if team_side == "away" and normalized_point is not None:
                            normalized_point = -normalized_point
                        add_offer(bookmaker_name, family, selection, price_value, normalized_point, market_key, team_side=team_side)
                    continue

                if family == "btts":
                    added = False
                    for selection, field in (("Yes", "yes"), ("No", "no")):
                        price = row.get(field) or row.get(f"{field}_od")
                        if price not in (None, ""):
                            add_offer(bookmaker_name, family, selection, price, None, market_key)
                            added = True
                    if not added and label:
                        selection = self._normalize_yes_no(label)
                        if selection is not None:
                            add_offer(bookmaker_name, family, selection, price_value, None, market_key)
                    continue

                if family == "doubleChance":
                    added = False
                    mapping = (("Home or Draw", "home_draw"), ("Away or Draw", "away_draw"), ("No Draw", "home_away"))
                    for selection, field in mapping:
                        price = row.get(field) or row.get(f"{field}_od")
                        if price not in (None, ""):
                            add_offer(bookmaker_name, family, selection, price, None, market_key)
                            added = True
                    if not added and label:
                        selection = self._normalize_double_chance_selection(label, match)
                        if selection is not None:
                            add_offer(bookmaker_name, family, selection, price_value, None, market_key)
                    continue

                if family == "dnb":
                    added = False
                    for selection, field in ((match.home_team, "home"), (match.away_team, "away")):
                        price = row.get(field) or row.get(f"{field}_od")
                        if price not in (None, ""):
                            add_offer(bookmaker_name, family, selection, price, None, market_key)
                            added = True
                    if not added and label:
                        selection = self._normalize_dnb_selection(label, match)
                        if selection is not None:
                            add_offer(bookmaker_name, family, selection, price_value, None, market_key)
                    continue

                if family == "teamTotals":
                    added = False
                    team_side = self._infer_team_total_side(market_key, label, match)
                    over_price = row.get("over") or row.get("over_od")
                    under_price = row.get("under") or row.get("under_od")
                    if over_price not in (None, "") and team_side is not None:
                        add_offer(bookmaker_name, family, "Over", over_price, point, market_key, team_side=team_side)
                        added = True
                    if under_price not in (None, "") and team_side is not None:
                        add_offer(bookmaker_name, family, "Under", under_price, point, market_key, team_side=team_side)
                        added = True
                    if not added and label:
                        selection = "Over" if label.lower().startswith("over") else "Under" if label.lower().startswith("under") else label
                        inferred_side = self._infer_team_total_side(market_key, label, match)
                        if inferred_side is not None:
                            add_offer(bookmaker_name, family, selection, price_value, point, market_key, team_side=inferred_side)

        if isinstance(bookmakers, dict):
            iterator = bookmakers.items()
        elif isinstance(bookmakers, list):
            iterator = []
            for item in bookmakers:
                if isinstance(item, dict):
                    iterator.append((str(item.get("name") or item.get("bookmaker") or "unknown"), item))
        else:
            iterator = []

        for bookmaker_name, bookmaker_payload in iterator:
            if isinstance(bookmaker_payload, list):
                markets = bookmaker_payload
            elif isinstance(bookmaker_payload, dict):
                markets = bookmaker_payload.get("markets", bookmaker_payload)
            else:
                continue

            if isinstance(markets, list):
                for market in markets:
                    if not isinstance(market, dict):
                        continue
                    market_key = str(market.get("key") or market.get("name") or market.get("market") or "").lower()
                    outcomes = market.get("outcomes")
                    if isinstance(outcomes, list):
                        parse_outcomes(bookmaker_name, market_key, [row for row in outcomes if isinstance(row, dict)])
                        continue
                    odds_rows = market.get("odds")
                    if isinstance(odds_rows, list):
                        parse_market_rows(bookmaker_name, market_key, [row for row in odds_rows if isinstance(row, dict)])
            elif isinstance(markets, dict):
                for market_key, market_value in markets.items():
                    key = str(market_key or "").lower()
                    if isinstance(market_value, dict):
                        outcomes = market_value.get("outcomes")
                        if isinstance(outcomes, list):
                            parse_outcomes(bookmaker_name, key, [row for row in outcomes if isinstance(row, dict)])
                            continue
                        odds_rows = market_value.get("odds")
                        if isinstance(odds_rows, list):
                            parse_market_rows(bookmaker_name, key, [row for row in odds_rows if isinstance(row, dict)])
                            continue
                    if isinstance(market_value, list):
                        rows = [row for row in market_value if isinstance(row, dict)]
                        parse_outcomes(bookmaker_name, key, rows)
                        parse_market_rows(bookmaker_name, key, rows)

        return offers

    @staticmethod
    def _is_supported_market(market_key: str) -> bool:
        key = str(market_key or '').lower().strip()
        if not key:
            return False
        banned_terms = (
            '1st half', 'first half', '2nd half', 'second half', 'half time', 'halftime', 'ht',
            'corner', 'corners', 'booking', 'bookings', 'card', 'cards', 'throw', 'throws',
            'offside', 'offsides', 'shot', 'shots', 'foul', 'fouls', 'player', 'next goal',
            'correct score', 'alternative', 'alternate', 'alt ', 'race to', 'odd/even',
            'clean sheet', 'win to nil', 'to qualify', 'penalty', 'minute', 'asian corners',
        )
        if any(term in key for term in banned_terms):
            return False
        return True

    @staticmethod
    def _family_for_market(market_key: str) -> str | None:
        key = str(market_key or "").lower().strip()
        if key in {"h2h", "1x2", "moneyline", "ml", "match winner", "match result", "full time result"} or "moneyline" in key:
            return "h2h"
        if 'draw no bet' in key or key in {'dnb', 'pk'}:
            return 'dnb'
        if 'double chance' in key or key in {'1x x2 12', 'doublechance'}:
            return 'doubleChance'
        if key in {'btts', 'both teams to score', 'both teams score', 'gg/ng'} or 'both teams to score' in key:
            return 'btts'
        if 'team total' in key or 'team totals' in key or key.startswith('home total') or key.startswith('away total') or 'goals over/under - home' in key or 'goals over/under - away' in key:
            return 'teamTotals'
        if key in {"totals", "goals over/under", "goal line", "over/under", "over under", "ou", "o/u"} or key.startswith("totals ") or key.startswith("goals over/under"):
            return "totals"
        if key in {"spread", "spreads", "handicap", "asian handicap"} or key.startswith("spread ") or key.startswith("handicap "):
            return "spreads"
        return None

    @staticmethod
    def _canonical_bookmaker(name: str) -> str:
        norm = normalize_bookmaker_name(name)
        if norm == "bet365":
            return "Bet365"
        if norm == "unibet":
            return "Unibet"
        if norm == "betfair":
            return "Betfair Exchange"
        if norm == "betfairexchange":
            return "Betfair Exchange"
        if norm == "sbobet":
            return "Sbobet"
        if norm == "pinnacle":
            return "Pinnacle"
        return str(name or "Unknown")

    @staticmethod
    def _line_from_value(value: Any) -> float | None:
        try:
            if value in (None, ''):
                return None
            return float(str(value).replace(',', '.'))
        except Exception:
            return None

    @staticmethod
    def _map_h2h_selection(raw_name: str, match: Match) -> str:
        name = str(raw_name or "").strip().lower()
        if name in {"home", "1", match.home_team.lower()}:
            return match.home_team
        if name in {"away", "2", match.away_team.lower()}:
            return match.away_team
        if name in {"draw", "x", "tie"}:
            return "Draw"
        return raw_name or ""

    @staticmethod
    def _normalize_yes_no(raw_name: str) -> str | None:
        name = str(raw_name or '').strip().lower().replace('-', ' ')
        if name in {'yes', 'да', 'btts yes', 'both teams to score yes', 'both teams score yes', 'gg'} or name.endswith(' yes'):
            return 'Yes'
        if name in {'no', 'нет', 'btts no', 'both teams to score no', 'both teams score no', 'ng'} or name.endswith(' no'):
            return 'No'
        return None

    @staticmethod
    def _normalize_double_chance_selection(raw_name: str, match: Match) -> str | None:
        text = str(raw_name or '').strip().lower().replace(' ', '')
        if text in {'1x', 'homeordraw'}:
            return f'{match.home_team} or Draw'
        if text in {'x2', 'awayordraw'}:
            return f'{match.away_team} or Draw'
        if text in {'12', 'nodraw'}:
            return 'No Draw'
        normalized = str(raw_name or '').strip()
        if normalized:
            return normalized
        return None

    @staticmethod
    def _normalize_dnb_selection(raw_name: str, match: Match) -> str | None:
        name = str(raw_name or '').strip().lower()
        if name in {'home', '1', match.home_team.lower()}:
            return match.home_team
        if name in {'away', '2', match.away_team.lower()}:
            return match.away_team
        return None

    @staticmethod
    def _infer_team_total_side(market_key: str, label: str | None, match: Match) -> str | None:
        key = str(market_key or '').strip().lower()
        raw_label = str(label or '').strip().lower()
        label_text = raw_label.replace('team total', '').replace('total', '').replace('goals', '').strip()
        if any(token in key for token in ('home total', 'team total home', 'goals over/under - home')):
            return 'home'
        if any(token in key for token in ('away total', 'team total away', 'goals over/under - away')):
            return 'away'
        if raw_label in {'home', '1'} or label_text in {match.home_team.lower(), 'home', '1'}:
            return 'home'
        if raw_label in {'away', '2'} or label_text in {match.away_team.lower(), 'away', '2'}:
            return 'away'
        if match.home_team.lower() in raw_label:
            return 'home'
        if match.away_team.lower() in raw_label:
            return 'away'
        return None


    def _looks_low_tier(self, league_name: str) -> bool:
        text = str(league_name or "").lower()
        markers = ("u17", "u18", "u19", "u20", "u21", "u23", "women", "reserve", "friendly", "esports")
        return any(marker in text for marker in markers)
