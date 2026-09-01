"""Repair SStats settlement pagination without changing grading thresholds.

``/Games/list`` returns at most 1000 rows and requires offset pagination. Some
responses expose ``count`` as the current-page size rather than the complete result
count. The legacy settlement loop trusted ``count == 1000`` as a terminal total and
therefore never requested offset 1000, leaving recent bets unmatched whenever the
multi-day result window exceeded one page.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from app.services.settlement import SettlementService

_INSTALLED = False


def _signature(service: SettlementService, item: dict[str, Any]) -> tuple[Any, ...]:
    return (
        item.get("id"),
        item.get("flashId"),
        item.get("date"),
        service._extract_team_name(item, "home"),
        service._extract_team_name(item, "away"),
    )


async def _fetch_sstats_rows_paged(
    service: SettlementService,
    start_date: str,
    end_date: str,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen_signatures: set[tuple[Any, ...]] = set()
    offset = 0
    limit = 1000
    page = 0
    max_pages = max(
        1,
        min(
            30,
            int(os.getenv("SETTLEMENT_SSTATS_MAX_PAGES", "12") or 12),
        ),
    )
    total_count: int | None = None
    try:
        timeout = float(
            getattr(service.settings, "sstats_timeout_seconds", 25.0) or 25.0
        )
        async with httpx.AsyncClient(timeout=timeout) as client:
            while page < max_pages:
                response = await client.get(
                    service.url,
                    params={
                        "from": start_date,
                        "to": end_date,
                        "limit": limit,
                        "offset": offset,
                        "apikey": str(service.settings.sstats_api_key),
                    },
                    headers={"X-API-Key": str(service.settings.sstats_api_key)},
                )
                response.raise_for_status()
                payload = response.json()
                if isinstance(payload, dict):
                    batch = payload.get("data") or payload.get("results") or []
                    raw_total = payload.get("count")
                    if raw_total not in (None, ""):
                        try:
                            parsed_total = int(raw_total)
                        except Exception:
                            parsed_total = None
                        # A one-page-sized count is ambiguous in SStats and must not
                        # stop offset pagination. Trust only an explicit larger total.
                        if parsed_total is not None and parsed_total > limit:
                            total_count = parsed_total
                elif isinstance(payload, list):
                    batch = payload
                else:
                    batch = []
                if not isinstance(batch, list) or not batch:
                    break

                added = 0
                for item in batch:
                    if not isinstance(item, dict):
                        continue
                    signature = _signature(service, item)
                    if signature in seen_signatures:
                        continue
                    seen_signatures.add(signature)
                    rows.append({**item, "_settlement_source": "sstats"})
                    added += 1

                page += 1
                if len(batch) < limit:
                    break
                offset += len(batch)
                if total_count is not None and offset >= total_count:
                    break
                if added == 0:
                    break
    except Exception:
        return rows
    return rows


def install() -> dict[str, Any]:
    global _INSTALLED
    current = SettlementService._fetch_sstats_rows
    if getattr(current, "_sstats_settlement_pagination_repair", False):
        _INSTALLED = True
        return {"status": "already_installed", "publication_contract_relaxed": False}

    async def patched(
        self: SettlementService,
        start_date: str,
        end_date: str,
    ) -> list[dict[str, Any]]:
        return await _fetch_sstats_rows_paged(self, start_date, end_date)

    patched._sstats_settlement_pagination_repair = True  # type: ignore[attr-defined]
    patched._original = current  # type: ignore[attr-defined]
    SettlementService._fetch_sstats_rows = patched
    _INSTALLED = True
    return {
        "status": "installed",
        "policy": "offset_until_short_page_or_unambiguous_total_with_bounded_max_pages",
        "max_pages": max(
            1,
            min(30, int(os.getenv("SETTLEMENT_SSTATS_MAX_PAGES", "12") or 12)),
        ),
        "publication_contract_relaxed": False,
    }


__all__ = ["_fetch_sstats_rows_paged", "install"]
