from __future__ import annotations

from typing import Any


def _n(value: Any) -> int:
    try:
        if value in (None, ''):
            return 0
        return int(float(str(value)))
    except Exception:
        return 0


def _names_from_rows(rows: Any) -> set[str]:
    if not isinstance(rows, list):
        return set()
    names: set[str] = set()
    for item in rows:
        if not isinstance(item, dict):
            continue
        book = item.get('book') or item.get('bookmaker') or item.get('bookmaker_key') or item.get('source')
        price = item.get('price') or item.get('odds') or item.get('decimal_odds')
        try:
            ok_price = float(str(price).replace(',', '.')) > 1.0
        except Exception:
            ok_price = False
        if book and ok_price:
            names.add(str(book).strip().lower())
    return names


def count_sources(row: dict[str, Any]) -> int:
    """Return current same-market price evidence count.

    Evaluated fallback rows often no longer carry raw bucket offers; the strict
    price quorum is already summarized in ``metrics.tier_b_bookmaker_quorum``.
    The old helper therefore reported zero live sources for actually published
    B-tier picks.  Prefer the quorum/priced-books fields, then fall back to raw
    offer rows.
    """
    metrics = row.get('metrics') if isinstance(row.get('metrics'), dict) else {}
    quorum = metrics.get('tier_b_bookmaker_quorum') if isinstance(metrics.get('tier_b_bookmaker_quorum'), dict) else {}
    for value in (
        quorum.get('priced_books_count'),
        metrics.get('priced_books_count'),
        row.get('priced_books_count'),
        quorum.get('books_count'),
        metrics.get('books_count'),
    ):
        n = _n(value)
        if n > 0:
            return n

    names: set[str] = set()
    names |= _names_from_rows(row.get('raw_bucket_offers'))
    source_summary = row.get('source_summary') if isinstance(row.get('source_summary'), dict) else {}
    for key in ('raw_bucket_offers', 'bucket_offers', 'offers'):
        names |= _names_from_rows(source_summary.get(key))
    return len(names)
