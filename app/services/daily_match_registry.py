"""Persistent exact match identities, provider evidence and gap-first work queue."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from app.schemas import Match, MatchContext, Offer
from app.services.context_admission import admit_context, identity_reasons
from app.services.fixture_identity import squad_identity, team_identity_key
from app.services.market_period import is_full_match_market

TZ = ZoneInfo("Europe/Moscow")
SSTATS_HISTORY_IDENTITY_REVISION = 2


def parse_time(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt.astimezone(UTC) if dt.tzinfo else None
    except (TypeError, ValueError):
        return None


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def write_json(path, payload):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n")
    temp.replace(path)


def match_identity(match: Match) -> str:
    pair = sorted([team_identity_key(match.home_team), team_identity_key(match.away_team)])
    identity = "|".join([match.sport_key, *pair, match.commence_time.astimezone(UTC).isoformat()])
    return hashlib.sha256(identity.encode()).hexdigest()[:24]


class DailyMatchRegistry:
    def __init__(self, path: Path, now: datetime):
        self.path, self.now = Path(path), now.astimezone(UTC)
        self.date = now.astimezone(TZ).date().isoformat()
        payload = read_json(path, {})
        # Re-key only explicit youth/reserve/women entries. Adult IDs are stable.
        # Retain publication acknowledgements but discard potentially conflated
        # cached evidence and retry state. Financial ledgers are never rewritten.
        remapped = {}
        for old_key, entry in list(payload.get("matches", {}).items()):
            row = dict(entry["match"])
            row["commence_time"] = parse_time(row["commence_time"])
            if row["commence_time"] is None:
                continue
            new_key = match_identity(Match(**row))
            if new_key == old_key:
                continue
            if new_key in payload["matches"]:
                raise ValueError("Conflicting registry identity during squad migration")
            payload["matches"][new_key] = payload["matches"].pop(old_key)
            entry["evidence"], entry["attempts"] = {}, {}
            if entry.get("publication"):
                entry["publication"]["match_id"] = new_key
            remapped[old_key] = new_key
        for publication in payload.get("published", []):
            publication["match_id"] = remapped.get(publication.get("match_id"), publication.get("match_id"))
        # Retain acknowledged-match markers even when the sending day rolls over.
        for publication in payload.get("published", []):
            entry = payload.get("matches", {}).get(publication.get("match_id"))
            if entry is not None:
                entry["publication"] = publication
        if payload.get("date_local") == self.date:
            self.data = payload
        else:
            retained = {
                key: row
                for key, row in payload.get("matches", {}).items()
                if parse_time(row["match"]["commence_time"])
                and parse_time(row["match"]["commence_time"]).astimezone(TZ).date().isoformat() == self.date
            }
            self.data = {"date_local": self.date, "matches": retained, "published": [], "issues": []}

    def sync(self, matches: list[Match]):
        entries = self.data["matches"]
        for match in sorted(matches, key=lambda m: m.commence_time):
            day = match.commence_time.astimezone(TZ).date().isoformat()
            lookahead = day != self.date
            if lookahead and not (
                self.now
                < match.commence_time
                <= self.now
                + timedelta(hours=min(48, max(4, float(os.getenv("DAILY_DATA_COLLECTION_WINDOW_HOURS", "24")))))
            ):
                continue
            key = match_identity(match)
            alias = self.data.get("aliases", {}).get(key)
            if alias in entries:
                key = alias
            day_count = sum(
                parse_time(row["match"]["commence_time"]).astimezone(TZ).date().isoformat() == day
                for row in entries.values()
            )
            if key not in entries and day_count >= 300:
                continue
            ids = match.metadata.get("provider_source_ids") or match.metadata.get("day_inventory_source_ids") or {}
            entry = entries.setdefault(
                key, {"match": asdict(match), "provider_ids": {}, "evidence": {}, "attempts": {}}
            )
            # Provider IDs belong to this exact team pair and kickoff, not a loose daily key.
            for provider, event_id in ids.items():
                existing = entry["provider_ids"].get(provider)
                if existing and str(existing) != str(event_id):
                    self.data["issues"].append({"kind": "provider_id_conflict", "match_id": key, "provider": provider})
                    continue
                entry["provider_ids"][provider] = event_id
            entry["match"]["metadata"].update(match.metadata)
        self._merge_exact_alias_duplicates()
        self.save()

    def _merge_exact_alias_duplicates(self):
        from app.services.fixture_identity import same_team_identity

        entries = self.data["matches"]
        keys = list(entries)
        for index, keep in enumerate(keys):
            if keep not in entries:
                continue
            first = entries[keep]
            a = first["match"]
            for drop in keys[index + 1 :]:
                if drop not in entries:
                    continue
                second = entries[drop]
                b = second["match"]
                reviewed = {frozenset(("js djidjel", "js jijel")), frozenset(("rc arbaa", "rc arba"))}
                ids, incoming = first.get("provider_ids", {}), second.get("provider_ids", {})
                shared = any(
                    str(ids[p]) == str(incoming[p]) and str(ids[p]).strip() for p in ids.keys() & incoming.keys()
                )
                if not shared and not any(
                    frozenset((a[side].lower(), b[side].lower())) in reviewed for side in ("home_team", "away_team")
                ):
                    continue
                if (
                    a["sport_key"] != b["sport_key"]
                    or parse_time(a["commence_time"]) != parse_time(b["commence_time"])
                    or not same_team_identity(a["home_team"], [b["home_team"]])
                    or not same_team_identity(a["away_team"], [b["away_team"]])
                ):
                    continue
                ids, incoming = first.get("provider_ids", {}), second.get("provider_ids", {})
                if any(str(ids[p]) != str(incoming[p]) for p in ids.keys() & incoming.keys()):
                    continue
                if first.get("publication") and second.get("publication"):
                    continue  # Multiple acknowledgements require explicit review.
                first.setdefault("provider_ids", {}).update(incoming)
                for name, evidence in second.get("evidence", {}).items():
                    current = first.setdefault("evidence", {}).get(name)
                    if current is None or str(evidence.get("observed_at", "")) > str(current.get("observed_at", "")):
                        first["evidence"][name] = evidence
                # Evidence is revalidated against the retained exact fixture by cached().
                if second.get("publication"):
                    first["publication"] = {**second["publication"], "match_id": keep}
                for publication in self.data.get("published", []):
                    if publication.get("match_id") == drop:
                        publication["match_id"] = keep
                del entries[drop]
                self.data.setdefault("aliases", {})[drop] = keep
                for alias, target in list(self.data["aliases"].items()):
                    if target == drop:
                        self.data["aliases"][alias] = keep
                self.data["issues"].append({"kind": "exact_alias_duplicate_merged", "kept": keep, "removed": drop})

    def matches(self):
        result = []
        for entry in self.data["matches"].values():
            row = dict(entry["match"])
            row["commence_time"] = parse_time(row["commence_time"])
            row["metadata"] = dict(row.get("metadata") or {})
            ids = entry["provider_ids"]
            row["metadata"].update({"provider_source_ids": ids, "day_inventory_source_ids": ids, "source_ids": ids})
            for provider, event_id in ids.items():
                row["metadata"][provider + "_id"] = event_id
                row["metadata"][provider + "_event_id"] = event_id
            if ids.get("sstats"):
                row["metadata"]["sstats_game_id"] = ids["sstats"]
            result.append(Match(**row))
        return result

    def actionable_gaps(self):
        rows = []
        for match in self.matches():
            lead = (match.commence_time - self.now).total_seconds() / 60
            if not 30 <= lead <= 240:
                continue
            line, context = self.has_role(match, "offers"), self.has_role(match, "context")
            if line and context:
                continue
            entry = self.data["matches"][match_identity(match)]
            rows.append(
                {
                    "match_id": match_identity(match),
                    "home": match.home_team,
                    "away": match.away_team,
                    "kickoff_utc": match.commence_time.isoformat(),
                    "missing": [role for role, ready in [("offers", line), ("context", context)] if not ready],
                    "provider_ids": entry.get("provider_ids", {}),
                    "attempts": {
                        name: {key: attempt.get(key) for key in ("at", "retry_after", "result", "diagnostic")}
                        for name, attempt in entry.get("attempts", {}).items()
                    },
                    "unattempted_roles": [
                        role
                        for role in ("offers", "context")
                        if not any(name.endswith(":" + role) for name in entry.get("attempts", {}))
                    ],
                }
            )
        return sorted(rows, key=lambda row: row["kickoff_utc"])

    def identity_review(self):
        """Potential feed-name gaps for review only; never transfer evidence."""
        from app.services.fixture_identity import same_team_identity

        groups = {}
        for match in self.matches():
            if timedelta(minutes=30) <= match.commence_time - self.now <= timedelta(hours=4):
                groups.setdefault((match.sport_key, match.commence_time), []).append(match)
        reviews = []
        for matches in groups.values():
            for index, first in enumerate(matches):
                for second in matches[index + 1 :]:
                    home = same_team_identity(first.home_team, [second.home_team])
                    away = same_team_identity(first.away_team, [second.away_team])
                    if not (home or away):
                        continue
                    reviews.append(
                        {
                            "kickoff_utc": first.commence_time.isoformat(),
                            "reason": "unmerged_fixture_records",
                            "automatic_merge": False,
                            "matches": [
                                {
                                    "match_id": match_identity(m),
                                    "home": m.home_team,
                                    "away": m.away_team,
                                    "provider_ids": self.data["matches"][match_identity(m)]["provider_ids"],
                                    "has_line": self.has_role(m, "offers"),
                                    "has_context": self.has_role(m, "context"),
                                }
                                for m in (first, second)
                            ],
                        }
                    )
        return reviews

    def cached(self, provider: str, role: str, match: Match):
        evidence = self.data["matches"].get(match_identity(match), {}).get("evidence", {}).get(provider + ":" + role)
        if not evidence:
            return None
        observed = parse_time(evidence["observed_at"])
        minutes_to_start = (match.commence_time - self.now).total_seconds() / 60
        ttl = (
            (15 if minutes_to_start <= 240 else 120 if minutes_to_start <= 480 else 240)
            if role == "offers"
            else 120
            if provider == "weather"
            else 360
        )
        if observed is None or not -timedelta(seconds=5) <= self.now - observed <= timedelta(minutes=ttl):
            return None
        try:
            if role == "offers":
                return [Offer(**row) for row in evidence["payload"] if is_full_match_market(row)]
            context = MatchContext(**evidence["payload"])
            if provider != "weather" and admit_context(provider, match, context, evidence["observed_at"], self.now):
                return None
            if (
                provider == "sstats"
                and context.source == "sstats_form"
                and os.getenv("PUBLICATION_PROFILE", "").lower() == "daily_quality"
            ):
                if context.details.get("sstats_score_basis") != "regulation-preferred-v2":
                    return None
                from app.providers.sstats import SStatsContextProvider, conflicting_history_indices

                if any(
                    conflicting_history_indices(context.payload.get(side) or [])
                    for side in ("home_recent", "away_recent")
                ):
                    return None
                context = SStatsContextProvider.chronological_form_context(context)
            if (
                provider == "sstats"
                and context.source == "sstats"
                and os.getenv("PUBLICATION_PROFILE", "").lower() == "daily_quality"
            ):
                from app.providers.sstats import market_only_context

                # Also invalidate persisted entries produced before the provenance marker.
                if market_only_context(context):
                    return None
            if (
                provider == "weather"
                and os.getenv("PUBLICATION_PROFILE", "").lower() == "daily_quality"
                and not context.details.get("weather_location_verified")
            ):
                return None
            if provider != "weather" and (
                context.source in {"bzzoiro_event_odds", "market_implied_xg", "market_signal"}
                or context.details.get("bzzoiro_event_only_context")
            ):
                return None
            context.details["daily_evidence_observed_at"] = evidence["observed_at"]
            context.details["daily_evidence_provider"] = provider
            return context
        except (TypeError, ValueError):
            return None

    def has_role(self, match: Match, role: str):
        providers = {
            name.split(":")[0]
            for name in self.data["matches"][match_identity(match)]["evidence"]
            if name.endswith(":" + role) and (role != "context" or not name.startswith("weather:"))
        }
        return any(self.cached(p, role, match) for p in providers)

    def targets(self, provider: str, role: str, *, limit: int = 60, eligible=None, bulk=False):
        ranked = []
        for match in self.matches():
            if eligible is not None and not eligible(match):
                continue
            minutes = (match.commence_time - self.now).total_seconds() / 60
            if minutes < 30:
                continue
            refresh_soon = False
            current = self.cached(provider, role, match)
            if current:
                observed = parse_time(current.details.get("daily_evidence_observed_at")) if role == "context" else None
                buffer = min(120, max(0, int(os.getenv("DAILY_CONTEXT_REFRESH_BUFFER_MINUTES", "30"))))
                refresh_soon = (
                    role == "context"
                    and provider != "weather"
                    and minutes <= 240
                    and observed is not None
                    and self.now - observed >= timedelta(minutes=360 - buffer)
                )
                if not refresh_soon:
                    continue
            entry = self.data["matches"][match_identity(match)]
            attempt = entry["attempts"].get(provider + ":" + role, {})
            retry = parse_time(attempt.get("retry_after"))
            if retry and self.now < retry:
                attempted = parse_time(attempt.get("at"))
                urgent_odds_retry = (
                    role == "offers"
                    and (minutes <= 120 or minutes <= 240 and self.has_role(match, "context"))
                    and attempt.get("result") == "empty"
                    and attempted is not None
                    and self.now - attempted >= timedelta(minutes=30)
                    and retry - attempted <= timedelta(minutes=120)
                )
                urgent_context_retry = (
                    role == "context"
                    and provider != "weather"
                    and minutes <= 240
                    and attempt.get("result") == "empty"
                    and attempted is not None
                    and self.now - attempted >= timedelta(minutes=30)
                    and retry - attempted <= timedelta(minutes=120)
                    and (refresh_soon or not self.has_role(match, "context"))
                )
                repaired_squad_retry = (
                    provider == "sstats"
                    and role == "context"
                    and attempt.get("result") == "empty"
                    and attempt.get("history_identity_revision") != SSTATS_HISTORY_IDENTITY_REVISION
                    and any(any(squad_identity(team)) for team in (match.home_team, match.away_team))
                )
                if not (urgent_odds_retry or urgent_context_retry or repaired_squad_retry):
                    continue
            lane = 0 if minutes <= 240 else 1 if minutes <= 480 else 2
            has_current_role = self.has_role(match, role)
            other_role = "offers" if role == "context" else "context"
            completes_pair = not has_current_role and self.has_role(match, other_role)
            # With a two-hour cadence and 30-minute publication lead, these
            # fixtures need this run; a later regular run would be too late.
            before_next_run = minutes <= 150
            ranked.append(((lane, has_current_role, not before_next_run, not completes_pair, minutes), match))
        ranked.sort(key=lambda item: item[0])
        # Close near window first, then prefetch next and rotate through the daily backlog.
        near = [m for key, m in ranked if key[0] == 0]
        nxt = [m for key, m in ranked if key[0] == 1][
            : limit if bulk else max(0, int(os.getenv("DAILY_PREFETCH_NEXT_MATCH_LIMIT", "60")))
        ]
        later = [m for key, m in ranked if key[0] == 2][
            : limit if bulk else max(0, int(os.getenv("DAILY_PREFETCH_BACKLOG_MATCH_LIMIT", "60")))
        ]
        return (near + nxt + later)[: max(0, limit)]

    def record(self, provider, role, targets, data, stats, *, observed_at=None):
        fetched = observed_at or datetime.now(UTC)
        self.now = fetched
        counts = {"accepted": 0, "rejected": 0, "deferred": 0, "empty": 0, "blocked": 0}
        for match in targets:
            entry = self.data["matches"][match_identity(match)]
            value = data.get(match.match_key)
            rejected = False
            if role == "context" and provider != "weather" and value:
                observed = value.details.get("daily_source_observed_at") or fetched.isoformat()
                reasons = admit_context(provider, match, value, observed, fetched)
                if reasons:
                    value = None
                    counts["rejected"] += 1
                    rejected = True
                    # A bad response must not evict still-valid previous evidence.
                    if self.cached(provider, role, match) is None:
                        entry["evidence"].pop(provider + ":" + role, None)
                    self.data["issues"].append(
                        {"provider": provider, "reason": reasons[0], "match_id": match_identity(match)}
                    )
            if role == "offers" and value:
                value = [offer for offer in value if is_full_match_market(offer)]
            if value:
                counts["accepted"] += 1
                payload = [asdict(offer) for offer in value] if role == "offers" else asdict(value)
                if role == "offers":
                    for row in payload:
                        row["metadata"].update(
                            {"fetched_at_utc": fetched.isoformat(), "registry_match_id": match_identity(match)}
                        )
                observed = fetched.isoformat()
                if role == "context":
                    observed = payload.get("details", {}).get("daily_source_observed_at") or observed
                entry["evidence"][provider + ":" + role] = {"observed_at": observed, "payload": payload}
            # Empty or unavailable endpoints are retried in a later run, not in a tight loop.
            near = 30 <= (match.commence_time - fetched).total_seconds() / 60 <= 120
            delay = 0 if value else 30 if role == "offers" and near else 120
            unavailable = bool(stats.get("auth_error") or stats.get("plan_restriction"))
            if unavailable:
                delay = 1440
            result = "data" if value else "unavailable" if unavailable else "empty"
            # A truncated batch or timed-out provider is not proof that a match
            # has no data. Keep it in the queue without a two-hour negative cache.
            if not value and not unavailable:
                if stats.get("rate_limited"):
                    result = "rate_limited"
                    delay = 120
                elif (
                    stats.get("budget_exhausted")
                    or stats.get("runtime_error")
                    or stats.get("response_errors")
                    or stats.get("history_extension_timeout")
                    or stats.get("reason") == "missing_api_key"
                ):
                    result = "deferred"
                    delay = 30
                    if not rejected:
                        counts["deferred"] += 1
                elif not rejected:
                    counts["empty"] += 1
            if not value and not rejected and result in {"unavailable", "rate_limited"}:
                counts["blocked"] += 1
            entry["attempts"][provider + ":" + role] = {
                "at": fetched.isoformat(),
                "retry_after": (fetched + timedelta(minutes=delay)).isoformat(),
                "result": result,
                **({"history_identity_revision": SSTATS_HISTORY_IDENTITY_REVISION}
                   if provider == "sstats" and role == "context" else {}),
                **({"diagnostic": stats["line_diagnostics"][match.match_key]}
                   if role == "offers" and match.match_key in stats.get("line_diagnostics", {}) else {}),
            }
        self.save()
        return counts

    def observation(self, match, role):
        times = []
        entry = self.data["matches"][match_identity(match)]
        for name, row in entry["evidence"].items():
            provider, kind = name.split(":", 1)
            if kind == role and (role != "context" or provider != "weather") and self.cached(provider, role, match):
                times.append(row["observed_at"])
        return max(times, default=None)

    def coverage(self):
        from app.providers.sstats import market_only_context

        result = {
            "inventory": sum(m.commence_time.astimezone(TZ).date().isoformat() == self.date for m in self.matches()),
            "line": 0,
            "context": 0,
            "ready": 0,
            "near": 0,
            "near_ready": 0,
            "form": 0,
            "standings": 0,
            "weather": 0,
            "collected_line": 0,
            "collected_context": 0,
            "collected_ready": 0,
            "future_matches": 0,
            "future_line": 0,
            "future_context": 0,
            "future_ready": 0,
            "near_two_book_lines": 0,
            "near_two_context_sources": 0,
            "near_two_book_and_context": 0,
            "lookahead": 0,
            "near_line": 0,
            "near_context": 0,
            "near_missing_line": 0,
            "near_missing_context": 0,
            "near_only_line": 0,
            "near_only_context": 0,
            "near_missing_both": 0,
        }
        for match in self.matches():
            line, context = self.has_role(match, "offers"), self.has_role(match, "context")
            near = 30 <= (match.commence_time - self.now).total_seconds() / 60 <= 240
            result["near"] += near
            result["near_ready"] += bool(near and line and context)
            result["near_line"] += bool(near and line)
            result["near_context"] += bool(near and context)
            result["near_missing_line"] += bool(near and not line)
            result["near_missing_context"] += bool(near and not context)
            result["near_only_line"] += bool(near and line and not context)
            result["near_only_context"] += bool(near and context and not line)
            result["near_missing_both"] += bool(near and not line and not context)
            future = match.commence_time > self.now
            result["future_matches"] += future
            result["future_line"] += bool(future and line)
            result["future_context"] += bool(future and context)
            result["future_ready"] += bool(future and line and context)
            evidence = self.data["matches"][match_identity(match)]["evidence"]
            if near:
                from app.utils import normalize_bookmaker_name

                books = {}
                providers = set()
                for name in evidence:
                    provider, role = name.split(":", 1)
                    value = self.cached(provider, role, match)
                    if role == "offers" and value:
                        for offer in value:
                            key = (offer.family, offer.selection.casefold(), offer.point, offer.team_side)
                            books.setdefault(key, set()).add(normalize_bookmaker_name(offer.bookmaker))
                    elif role == "context" and provider not in {"weather", "self_history"} and value:
                        providers.add(provider)
                two_books = any(len(group) >= 2 for group in books.values())
                result["near_two_book_lines"] += two_books
                result["near_two_context_sources"] += len(providers) >= 2
                result["near_two_book_and_context"] += two_books and len(providers) >= 2
            details = [
                row["payload"].get("details", {})
                for name, row in evidence.items()
                if name.endswith(":context") and self.cached(name.split(":")[0], "context", match)
            ]
            if near:
                form = any(
                    (d.get("home_recent_count") or 0) > 0 and (d.get("away_recent_count") or 0) > 0 for d in details
                )
                result["form"] += form or any(
                    "form" in str(field).lower() and value not in (None, "", [], {})
                    for d in details
                    for field, value in d.items()
                )
                result["standings"] += any(
                    bool(
                        d.get("thesportsdb_home_rank")
                        and d.get("thesportsdb_away_rank")
                        and not d.get("thesportsdb_partial_context")
                    )
                    or any(
                        any(token in str(field).lower() for token in ("standing", "table"))
                        and value not in (None, "", [], {}, False)
                        for field, value in d.items()
                    )
                    for d in details
                )
                result["weather"] += bool(self.cached("weather", "context", match))
            if match.commence_time.astimezone(TZ).date().isoformat() != self.date:
                result["lookahead"] += 1
                continue
            entry = self.data["matches"][match_identity(match)]
            collected_line = any(
                name.endswith(":offers") and any(is_full_match_market(offer) for offer in row["payload"])
                for name, row in entry["evidence"].items()
            )
            collected_context = any(
                name.endswith(":context")
                and not name.startswith("weather:")
                and row["payload"]
                and not (identity_reasons(name.split(":")[0], match, MatchContext(**row["payload"])))
                and not (name == "sstats:context" and market_only_context(MatchContext(**row["payload"])))
                for name, row in entry["evidence"].items()
            )
            result["collected_line"] += collected_line
            result["collected_context"] += collected_context
            result["collected_ready"] += bool(collected_line and collected_context)
            line, context = self.has_role(match, "offers"), self.has_role(match, "context")
            result["line"] += bool(line)
            result["context"] += bool(context)
            result["ready"] += bool(line and context)
        # A live SLA uses only the actionable window, never expired all-day prices.
        denominator = result["near"]
        result["near_ready_pct"] = round(100 * result["near_ready"] / denominator, 1) if denominator else None
        result["near_coverage_status"] = (
            "no_upcoming_matches"
            if not denominator
            else "target_95"
            if result["near_ready"] / denominator >= 0.95
            else "minimum_90"
            if result["near_ready"] / denominator >= 0.90
            else "below_target"
        )
        result["near_missing_to_95"] = max(0, (95 * denominator + 99) // 100 - result["near_ready"])
        result["inventory_shortfall"] = max(0, 300 - result["inventory"])
        return result

    def save(self):
        self.data["issues"] = self.data["issues"][-100:]
        write_json(self.path, self.data)
