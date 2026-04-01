from pathlib import Path
import re

base = Path('/mnt/data/repo/app')

# Patch utils.py
utils = (base/'utils.py').read_text()
utils = utils.replace(
"""TEAM_ALIAS_MAP = {
""",
"""BOOKMAKER_ALIAS_MAP = {
    "unibet": "unibet",
    "unibetuk": "unibet",
    "unibetfr": "unibet",
    "unibetnl": "unibet",
    "unibetse": "unibet",
    "bet365": "bet365",
    "bet365com": "bet365",
    "bet365sportsbook": "bet365",
    "betfair": "betfair",
    "betfairexchange": "betfair",
}

TEAM_ALIAS_MAP = {
"""
)
utils = utils.replace(
    r'r"\\bfc|cf|ac|sc|club|fk|bk|afc|calcio|hc|bc|kk|baseball|basketball|hockey|club de futbol|esporte clube|deportivo|de|da|del|cd|ud|sd\\b": " ",',
    r'r"\\b(?:fc|cf|ac|sc|club|fk|bk|afc|calcio|hc|bc|kk|baseball|basketball|hockey|club de futbol|esporte clube|deportivo|de|da|del|cd|ud|sd)\\b": " ",',
)
utils = utils.replace(
"""def normalize_bookmaker_name(name: str) -> str:
    return "".join(ch for ch in str(name or "").lower() if ch.isalnum())
""",
"""def normalize_bookmaker_name(name: str) -> str:
    raw = "".join(ch for ch in str(name or "").lower() if ch.isalnum())
    if not raw:
        return ""
    if raw.startswith("unibet"):
        return "unibet"
    if raw.startswith("bet365"):
        return "bet365"
    return BOOKMAKER_ALIAS_MAP.get(raw, raw)
"""
)
(base/'utils.py').write_text(utils)

# Patch schemas.py
schemas = (base/'schemas.py').read_text()
schemas = schemas.replace(
"""    implied_probability: float
    model_probability: float
    adjusted_probability: float
    edge_pct: float
""",
"""    implied_probability: float
    market_probability: float
    consensus_probability: float
    model_probability: float
    final_probability: float
    adjusted_probability: float
    model_mode: str = "market_only"
    edge_pct: float = 0.0
"""
)
(base/'schemas.py').write_text(schemas)

# Patch config.py add bookies placeholders
config = (base/'config.py').read_text()
config = config.replace(
"""    odds_api_io_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ODDS_API_IO_KEY"),
    )
    sstats_api_key: str | None = Field(
""",
"""    odds_api_io_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("ODDS_API_IO_KEY"),
    )
    bookies_api_key: str | None = Field(
        default=None,
        validation_alias=AliasChoices("BOOKIES_API_KEY", "BOOKIESAPI_KEY"),
    )
    sstats_api_key: str | None = Field(
"""
)
(base/'config.py').write_text(config)

# Replace model.py entirely with targeted transforms
model_path = base/'services'/'model.py'
text = model_path.read_text()
text = text.replace(
"self.target_books = {normalize_bookmaker_name(name): True for name in settings.target_bookmakers}",
"self.target_books = {normalize_bookmaker_name(name): True for name in settings.target_bookmakers if normalize_bookmaker_name(name)}"
)

text = text.replace(
"""        target_offers = [offer for offer in offers if self._is_target_book(offer.bookmaker)]
        if not target_offers:
            target_offers = offers

        seen_candidate_keys: dict[tuple[str, str, str, str], CandidateBet] = {}
        for offer in target_offers:
            tag = self._selection_tag(match, offer)
            group_key = self._group_key(offer)
            if not tag or not group_key:
                rejections[\"unsupported_offer_shape\"] += 1
                continue
            candidate = self._evaluate_offer(match, offer, grouped, context, model_base)
            if candidate is None:
                rejections[\"offer_rejected\"] += 1
                continue
""",
"""        target_offers = [offer for offer in offers if self._is_target_book(offer.bookmaker)]
        rejections[\"non_target_bookmaker\"] += max(0, len(offers) - len(target_offers))
        if not target_offers:
            debug = {
                \"match_key\": match.match_key,
                \"sport\": match.sport_key,
                \"league\": match.league_name,
                \"home\": match.home_team,
                \"away\": match.away_team,
                \"offers_total\": len(offers),
                \"target_offers\": 0,
                \"groups\": {key: sum(len(v) for v in books.values()) for key, books in grouped.items()},
                \"context\": asdict(context) if context is not None else None,
                \"model_base\": model_base,
                \"candidate_count\": 0,
                \"mode\": \"target_bookmakers_only\",
            }
            return [], rejections, debug

        seen_candidate_keys: dict[tuple[str, str, str, str], CandidateBet] = {}
        for offer in target_offers:
            tag = self._selection_tag(match, offer)
            group_key = self._group_key(offer)
            if not tag or not group_key:
                rejections[\"unsupported_market_family\"] += 1
                continue
            candidate, rejection_reason = self._evaluate_offer(match, offer, grouped, context, model_base)
            if candidate is None:
                rejections[rejection_reason or \"offer_rejected\"] += 1
                continue
"""
)
text = text.replace(
'            "offers_total": len(offers),\n',
'            "offers_total": len(offers),\n            "target_offers": len(target_offers),\n'
)
text = text.replace(
"""    ) -> CandidateBet | None:
""",
"""    ) -> tuple[CandidateBet | None, str | None]:
"""
)
text = text.replace(
"""        if not tag or not group_key:
            return None
""",
"""        if not tag or not group_key:
            return None, "unsupported_market_family"
"""
)
text = text.replace(
"""        if consensus is None or consensus.get("books_count", 0) < self.settings.min_books_for_consensus:
            consensus = self._consensus_for_group(match, grouped, group_key, tag, exclude_book=None)
        if consensus is None:
            return None
""",
"""        if consensus is None or consensus.get("books_count", 0) < self.settings.min_books_for_consensus:
            consensus = self._consensus_for_group(match, grouped, group_key, tag, exclude_book=None)
        if consensus is None:
            return None, "no_consensus_group"
"""
)
text = text.replace(
"""        if model_probability is None:
            return None
""",
"""        if model_probability is None:
            return None, "no_consensus_group"
"""
)
text = text.replace(
"""        if offer.price < self.settings.odds_min or offer.price > self.settings.odds_max:
            return None
        if books_count < self.settings.min_books_publish:
            return None
        if sources_count < self.settings.min_sources_publish:
            return None
        if confidence < self.settings.min_model_confidence:
            return None
        if edge_pct < self.settings.min_edge_pct:
            return None
        if ev_pct < self.settings.min_ev_pct:
            return None

        outlier_distance = price_distance_pct(offer.price, reference_price) if reference_price else None
        outlier_penalty = 0.0
        if outlier_distance is not None and outlier_distance > self.settings.outlier_price_tolerance_pct:
            outlier_penalty = clamp(
                outlier_distance - self.settings.outlier_price_tolerance_pct,
                0.0,
                self.settings.outlier_max_penalty,
            )
""",
"""        if offer.price < self.settings.odds_min or offer.price > self.settings.odds_max:
            return None, "odds_out_of_range"
        if books_count < self.settings.min_books_publish:
            return None, "insufficient_books"
        if sources_count < self.settings.min_sources_publish:
            return None, "insufficient_sources"
        if confidence < self.settings.min_model_confidence:
            return None, "confidence_below_threshold"
        if edge_pct < self.settings.min_edge_pct:
            return None, "edge_below_threshold"
        if ev_pct < self.settings.min_ev_pct:
            return None, "ev_below_threshold"

        outlier_distance = price_distance_pct(offer.price, reference_price) if reference_price else None
        outlier_penalty = 0.0
        if outlier_distance is not None and outlier_distance > self.settings.outlier_price_tolerance_pct:
            outlier_penalty = clamp(
                outlier_distance - self.settings.outlier_price_tolerance_pct,
                0.0,
                self.settings.outlier_max_penalty,
            )
        if outlier_penalty >= self.settings.outlier_max_penalty:
            return None, "outlier_penalty_too_high"
"""
)
text = text.replace(
"""        reasons = [
            f"model={model_reason}",
            f"consensus_fair_odds={round2(consensus.get('fair_odds'))}",
            f"market_prob={round2(market_prob * 100.0)}%",
        ]
""",
"""        final_probability = adjusted_probability
        model_mode = "market_only" if model_reason == "consensus" else model_reason
        reasons = [
            f"mode={model_mode}",
            f"model={model_reason}",
            f"consensus_fair_odds={round2(consensus.get('fair_odds'))}",
            f"market_prob={round2(market_prob * 100.0)}%",
            f"model_prob={round2(model_probability * 100.0)}%",
            f"final_prob={round2(final_probability * 100.0)}%",
        ]
"""
)
text = text.replace(
"""        return CandidateBet(
""",
"""        return CandidateBet(
"""
)
text = text.replace(
"""            implied_probability=implied,
            model_probability=model_probability,
            adjusted_probability=adjusted_probability,
            edge_pct=edge_pct,
""",
"""            implied_probability=implied,
            market_probability=market_prob,
            consensus_probability=market_prob,
            model_probability=model_probability,
            final_probability=final_probability,
            adjusted_probability=adjusted_probability,
            model_mode=model_mode,
            edge_pct=edge_pct,
"""
)
text = text.replace(
"""                "consensus_sources": consensus.get("sources", []),
            },
            diagnostics={
""",
"""                "consensus_sources": consensus.get("sources", []),
                "mode": model_mode,
            },
            diagnostics={
"""
)
text = text.replace(
"""                "market_probability": market_prob,
                "reference_price": reference_price,
                "consensus_fair_odds": consensus.get("fair_odds"),
""",
"""                "market_probability": market_prob,
                "consensus_probability": market_prob,
                "model_probability": model_probability,
                "final_probability": final_probability,
                "reference_price": reference_price,
                "consensus_fair_odds": consensus.get("fair_odds"),
"""
)
text = text.replace(
"""            publication_score=publication_score,
        )
""",
"""            publication_score=publication_score,
        ), None
"""
)
model_path.write_text(text)

# Patch runner debug summary for mode counts
runner_path = base/'services'/'runner.py'
runner = runner_path.read_text()
runner = runner.replace(
"""            summary = {
""",
"""            mode_counts: dict[str, int] = defaultdict(int)
            for candidate in candidates:
                mode_counts[str(candidate.model_mode)] += 1
            summary = {
"""
)
runner = runner.replace(
"""                "rejections": rejections,
            }
""",
"""                "rejections": rejections,
                "candidate_modes": dict(mode_counts),
            }
"""
)
runner_path.write_text(runner)

# Patch sstats diagnostics
sstats_path = base/'providers'/'sstats.py'
sstats = sstats_path.read_text()
sstats = sstats.replace(
"""        stats: dict[str, Any] = {
            "enabled": bool(self.settings.enable_sstats_context and self.settings.sstats_api_key),
            "api_key_present": bool(self.settings.sstats_api_key),
            "requests": 0,
            "response_errors": 0,
            "days_requested": 0,
            "rows_fetched": 0,
            "contexts_built": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "unmatched_rows": 0,
        }
        preview: dict[str, Any] = {"unmatched_rows": [], "matched_examples": []}
""",
"""        stats: dict[str, Any] = {
            "enabled": bool(self.settings.enable_sstats_context and self.settings.sstats_api_key),
            "api_key_present": bool(self.settings.sstats_api_key),
            "requests": 0,
            "response_errors": 0,
            "days_requested": 0,
            "rows_fetched": 0,
            "contexts_built": 0,
            "matched_exact": 0,
            "matched_loose": 0,
            "matched_fuzzy": 0,
            "unmatched_rows": 0,
            "http_statuses": [],
            "payload_shapes": [],
            "last_body_preview": None,
            "last_url": None,
        }
        preview: dict[str, Any] = {"unmatched_rows": [], "matched_examples": [], "request_debug": []}
"""
)
sstats = sstats.replace(
"""                preview["unmatched_rows"].extend(day_preview["unmatched_rows"][:10])
                preview["matched_examples"].extend(day_preview["matched_examples"][:10])
""",
"""                preview["unmatched_rows"].extend(day_preview["unmatched_rows"][:10])
                preview["matched_examples"].extend(day_preview["matched_examples"][:10])
                if stats.get("last_url"):
                    preview["request_debug"].append({
                        "date": date_key,
                        "url": stats.get("last_url"),
                        "status": (stats.get("http_statuses") or [None])[-1],
                        "shape": (stats.get("payload_shapes") or [None])[-1],
                    })
"""
)
sstats = sstats.replace(
"""        for url in [self.primary_url, self.fallback_url]:
            try:
                response = await client.get(url, params=params, headers=headers)
            except Exception:
                stats["response_errors"] += 1
                continue
            if response.status_code != 200:
                stats["response_errors"] += 1
                continue
            try:
                payload = response.json()
            except Exception:
                stats["response_errors"] += 1
                continue
            if isinstance(payload, list):
                return [row for row in payload if isinstance(row, dict)]
            if isinstance(payload, dict):
                rows = payload.get("data") or payload.get("results") or payload.get("rows") or []
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
            return []
""",
"""        for url in [self.primary_url, self.fallback_url, self.primary_url + "/", self.fallback_url + "/"]:
            stats["last_url"] = url
            try:
                response = await client.get(url, params=params, headers=headers)
            except Exception:
                stats["response_errors"] += 1
                continue
            stats.setdefault("http_statuses", []).append(response.status_code)
            stats["last_body_preview"] = response.text[:400]
            if response.status_code != 200:
                stats["response_errors"] += 1
                continue
            try:
                payload = response.json()
            except Exception:
                stats["response_errors"] += 1
                continue
            if isinstance(payload, list):
                stats.setdefault("payload_shapes", []).append("list")
                return [row for row in payload if isinstance(row, dict)]
            if isinstance(payload, dict):
                stats.setdefault("payload_shapes", []).append(",".join(sorted(payload.keys())[:10]))
                rows = (
                    payload.get("data")
                    or payload.get("results")
                    or payload.get("rows")
                    or payload.get("games")
                    or payload.get("matches")
                    or payload.get("items")
                    or []
                )
                if isinstance(rows, list):
                    return [row for row in rows if isinstance(row, dict)]
            return []
"""
)
sstats_path.write_text(sstats)

# Patch odds_api_io diagnostics and more flexible parsing
odds_path = base/'providers'/'odds_api_io.py'
odds = odds_path.read_text()
odds = odds.replace(
"""        stats: dict[str, Any] = {
            "enabled": bool(self.settings.enable_odds_api_io and self.settings.odds_api_io_key),
            "api_key_present": bool(self.settings.odds_api_io_key),
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
        }
        preview: dict[str, Any] = {"unmatched_events": [], "matched_examples": []}
""",
"""        stats: dict[str, Any] = {
            "enabled": bool(self.settings.enable_odds_api_io and self.settings.odds_api_io_key),
            "api_key_present": bool(self.settings.odds_api_io_key),
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
        }
        preview: dict[str, Any] = {"unmatched_events": [], "matched_examples": [], "response_debug": []}
"""
)
odds = odds.replace(
"""            if response.status_code != 200:
                stats["response_errors"] += 1
                break
            try:
                payload = response.json()
""",
"""            stats.setdefault("event_http_statuses", []).append(response.status_code)
            stats["last_body_preview"] = response.text[:500]
            if response.status_code != 200:
                stats["response_errors"] += 1
                break
            try:
                payload = response.json()
"""
)
odds = odds.replace(
"""            if not isinstance(payload, list):
                break
""",
"""            if not isinstance(payload, list):
                if isinstance(payload, dict):
                    stats.setdefault("payload_shapes", []).append(",".join(sorted(payload.keys())[:10]))
                    payload = payload.get("data") or payload.get("events") or payload.get("results") or []
                if not isinstance(payload, list):
                    break
"""
)
odds = odds.replace(
"""            if response.status_code != 200:
                stats["response_errors"] += 1
                return []
        try:
            payload = response.json()
""",
"""            stats.setdefault("odds_http_statuses", []).append(response.status_code)
            stats["last_body_preview"] = response.text[:800]
            if response.status_code != 200:
                stats["response_errors"] += 1
                return []
        try:
            payload = response.json()
"""
)
odds = odds.replace(
"""        if isinstance(payload, list):
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            rows: list[dict[str, Any]] = []
            for key, value in payload.items():
                if isinstance(value, dict):
                    if "id" not in value:
                        value = dict(value)
                        value["id"] = key
                    rows.append(value)
            return rows
""",
"""        if isinstance(payload, list):
            stats.setdefault("payload_shapes", []).append("odds:list")
            return [item for item in payload if isinstance(item, dict)]
        if isinstance(payload, dict):
            stats.setdefault("payload_shapes", []).append("odds:" + ",".join(sorted(payload.keys())[:10]))
            direct_rows = payload.get("data") or payload.get("results") or payload.get("events")
            if isinstance(direct_rows, list):
                return [item for item in direct_rows if isinstance(item, dict)]
            rows: list[dict[str, Any]] = []
            for key, value in payload.items():
                if isinstance(value, dict):
                    if "id" not in value:
                        value = dict(value)
                        value["id"] = key
                    rows.append(value)
            return rows
"""
)
odds = odds.replace(
"""        bookmakers = row.get("bookmakers") or {}
        if not isinstance(bookmakers, dict):
            return [], 0
        offers: list[Offer] = []
        markets_parsed = 0
        for bookmaker_name, markets in bookmakers.items():
            if not isinstance(markets, list):
                continue
            for market in markets:
""",
"""        bookmakers = row.get("bookmakers") or {}
        offers: list[Offer] = []
        markets_parsed = 0
        bookmaker_rows: list[tuple[str, Any]] = []
        if isinstance(bookmakers, dict):
            bookmaker_rows.extend(bookmakers.items())
        elif isinstance(bookmakers, list):
            for bookmaker in bookmakers:
                if not isinstance(bookmaker, dict):
                    continue
                bookmaker_name = str(bookmaker.get("name") or bookmaker.get("title") or bookmaker.get("key") or "")
                bookmaker_rows.append((bookmaker_name, bookmaker))
        else:
            return [], 0
        for bookmaker_name, markets in bookmaker_rows:
            if isinstance(markets, dict):
                markets = markets.get("markets") or markets.get("odds") or markets.get("lines") or markets
                if isinstance(markets, dict):
                    markets = list(markets.values())
            if not isinstance(markets, list):
                continue
            for market in markets:
"""
)
odds = odds.replace(
"""                prices = market.get("odds") or []
                if not isinstance(prices, list):
                    continue
""",
"""                prices = market.get("odds") or market.get("outcomes") or market.get("prices") or []
                if isinstance(prices, dict):
                    prices = [prices]
                if not isinstance(prices, list):
                    continue
"""
)
odds = odds.replace(
"""                        parsed_offers, markets_parsed = self._parse_event_odds(row, entry["match"], entry["mode"])
                        stats["markets_parsed"] += markets_parsed
                        stats["offers_parsed"] += len(parsed_offers)
                        if parsed_offers:
                            offers_by_match[entry["match"].match_key].extend(parsed_offers)
""",
"""                        parsed_offers, markets_parsed = self._parse_event_odds(row, entry["match"], entry["mode"])
                        stats["markets_parsed"] += markets_parsed
                        stats["offers_parsed"] += len(parsed_offers)
                        stats["bookmakers_seen"] += len(row.get("bookmakers") or []) if isinstance(row.get("bookmakers"), (dict, list)) else 0
                        preview["response_debug"].append({
                            "event_id": event_id,
                            "markets_parsed": markets_parsed,
                            "offers_parsed": len(parsed_offers),
                            "top_level_keys": sorted(list(row.keys()))[:12],
                            "bookmakers_type": type(row.get("bookmakers") or {}).__name__,
                        })
                        if parsed_offers:
                            offers_by_match[entry["match"].match_key].extend(parsed_offers)
"""
)
odds_path.write_text(odds)

print('patched')
