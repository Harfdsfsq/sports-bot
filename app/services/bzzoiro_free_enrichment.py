"""Bounded native v2 football enrichment; no consensus quote becomes an Offer."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx

from app.services.api_budget import BudgetedAsyncClient
from app.services.context_admission import admit_context
from app.services.daily_match_registry import parse_time, read_json, write_json
from app.services.fixture_identity import direct_fixture_identity, same_team_identity

BASE = "https://sports.bzzoiro.com/api/v2"


def event_identity(event):
    def name(value):
        return value.get("name") if isinstance(value, dict) else value

    return {
        "home": name(event.get("home_team")),
        "away": name(event.get("away_team")),
        "commence_time": event.get("event_date"),
    }


def flatten_prediction(payload):
    """Documented v2 percentages are 0–100; model confidence is 0–1."""
    markets = payload.get("markets") or {}
    xg, outcome = markets.get("expected_goals") or {}, markets.get("match_result") or {}
    totals, model = markets.get("over_under") or {}, payload.get("model") or {}
    # Never manufacture sporting xG from odds/consensus or an incomplete payload.
    try:
        goals = [float(xg[side]) for side in ("home", "away")]
        if any(not math.isfinite(v) or not 0 <= v <= 10 for v in goals):
            return None
    except (KeyError, TypeError, ValueError):
        return None
    if model.get("confidence") is not None:
        confidence = float(model["confidence"])
        if not math.isfinite(confidence) or not 0 <= confidence <= 1:
            return None
    result = {
        "id": payload.get("id"),
        "created_at": payload.get("created_at"),
        "updated_at": payload.get("updated_at"),
        "expected_home_goals": goals[0],
        "expected_away_goals": goals[1],
        "confidence": model.get("confidence"),
        "model_version": model.get("version"),
    }
    for source, target in [("prob_home", "prob_home_win"), ("prob_away", "prob_away_win"), ("prob_draw", "prob_draw")]:
        if outcome.get(source) is not None:
            value = float(outcome[source])
            if not math.isfinite(value) or not 0 <= value <= 100:
                return None
            result[target] = value / 100
    for field in ("prob_over_15", "prob_over_25", "prob_over_35"):
        if totals.get(field) is not None:
            value = float(totals[field])
            if not math.isfinite(value) or not 0 <= value <= 100:
                return None
            result[field] = value / 100
    return result


async def enrich(provider, matches, contexts, stats):
    now = datetime.now(UTC)
    targets = [
        m
        for m in matches
        if timedelta(minutes=30)
        <= m.commence_time - now
        <= timedelta(hours=min(48, max(4, float(os.getenv("DAILY_DATA_COLLECTION_WINDOW_HOURS", "24")))))
    ]
    targets.sort(key=lambda m: (m.match_key in contexts, m.commence_time))
    targets = targets[:300]
    detail_limit = max(0, min(30, int(os.getenv("BZZOIRO_V2_NATIVE_MATCH_LIMIT", "8"))))
    if not targets:
        return
    limit = min(30, max(0, int(os.getenv("BZZOIRO_V2_NATIVE_REQUEST_LIMIT", "24"))))
    report = stats["v2_native"] = {
        "requests": 0,
        "cache_hits": 0,
        "contexts": 0,
        "details": 0,
        "detail_attempts": 0,
        "standings": 0,
        "rejected": 0,
        "errors": 0,
        "prediction_records": 0,
        "fixtures_matched": 0,
        "skip_reasons": {},
    }

    def skip(reason):
        report["skip_reasons"][reason] = report["skip_reasons"].get(reason, 0) + 1

    provider_timeout = float(
        os.getenv("DAILY_PROVIDER_TIMEOUT_SECONDS_BZZOIRO", os.getenv("DAILY_PROVIDER_TIMEOUT_SECONDS", "300"))
    )
    deadline = min(
        time.monotonic() + 60, getattr(provider, "_native_fetch_started", time.monotonic()) + provider_timeout - 10
    )
    blocked = False
    async with BudgetedAsyncClient(timeout=8) as client:

        async def get(path, params=None, ttl=600):
            nonlocal blocked
            params = params or {}
            digest = hashlib.sha256((path + json.dumps(params, sort_keys=True)).encode()).hexdigest()[:24]
            cache = Path(".data/provider_cache") / ("bzzoiro-v2-native-" + digest + ".json")
            saved = read_json(cache, {})
            observed = parse_time(saved.get("observed_at"))
            if observed and timedelta(0) <= now - observed <= timedelta(seconds=ttl):
                report["cache_hits"] += 1
                return saved.get("payload"), observed
            remaining = deadline - time.monotonic()
            if (
                blocked
                or remaining <= 0
                or report["requests"] >= limit
                or (provider.max_http_requests and provider._requests_used >= provider.max_http_requests)
            ):
                skip("blocked" if blocked else "deadline" if remaining <= 0 else "request_budget")
                return None, None
            report["requests"] += 1
            stats["requests"] += 1
            provider._requests_used += 1
            try:
                response = await asyncio.wait_for(
                    client.get(BASE + path, params=params, headers={"Authorization": "Token " + provider.api_key}),
                    timeout=min(8, remaining),
                )
                if response.status_code in {401, 402, 403, 429}:
                    blocked = True
                    report["blocked_status"] = response.status_code
                if response.status_code != 200:
                    report["errors"] += 1
                    return None, None
                payload = response.json()
                stamp = datetime.now(UTC)
                write_json(cache, {"observed_at": stamp.isoformat(), "payload": payload})
                return payload, stamp
            except (TimeoutError, OSError, ValueError, httpx.HTTPError) as exc:
                report["errors"] += 1
                report["last_error_type"] = type(exc).__name__
                return None, None

        predictions = []
        # The API declares date-time bounds: equal date-only strings formed an
        # empty same-day interval and could return midnight/past predictions.
        start = now.isoformat(timespec="seconds")
        end = (max(m.commence_time for m in targets) + timedelta(seconds=1)).isoformat(timespec="seconds")
        offset, seen = 0, set()
        for _ in range(4):
            payload, observed = await get(
                "/predictions/",
                {"date_from": start, "date_to": end, "status": "upcoming", "limit": 200, "offset": offset},
            )
            report["response_shape"] = sorted(payload)[:20] if isinstance(payload, dict) else type(payload).__name__
            batch = (
                payload.get("results", [])
                if isinstance(payload, dict)
                else payload
                if isinstance(payload, list)
                else []
            )
            if not isinstance(batch, list):
                skip("invalid_results_shape")
                break
            if not batch:
                break
            signature = json.dumps(batch, sort_keys=True)
            if signature in seen:
                break
            seen.add(signature)
            predictions.extend((p, observed) for p in batch if isinstance(p, dict))
            if not isinstance(payload, dict) or not payload.get("next"):
                break
            offset += len(batch)
        report["prediction_records"] = len(predictions)
        report["prediction_sample"] = [
            {
                "id": p.get("id"),
                "event": event_identity(p["event"]) if isinstance(p.get("event"), dict) else None,
                "fields": sorted(p)[:20],
            }
            for p, _ in predictions[:3]
        ]
        report["target_sample"] = [
            {"home": m.home_team, "away": m.away_team, "kickoff": m.commence_time.isoformat()} for m in targets
        ]
        for match in targets:
            found = [
                (p, at)
                for p, at in predictions
                if isinstance(p.get("event"), dict)
                and direct_fixture_identity(match, event_identity(p["event"]), tolerance_hours=0.25)
            ]
            # Multiple model records / event IDs are not resolved by confidence rank.
            if len(found) != 1:
                skip("no_exact_fixture" if not found else "ambiguous_fixture")
                continue
            report["fixtures_matched"] += 1
            prediction, observed = found[0]
            event = prediction["event"]
            sid = str(event.get("id") or "")
            if (
                not sid.isdecimal()
                or int(sid) <= 0
                or event.get("status") in {"cancelled", "postponed", "live", "finished", "unresolved"}
            ):
                report["rejected"] += 1
                skip("invalid_event_id_or_status")
                continue
            try:
                flat = flatten_prediction(prediction)
            except (AttributeError, TypeError, ValueError, OverflowError):
                flat = None
            if flat is None:
                report["rejected"] += 1
                skip("invalid_sporting_projection")
                continue
            normalized_event = {
                **event,
                "home_team": event_identity(event)["home"],
                "away_team": event_identity(event)["away"],
            }
            context = provider._prediction_to_context(flat, normalized_event, "exact")
            context.details.update(
                daily_source_observed_at=observed.isoformat(),
                bzzoiro_api_version="v2",
                projection_basis="provider_model",
                shot_xg_verified=False,
            )
            reasons = admit_context("bzzoiro", match, context, observed.isoformat(), datetime.now(UTC))
            if reasons:
                report["rejected"] += 1
                for reason in reasons:
                    skip(reason)
                continue
            old = contexts.get(match.match_key)
            old_time = (
                parse_time(
                    old.details.get("bzzoiro_prediction_updated_at") or old.details.get("bzzoiro_prediction_created_at")
                )
                if old
                else None
            )
            new_time = parse_time(flat.get("updated_at") or flat.get("created_at"))
            if old and old_time and new_time and old_time > new_time:
                skip("older_than_existing")
                continue
            contexts[match.match_key] = context
            report["contexts"] += 1
            sid = str(event.get("id") or "")
            if not sid.isdecimal():
                continue
            if report["detail_attempts"] >= detail_limit:
                skip("optional_detail_limit")
                continue
            report["detail_attempts"] += 1
            detail, _ = await get("/events/" + sid + "/", ttl=3600)
            if (
                not isinstance(detail, dict)
                or str(detail.get("id")) != sid
                or not direct_fixture_identity(match, event_identity(detail), tolerance_hours=0.25)
            ):
                continue
            report["details"] += 1
            context.payload["event"] = {
                **detail,
                "home_team": event_identity(detail)["home"],
                "away_team": event_identity(detail)["away"],
            }
            # Retain facts for reporting/research, without unvalidated model bonuses.
            context.details["bzzoiro_fixture_facts"] = {
                k: detail[k]
                for k in (
                    "venue",
                    "weather",
                    "neutral_ground",
                    "derby",
                    "travel_distance",
                    "round",
                    "group",
                    "is_neutral_ground",
                    "is_local_derby",
                    "travel_distance_km",
                    "round_number",
                    "group_name",
                )
                if k in detail
            }
            if os.getenv("BZZOIRO_V2_NATIVE_EXTRA_FACTS_ENABLED", "false").lower() == "true":
                availability, _ = await get("/events/" + sid + "/availability/", ttl=600)
                if isinstance(availability, dict) and str(availability.get("event_id")) == sid:
                    context.details["bzzoiro_available"] = availability.get("available", {})
                    if (availability.get("available") or {}).get("lineups") and availability.get(
                        "lineup_status"
                    ) == "confirmed":
                        lineup, _ = await get("/events/" + sid + "/lineups/", ttl=600)
                        if (
                            isinstance(lineup, dict)
                            and str(lineup.get("event_id")) == sid
                            and lineup.get("lineup_status") == "confirmed"
                        ):
                            context.details["bzzoiro_confirmed_lineups"] = lineup.get("lineups")
                h2h, _ = await get("/events/" + sid + "/h2h/", ttl=21600)
                if isinstance(h2h, dict) and isinstance(h2h.get("recent_matches"), list):
                    context.details["bzzoiro_h2h"] = h2h
            league = detail.get("league") or {}
            league_id = detail.get("league_id") or (league.get("id") if isinstance(league, dict) else None)
            season = detail.get("season") or {}
            season_id = detail.get("season_id") or (season.get("id") if isinstance(season, dict) else None)
            if not str(league_id or "").isdecimal() or not str(season_id or "").isdecimal():
                continue
            table, _ = await get("/leagues/" + str(league_id) + "/standings/", {"season_id": season_id}, ttl=7200)
            groups = table.get("groups") if isinstance(table, dict) else None
            tables = (
                [g.get("standings", []) for g in groups if isinstance(g, dict)]
                if isinstance(groups, list)
                else [table.get("standings", []) if isinstance(table, dict) else []]
            )
            # Require both explicit team IDs within one unambiguous table/group.
            home_id, away_id = detail.get("home_team_id"), detail.get("away_team_id")
            team_ids = []
            for side, explicit, name in [("home", home_id, match.home_team), ("away", away_id, match.away_team)]:
                team = detail.get(side + "_team_obj")
                if (
                    explicit is None
                    and isinstance(team, dict)
                    and same_team_identity(name, [str(team.get("name") or "")])
                ):
                    explicit = team.get("id")
                team_ids.append(explicit)
            home_id, away_id = team_ids
            pairs = []
            for group in tables:
                home = [r for r in group if str(r.get("team_id")) == str(home_id) and home_id is not None]
                away = [r for r in group if str(r.get("team_id")) == str(away_id) and away_id is not None]
                if len(home) == len(away) == 1:
                    pairs.append({"home": home[0], "away": away[0]})
            if len(pairs) == 1:
                context.details["bzzoiro_standings"] = pairs[0]
                report["standings"] += 1
    stats["contexts_built"] = len(contexts)
