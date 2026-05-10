from __future__ import annotations

"""Runner-level Bzzoiro odds bridge.

The previous CandidateFactory class wrapper can be bypassed when other runtime
patches replace/own the factory method. This patch wraps the concrete
PredictionRunner.factory instance after PredictionRunner.__init__ completes.
That is the object actually used by runner.run_once before candidate generation.
"""

import json
import types
from pathlib import Path
from typing import Any

from app.schemas import MatchContext, Offer

ROOT = Path(__file__).resolve().parents[2]
EXPORT_DIR = ROOT / ".data" / "exports"
REPORT = EXPORT_DIR / "latest-bzzoiro-runner-bridge.json"
FINAL_REPORT = EXPORT_DIR / "latest-bzzoiro-final-bridge.json"
REKEY_REPORT = EXPORT_DIR / "latest-bzzoiro-odds-rekey.json"
_INSTALLED = False


def _write(payload: dict[str, Any]) -> None:
    try:
        EXPORT_DIR.mkdir(parents=True, exist_ok=True)
        text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        REPORT.write_text(text, encoding="utf-8")
        FINAL_REPORT.write_text(text, encoding="utf-8")
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


def _bridge(matches, offers_by_match, contexts_by_match) -> tuple[dict[str, list[Offer]], dict[str, Any], dict[str, Any]]:
    from app.services import signal_stack_runtime_patch as stack
    from app.services import bzzoiro_odds_rekey_runtime_patch as rekey

    matches_list = list(matches or [])
    by_key = {m.match_key: m for m in matches_list}
    merged_offers: dict[str, list[Offer]] = {str(k): list(v or []) for k, v in dict(offers_by_match or {}).items()}
    merged_contexts: dict[str, Any] = {str(k): v for k, v in dict(contexts_by_match or {}).items()}

    contexts_seen = 0
    contexts_matched = 0
    contexts_rekeyed = 0
    hints_seen = 0
    offers_added = 0
    offers_direct = 0
    offers_rekeyed = 0
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
            if len(examples) < 16:
                examples.append({
                    "from_key": key,
                    "to_key": target_key,
                    "home": getattr(target, "home_team", ""),
                    "away": getattr(target, "away_team", ""),
                    "hints": len(hints),
                })

    report = {
        "status": "ok",
        "wrapper": "runner_instance_factory_build_candidates",
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
    return merged_offers, merged_contexts, report


def _wrap_factory_instance(factory: Any) -> bool:
    if factory is None:
        return False
    current = getattr(factory, "build_candidates", None)
    if not callable(current):
        return False
    if getattr(current, "_harizon_runner_bzzoiro_bridge", False):
        return True

    def wrapped(self, matches, offers_by_match, contexts_by_match, market_signals_by_match=None):
        try:
            self.target_books.add(self._norm_book("Bzzoiro"))
            self.consensus_books.add(self._norm_book("Bzzoiro"))
        except Exception:
            pass
        try:
            bridged_offers, bridged_contexts, report = _bridge(matches, offers_by_match, contexts_by_match)
        except Exception as exc:
            _write({"status": "bridge_error", "error": f"{type(exc).__name__}: {exc}"})
            bridged_offers, bridged_contexts, report = offers_by_match, contexts_by_match, {"status": "bridge_error"}
        result = current(bridged_offers and matches, bridged_offers, bridged_contexts, market_signals_by_match)
        try:
            candidates, rejections, debug = result
            debug = dict(debug or {})
            debug["bzzoiro_runner_bridge_patch"] = report
            return candidates, rejections, debug
        except Exception:
            return result

    wrapped._harizon_runner_bzzoiro_bridge = True  # type: ignore[attr-defined]
    factory.build_candidates = types.MethodType(wrapped, factory)
    return True


def install() -> dict[str, Any]:
    global _INSTALLED
    try:
        from app.services.runner import PredictionRunner
    except Exception as exc:
        return {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
    current_init = PredictionRunner.__init__
    if getattr(current_init, "_harizon_runner_bzzoiro_bridge_init", False):
        _INSTALLED = True
        return {"status": "already_wrapped"}

    def wrapped_init(self, *args, **kwargs):
        current_init(self, *args, **kwargs)
        ok = _wrap_factory_instance(getattr(self, "factory", None))
        _write({"status": "installed", "runner_factory_wrapped": bool(ok)})

    wrapped_init._harizon_runner_bzzoiro_bridge_init = True  # type: ignore[attr-defined]
    PredictionRunner.__init__ = wrapped_init
    _INSTALLED = True
    _write({"status": "installed", "runner_init_wrapped": True})
    return {"status": "installed"}
