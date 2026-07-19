from __future__ import annotations

"""Final runtime repairs for strict two-source day coverage.

The production chain has many compatibility wrappers.  This module is intentionally
installed near the end of that chain and fixes three concrete runtime failures:

* the Bzzoiro odds rescue called a keyword-only matcher positionally;
* cached provider evidence was only reusable through an exact runtime match key;
* Bzzoiro context collection spent the provider deadline on sequential event-detail
  requests instead of using the documented paginated v2 predictions endpoint.

Publication/value/movement guards are not changed here.
"""

import contextlib
import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from app.schemas import MatchContext, Offer
from app.utils import score_event_match as _real_score_event_match

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-strict-coverage-runtime-repair.json"
_INSTALLED = False


def _as_int(value: Any, default: int) -> int:
    try:
        return int(float(value))
    except Exception:
        return default


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
    except Exception:
        pass


def score_event_match_compat(*args: Any, **kwargs: Any):
    """Accept the old positional call shape and forward to the keyword-only API."""

    if args:
        if len(args) < 9:
            raise TypeError("legacy score_event_match requires at least 9 positional arguments")
        positional = {
            "sport": args[0],
            "match_home": args[1],
            "match_away": args[2],
            "match_start": args[3],
            "match_league": args[4],
            "event_home": args[5],
            "event_away": args[6],
            "event_start": args[7],
            "event_league": args[8],
        }
        positional.update(kwargs)
        return _real_score_event_match(**positional)
    return _real_score_event_match(**kwargs)


score_event_match_compat._harizon_positional_compat = True  # type: ignore[attr-defined]


def _install_score_match_compat() -> dict[str, Any]:
    try:
        from app.services import sstats_bzzoiro_odds_merge_patch as module

        module.score_event_match = score_event_match_compat
        os.environ["CORE_ODDS_PATCH_MATCH_LIMIT"] = "300"
        return {"status": "installed", "full_cohort_limit": 300}
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def _reconstruct_cached(role: str, raw: Any) -> Any | None:
    try:
        if role == "odds" and isinstance(raw, list):
            rows = [Offer(**row) for row in raw if isinstance(row, dict)]
            return rows or None
        if role == "context" and isinstance(raw, dict):
            return MatchContext(**raw)
    except Exception:
        return None
    return None


def _alias_cached_provider_data_factory(original: Any):
    from app.services import daily_coverage_ledger as ledger
    from app.services.daily_coverage_identity import identity_from_key, row_identities

    def cached_provider_data(provider_name: str, method_name: str, matches: list[Any]) -> dict[str, Any]:
        exact = original(provider_name, method_name, matches)
        result = dict(exact or {})
        missing = [m for m in matches if str(getattr(m, "match_key", "")) not in result]
        if not missing:
            return result

        provider = ledger.canonical_source(provider_name)
        role = "odds" if "offer" in method_name.lower() else "context"
        evidence = ledger.load(ledger.evidence_path(ledger.target_date()), {})
        rows = (
            evidence.get("matches")
            if isinstance(evidence, dict) and isinstance(evidence.get("matches"), dict)
            else {}
        )
        identity_index: dict[Any, list[dict[str, Any]]] = {}
        for key, value in rows.items():
            if not isinstance(value, dict):
                continue
            identity = identity_from_key(key)
            if identity is not None:
                identity_index.setdefault(identity, []).append(value)

        max_minutes = _as_int(
            os.getenv("DAILY_COVERAGE_ODDS_CACHE_MINUTES") if role == "odds" else os.getenv("DAILY_COVERAGE_CONTEXT_CACHE_MINUTES"),
            360 if role == "odds" else 1440,
        )
        cutoff = datetime.now(UTC) - timedelta(minutes=max_minutes)

        for match in missing:
            match_key = str(getattr(match, "match_key", ""))
            if not match_key:
                continue
            row = {
                "match_key": match_key,
                "home_team": getattr(match, "home_team", ""),
                "away_team": getattr(match, "away_team", ""),
                "commence_time": getattr(match, "commence_time", None),
            }
            candidates: list[dict[str, Any]] = []
            exact_row = rows.get(match_key)
            if isinstance(exact_row, dict):
                candidates.append(exact_row)
            for identity in row_identities(row, getattr(match, "commence_time", None)):
                candidates.extend(identity_index.get(identity, []))

            newest: tuple[datetime, Any] | None = None
            for candidate in candidates:
                bucket = candidate.get(role)
                item = bucket.get(provider) if isinstance(bucket, dict) else None
                if not isinstance(item, dict):
                    continue
                try:
                    updated = datetime.fromisoformat(
                        str(item.get("updated_at_utc") or "").replace("Z", "+00:00")
                    )
                    if updated.tzinfo is None:
                        updated = updated.replace(tzinfo=UTC)
                    updated = updated.astimezone(UTC)
                except Exception:
                    continue
                if updated < cutoff:
                    continue
                reconstructed = _reconstruct_cached(role, item.get("data"))
                if reconstructed is None:
                    continue
                if newest is None or updated > newest[0]:
                    newest = (updated, reconstructed)
            if newest is not None:
                result[match_key] = newest[1]
        return result

    cached_provider_data._harizon_alias_aware = True  # type: ignore[attr-defined]
    return cached_provider_data


def _install_alias_cache() -> dict[str, Any]:
    try:
        from app.services import daily_coverage_ledger as ledger
        from app.services import daily_coverage_runtime_boundary as boundary

        current = ledger.cached_provider_data
        if getattr(current, "_harizon_alias_aware", False):
            patched = current
            status = "already_installed"
        else:
            patched = _alias_cached_provider_data_factory(current)
            ledger.cached_provider_data = patched
            status = "installed"
        # The boundary imported the function symbol directly.  Rebind its global too.
        boundary.cached_provider_data = patched
        return {"status": status, "boundary_rebound": True}
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def _prediction_event_id(row: Any) -> str:
    if not isinstance(row, dict):
        return ""
    event = row.get("event")
    if isinstance(event, dict):
        value = event.get("id") or event.get("event_id")
        if value not in (None, ""):
            return str(value)
    value = row.get("event_id") or row.get("eventId")
    return "" if value in (None, "") else str(value)


async def _fetch_predictions_v2(
    provider: Any,
    client: httpx.AsyncClient,
    headers: dict[str, str],
    date_from: str,
    date_to: str,
    stats: dict[str, Any],
) -> list[dict[str, Any]]:
    page_size = min(200, max(20, _as_int(os.getenv("BZZOIRO_PREDICTIONS_PAGE_SIZE"), 200)))
    max_pages = max(1, _as_int(os.getenv("BZZOIRO_PREDICTIONS_MAX_PAGES"), 10))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    offset = 0
    for _ in range(max_pages):
        payload = await provider._get_json(
            client,
            "/predictions/",
            headers,
            {
                "status": "upcoming",
                "date_from": date_from,
                "date_to": date_to,
                "limit": page_size,
                "offset": offset,
            },
            stats,
        )
        batch = provider._rows(payload)
        if not batch:
            break
        for row in batch:
            marker = str(row.get("id") or _prediction_event_id(row) or len(rows))
            if marker in seen:
                continue
            seen.add(marker)
            rows.append(row)
        if len(batch) < page_size:
            break
        offset += page_size
    return rows


async def fetch_context_batch_predictions(self: Any, matches: list[Any]):
    """Build broad Bzzoiro context from v2 prediction pages before detail calls."""

    stats: dict[str, Any] = {
        "provider_version": "bzzoiro-v2-batch-context-2026-07-19",
        "api_version": "v2",
        "enabled": bool(getattr(self, "api_key", None)),
        "api_key_present": bool(getattr(self, "api_key", None)),
        "mode": "batch_predictions_first_detail_on_gap",
        "requests": 0,
        "response_errors": 0,
        "retry_attempts": 0,
        "events_fetched": 0,
        "predictions_fetched": 0,
        "event_matches": 0,
        "contexts_built": 0,
        "contexts_built_from_batch_prediction": 0,
        "detail_prediction_requests": 0,
        "detail_odds_requests": 0,
        "matched_exact": 0,
        "matched_loose": 0,
        "matched_fuzzy": 0,
        "event_rejected_no_quality": 0,
        "http_statuses": [],
        "payload_shapes": [],
        "last_url": None,
        "last_error": None,
        "last_body_preview": None,
    }
    preview: dict[str, Any] = {
        "sample_events": [],
        "sample_predictions": [],
        "matched_examples": [],
        "unmatched_examples": [],
    }
    if not getattr(self, "api_key", None):
        return {}, stats, preview

    soccer_matches = [match for match in matches if getattr(match, "sport_key", "") == "soccer"]
    if not soccer_matches:
        return {}, stats, preview
    soccer_matches = self._prioritize_matches(soccer_matches)
    if getattr(self, "enforce_context_limit", False):
        limit = max(0, _as_int(os.getenv("BZZOIRO_CONTEXT_MATCH_LIMIT"), 0))
        if limit:
            soccer_matches = soccer_matches[:limit]

    min_dt = min(match.commence_time for match in soccer_matches).astimezone(UTC)
    max_dt = max(match.commence_time for match in soccer_matches).astimezone(UTC)
    date_from = min_dt.date().isoformat()
    date_to = max_dt.date().isoformat()
    headers = {"Authorization": f"Token {self.api_key}"}
    contexts: dict[str, MatchContext] = {}
    detail_limit = max(0, _as_int(os.getenv("BZZOIRO_CONTEXT_DETAIL_GAP_LIMIT"), 18))
    odds_fallback_limit = max(0, _as_int(os.getenv("BZZOIRO_CONTEXT_ODDS_FALLBACK_LIMIT"), 8))
    detail_used = 0
    odds_used = 0

    async with httpx.AsyncClient(timeout=getattr(self, "timeout", 20.0), follow_redirects=True) as client:
        events = await self._fetch_events(client, headers, date_from, date_to, stats)
        predictions = await _fetch_predictions_v2(
            self, client, headers, date_from, date_to, stats
        )
        stats["events_fetched"] = len(events)
        stats["predictions_fetched"] = len(predictions)
        preview["sample_events"] = events[:3]
        preview["sample_predictions"] = predictions[:3]
        prediction_by_event = {
            event_id: row
            for row in predictions
            if (event_id := _prediction_event_id(row))
        }

        for match in soccer_matches:
            event, quality, score, diag = self._match_event(match, events)
            if not event or not quality:
                stats["event_rejected_no_quality"] += 1
                if len(preview["unmatched_examples"]) < 8:
                    preview["unmatched_examples"].append(
                        {
                            "match_key": match.match_key,
                            "home": match.home_team,
                            "away": match.away_team,
                            "best_event": diag,
                        }
                    )
                continue
            event_id = str(event.get("id") or "")
            prediction = prediction_by_event.get(event_id)
            details: dict[str, Any] = {
                "event": event,
                "odds": None,
                "stats": None,
                "metadata": None,
                "prediction": prediction,
            }
            context = self._event_to_context(details, quality)
            from_batch = context is not None and prediction is not None

            if context is None and event_id and detail_used < detail_limit:
                detail_used += 1
                stats["detail_prediction_requests"] += 1
                details["prediction"] = await self._get_json(
                    client, f"/events/{event_id}/prediction/", headers, {}, stats
                )
                context = self._event_to_context(details, quality)

            if context is None and event_id and odds_used < odds_fallback_limit:
                odds_used += 1
                stats["detail_odds_requests"] += 1
                details["odds"] = await self._get_json(
                    client, f"/events/{event_id}/odds/", headers, {}, stats
                )
                context = self._event_to_context(details, quality)

            if context is None:
                continue
            contexts[match.match_key] = context
            stats["event_matches"] += 1
            stats["contexts_built"] = len(contexts)
            if from_batch:
                stats["contexts_built_from_batch_prediction"] += 1
            if quality == "exact":
                stats["matched_exact"] += 1
            elif quality == "loose":
                stats["matched_loose"] += 1
            elif quality == "fuzzy":
                stats["matched_fuzzy"] += 1
            if len(preview["matched_examples"]) < 10:
                preview["matched_examples"].append(
                    {
                        "match_key": match.match_key,
                        "event_id": event_id,
                        "quality": quality,
                        "score": round(float(score), 2),
                        "batch_prediction": prediction is not None,
                        "expected_home": context.expected_home,
                        "expected_away": context.expected_away,
                    }
                )

    return contexts, stats, preview


fetch_context_batch_predictions._harizon_batch_predictions_first = True  # type: ignore[attr-defined]


def _install_bzzoiro_batch_context() -> dict[str, Any]:
    try:
        from app.providers.bzzoiro_v2 import BzzoiroContextProvider

        current = BzzoiroContextProvider.fetch_context
        if getattr(current, "_harizon_batch_predictions_first", False):
            return {"status": "already_installed"}
        BzzoiroContextProvider.fetch_context = fetch_context_batch_predictions
        os.environ.setdefault("BZZOIRO_CONTEXT_DETAIL_GAP_LIMIT", "18")
        os.environ.setdefault("BZZOIRO_CONTEXT_ODDS_FALLBACK_LIMIT", "8")
        os.environ.setdefault("BZZOIRO_PREDICTIONS_PAGE_SIZE", "200")
        os.environ.setdefault("BZZOIRO_PREDICTIONS_MAX_PAGES", "10")
        return {
            "status": "installed",
            "strategy": "v2_predictions_pages_then_bounded_event_detail_gaps",
        }
    except Exception as exc:
        return {"status": "error", "error": f"{type(exc).__name__}: {exc}"}


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True
    report = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "status": "installed",
        "score_event_match_compat": _install_score_match_compat(),
        "alias_aware_evidence_cache": _install_alias_cache(),
        "bzzoiro_batch_context": _install_bzzoiro_batch_context(),
        "publication_contract_relaxed": False,
    }
    if any(
        isinstance(value, dict) and value.get("status") == "error"
        for key, value in report.items()
        if key not in {"created_at_utc", "status", "publication_contract_relaxed"}
    ):
        report["status"] = "partial_error"
    _write(report)
    return report


__all__ = [
    "fetch_context_batch_predictions",
    "install",
    "score_event_match_compat",
]
