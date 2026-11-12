"""Team-ID-bound regulation history for thin upcoming fixtures."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from app.services.api_budget import BudgetedAsyncClient
from app.services.daily_match_registry import parse_time, read_json, write_json
from app.services.fixture_identity import direct_fixture_identity, history_team_name, same_history_team_identity


def verified_fixture(match):
    """A stored discovery receipt binds IDs only to its original exact fixture."""
    metadata = getattr(match, "metadata", None) or {}
    row = metadata.get("sstats_fixture") or {}
    if not isinstance(row, dict) or not all(isinstance(row.get(side + "Team"), dict) for side in ("home", "away")):
        return None
    sid = str(row.get("id") or "")
    ids = metadata.get("provider_source_ids") or metadata.get("day_inventory_source_ids") or {}
    start = parse_time(row.get("date"))
    raw = row.get("dateUtc")
    if start is None and isinstance(raw, (int, float)) and not isinstance(raw, bool) and 946684800 <= raw <= 4102444800:
        start = datetime.fromtimestamp(raw, UTC)
    if (
        sid.isdecimal()
        and int(sid) > 0
        and str(ids.get("sstats")) == sid
        and direct_fixture_identity(
            match,
            {
                "home": row["homeTeam"].get("name"),
                "away": row["awayTeam"].get("name"),
                "commence_time": start.isoformat() if start else None,
            },
            tolerance_hours=0.25,
        )
    ):
        return row
    return None


def team_bindings(matches, rows):
    """Bind only unique IDs witnessed with a compatible name and squad."""
    rows = list(rows) + [row for match in matches if (row := verified_fixture(match)) is not None]
    names_by_id = {}
    for row in rows:
        for side in ("home", "away"):
            team = row.get(side + "Team")
            if not isinstance(team, dict):
                continue
            sid, name = str(team.get("id") or ""), team.get("name")
            if sid.isdecimal() and int(sid) > 0 and isinstance(name, str) and name.strip():
                names_by_id.setdefault(sid, set()).add(name)
    bindings = {}
    for match in matches:
        for name in (history_team_name(match, "home"), history_team_name(match, "away")):
            candidates = [
                sid for sid, names in names_by_id.items() if any(same_history_team_identity(name, [n]) for n in names)
            ]
            if len(candidates) == 1:
                sid = candidates[0]
                if all(same_history_team_identity(name, [n]) for n in names_by_id[sid]):
                    bindings[sid] = name
    return bindings


async def enrich_team_form(provider, matches, seed_rows, stats, preview):
    """No unfiltered scan, inferred team IDs or refreshed cache timestamps."""
    report = stats["targeted_history"] = {
        "targets": len(matches),
        "bound_teams": 0,
        "requests": 0,
        "rows": 0,
        "rejected_rows": 0,
        "cache_hits": 0,
        "contexts": 0,
    }
    # Bind before applying the configurable fixture cap. Unsupported early
    # fixtures must not prevent a later fixture with two known IDs being tried.
    bindings = team_bindings(matches, seed_rows)
    from app.services.sstats_team_lookup import discover

    discovered, catalogue = await discover(provider, matches, bindings, stats)
    bindings.update(discovered)
    report["available_bound_teams"] = len(bindings)
    eligible = [
        m
        for m in sorted(matches, key=lambda m: m.commence_time)
        if all(
            any(same_history_team_identity(name, [bound]) for bound in bindings.values()) for name in (history_team_name(m, "home"), history_team_name(m, "away"))
        )
    ]
    report["eligible_targets"] = len(eligible)
    targets = eligible[: min(100, max(8, int(os.getenv("DAILY_SSTATS_TARGETED_MATCH_LIMIT", "32"))))]
    report["selected_targets"] = len(targets)
    bindings = (
        {
            sid: name
            for sid, name in bindings.items()
            if any(same_history_team_identity(name, [history_team_name(m, "home"), history_team_name(m, "away")]) for m in targets)
        }
        if targets
        else bindings
    )
    report["bound_teams"] = len(bindings)
    request_limit = min(16, max(0, int(os.getenv("DAILY_SSTATS_TARGETED_REQUEST_LIMIT", "4"))))
    if not bindings:
        report["stop_reason"] = "no_unique_team_ids"
        return {}
    if not targets:
        report["stop_reason"] = "no_fully_bound_fixture"
        return {}
    if request_limit == 0:
        report["stop_reason"] = "request_limit"
        return {}
    now = datetime.now(UTC)
    params = {
        "Team": ",".join(sorted(bindings, key=int)),
        "Ended": "true",
        "Order": -1,
        "From": (now - timedelta(days=120)).date().isoformat(),
        # SStats accepts second precision, not Python's default microseconds.
        "To": now.isoformat(timespec="seconds"),
        "TimeZone": 0,
        "Limit": 1000,
    }
    # A stable daily key; To remains the original receipt's cutoff on cache hits.
    digest = hashlib.sha256(json.dumps({**params, "To": now.date().isoformat()}, sort_keys=True).encode()).hexdigest()[
        :24
    ]
    cache = Path(".data/provider_cache") / ("sstats-targeted-v1-" + digest + ".json")
    saved = read_json(cache, {})
    observed = parse_time(saved.get("observed_at"))
    if observed and timedelta(0) <= now - observed < timedelta(minutes=60) and isinstance(saved.get("rows"), list):
        raw_rows = saved["rows"]
        report["cache_hits"] = 1
    else:
        raw_rows, seen = [], set()
        observed = None
        start_requests = int(stats.get("requests", 0))
        ceiling = start_requests + request_limit
        complete = False
        timeout = min(90, max(1, float(os.getenv("DAILY_SSTATS_TARGETED_TIMEOUT_SECONDS", "25"))))
        try:
            async with asyncio.timeout(timeout):
                async with BudgetedAsyncClient(timeout=min(8, timeout)) as client:
                    offset = 0
                    while int(stats.get("requests", 0)) < ceiling:
                        response = await provider._request_with_retries(
                            client,
                            provider.url,
                            params={**params, "Offset": offset, "apikey": provider.settings.sstats_api_key},
                            stats=stats,
                            request_ceiling=ceiling,
                        )
                        if response is None:
                            report["stop_reason"] = "provider_budget_or_error"
                            break
                        observed = observed or datetime.now(UTC)
                        payload = response.json()
                        batch = (
                            payload.get("data", payload.get("results", [])) if isinstance(payload, dict) else payload
                        )
                        if not isinstance(batch, list) or any(not isinstance(r, dict) for r in batch):
                            report["stop_reason"] = "invalid_response_shape"
                            break
                        if not batch:
                            complete = True
                            break
                        signature = json.dumps(batch, sort_keys=True)
                        if signature in seen:
                            report["stop_reason"] = "repeated_page"
                            break
                        seen.add(signature)
                        raw_rows.extend(batch)
                        # A capped short page is not evidence that history ended.
                        offset += len(batch)
                    else:
                        report["stop_reason"] = "request_limit"
        except TimeoutError:
            report["stop_reason"] = "timeout"
        except (ValueError, TypeError) as exc:
            report["stop_reason"] = type(exc).__name__
        finally:
            report["requests"] = int(stats.get("requests", 0)) - start_requests
        if complete and observed:
            write_json(cache, {"observed_at": observed.isoformat(), "rows": raw_rows})
    by_id, conflicts = {}, set()
    for row in raw_rows:
        if not isinstance(row, dict):
            report["rejected_rows"] += 1
            continue
        start = provider._extract_start(row)
        sid = str(row.get("id") or "")
        bound = False
        bad_identity = False
        for side in ("home", "away"):
            team = row.get(side + "Team") or {}
            if not isinstance(team, dict):
                continue
            expected = bindings.get(str(team.get("id")))
            # A provider ignoring Team must not smuggle another target's form
            # through a correct opponent ID and an incompatible team ID.
            if any(
                str(team.get("id")) != bid and same_history_team_identity(name, [str(team.get("name") or "")])
                for bid, name in bindings.items()
            ):
                bad_identity = True
            if expected:
                matches_name = same_history_team_identity(expected, [str(team.get("name") or "")])
                bound = bound or matches_name
                bad_identity = bad_identity or not matches_name
        status = row.get("status")
        scores = [provider._extract_result(row, side) for side in ("home", "away")]
        if (
            not bound
            or bad_identity
            or not sid.isdecimal()
            or int(sid) <= 0
            or start is None
            or start >= now
            or (status is not None and str(status) not in {"8", "9", "10", "17", "18"})
            or any(v is None or not math.isfinite(v) or v < 0 or not v.is_integer() for v in scores)
        ):
            report["rejected_rows"] += 1
            continue
        signature = (
            start.isoformat(),
            provider._extract_team_name(row, "home"),
            provider._extract_team_name(row, "away"),
            *scores,
        )
        if sid in by_id and by_id[sid][0] != signature:
            conflicts.add(sid)
        by_id.setdefault(sid, (signature, row))
    valid = [row for sid, (_, row) in by_id.items() if sid not in conflicts]
    report["conflicting_ids"] = len(conflicts)
    report["rows"] = len(valid)
    if valid and observed:
        from app.services.team_result_archive import retain_history

        valid, report["team_result_archive"] = retain_history(provider, valid, observed.isoformat())
    contexts = provider._build_team_form_contexts(targets, valid, preview) if valid else {}
    for context in contexts.values():
        context.details["daily_source_observed_at"] = observed.isoformat()
        context.details["sstats_history_scope"] = "verified_team_ids"
    for match in targets:
        if match.match_key in contexts and match.match_key in catalogue:
            contexts[match.match_key].details["sstats_team_catalogue"] = catalogue[match.match_key]
    report["contexts"] = len(contexts)
    return contexts
