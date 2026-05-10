from __future__ import annotations

"""Final Bzzoiro odds bridge.

Why this exists:
- signal_stack_runtime_patch mines Bzzoiro odds hints inside CandidateFactory.build_candidates;
- other runtime wrappers can be installed after sitecustomize/usercustomize;
- a wrapper installed before signal_stack cannot rekey offers that do not exist yet.

This final wrapper mines Bzzoiro hints itself and attaches them to the canonical
odds-api.io match_key before candidate generation. It is intentionally installed
at the very end of usercustomize.py.
"""

import json
from pathlib import Path
from typing import Any

from app.schemas import MatchContext, Offer

ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
REPORT = EXPORT_DIR / "latest-bzzoiro-final-bridge.json"
REKEY_REPORT = EXPORT_DIR / "latest-bzzoiro-odds-rekey.json"
_INSTALLED = False


def _write(payload: dict[str, Any]) -> None:
    try:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        REPORT.write_text(text, encoding="utf-8")
        REKEY_REPORT.write_text(text, encoding="utf-8")
    except Exception:
        pass


def _iter_contexts(value: Any):
    if isinstance(value, MatchContext):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _iter_contexts(item)
    elif isinstance(value, (list, tuple, set)):
        for item in value:
            yield from _iter_contexts(item)


def _append_context(contexts: dict[str, Any], key: str, context: MatchContext) -> None:
    existing = contexts.get(key)
    if existing is None:
        contexts[key] = [context]
    elif isinstance(existing, list):
        if context not in existing:
            existing.append(context)
    else:
        contexts[key] = [existing, context]


def _offer_id(offer: Offer) -> tuple[Any, ...]:
    return (
        str(offer.source or "").lower(),
        str(offer.bookmaker or "").lower(),
        str(offer.family or "").lower(),
        str(offer.selection or "").lower(),
        round(float(offer.point), 4) if offer.point is not None else None,
        round(float(offer.price), 4),
        str(offer.team_side or "").lower(),
    )


def _append_offer(offers_by_match: dict[str, list[Offer]], key: str, offer: Offer) -> bool:
    bucket = offers_by_match.setdefault(key, [])
    ident = _offer_id(offer)
    if any(_offer_id(item) == ident for item in bucket):
        return False
    bucket.append(offer)
    return True


def _source_combinations(offers_by_match: dict[str, list[Offer]]) -> dict[str, int]:
    combos: dict[str, int] = {}
    for offers in offers_by_match.values():
        sources = sorted({str(o.source or "").strip().lower() for o in offers if str(o.source or "").strip()})
        if not sources:
            continue
        combo = "+".join(sources)
        combos[combo] = combos.get(combo, 0) + 1
    return dict(sorted(combos.items(), key=lambda item: (-item[1], item[0])))


def install() -> dict[str, Any]:
    global _INSTALLED
    try:
        from app.services.model import CandidateFactory
        from app.services import signal_stack_runtime_patch as stack
        from app.services import bzzoiro_odds_rekey_runtime_patch as rekey
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}

    current = CandidateFactory.build_candidates
    if getattr(current, "_harizon_bzzoiro_final_bridge", False):
        _INSTALLED = True
        return {"status": "already_wrapped"}

    original = current

    def wrapped(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):
        matches_list = list(matches or [])
        by_key = {m.match_key: m for m in matches_list}
        merged_offers: dict[str, list[Offer]] = {str(k): list(v or []) for k, v in dict(offers_by_match or {}).items()}
        merged_contexts: dict[str, Any] = {str(k): v for k, v in dict(contexts_by_match or {}).items()}

        try:
            self.target_books.add(self._norm_book("Bzzoiro"))
            self.consensus_books.add(self._norm_book("Bzzoiro"))
        except Exception:
            pass

        contexts_seen = 0
        contexts_matched = 0
        contexts_rekeyed = 0
        offers_added = 0
        offers_direct = 0
        offers_rekeyed = 0
        hints_seen = 0
        skipped_no_target = 0
        examples: list[dict[str, Any]] = []

        for raw_key, raw_value in list(dict(contexts_by_match or {}).items()):
            key = str(raw_key)
            for context in _iter_contexts(raw_value):
                if not isinstance(context, MatchContext):
                    continue
                if "bzzoiro" not in str(getattr(context, "source", "") or "").lower():
                    continue
                contexts_seen += 1
                try:
                    stack._enhance_context(context)  # type: ignore[attr-defined]
                except Exception:
                    pass
                target = None
                try:
                    target = rekey._best_match(key, context, matches_list, by_key)  # type: ignore[attr-defined]
                except Exception:
                    target = by_key.get(key)
                if target is None:
                    target = by_key.get(key)
                if target is None:
                    skipped_no_target += 1
                    continue
                contexts_matched += 1
                target_key = str(target.match_key)
                if target_key != key:
                    contexts_rekeyed += 1
                    _append_context(merged_contexts, target_key, context)

                hints = list((getattr(context, "details", {}) or {}).get("provider_odds_hints") or [])
                hints_seen += len(hints)
                for hint in hints:
                    try:
                        offer = stack._offer_from_hint(hint, target)  # type: ignore[attr-defined]
                    except Exception:
                        offer = None
                    if offer is None:
                        continue
                    if _append_offer(merged_offers, target_key, offer):
                        offers_added += 1
                        if target_key == key:
                            offers_direct += 1
                        else:
                            offers_rekeyed += 1
                if len(examples) < 12:
                    examples.append({
                        "from_key": key,
                        "to_key": target_key,
                        "home": getattr(target, "home_team", ""),
                        "away": getattr(target, "away_team", ""),
                        "hints": len(hints),
                    })

        report = {
            "status": "ok",
            "matches_seen": len(matches_list),
            "contexts_seen": contexts_seen,
            "contexts_matched": contexts_matched,
            "contexts_rekeyed": contexts_rekeyed,
            "hints_seen": hints_seen,
            "bzzoiro_offers_added_to_canonical_matches": offers_added,
            "bzzoiro_offers_direct": offers_direct,
            "bzzoiro_offers_rekeyed": offers_rekeyed,
            "skipped_no_target": skipped_no_target,
            "offer_source_combinations_after_bridge": _source_combinations(merged_offers),
            "examples": examples,
        }
        _write(report)

        candidates, rejections, debug = original(self, matches, merged_offers, merged_contexts, market_signals_by_match=market_signals_by_match)
        try:
            debug = dict(debug or {})
            debug["bzzoiro_final_odds_bridge_patch"] = report
        except Exception:
            pass
        return candidates, rejections, debug

    wrapped._harizon_bzzoiro_final_bridge = True
    CandidateFactory.build_candidates = wrapped
    _INSTALLED = True
    _write({"status": "installed"})
    return {"status": "installed"}
