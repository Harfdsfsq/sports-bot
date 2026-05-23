from __future__ import annotations

"""Normalize Bzzoiro total points before materializing Offer rows.

Bzzoiro odds hints can encode totals as 15/25/35 while HARIZON market buckets
expect 1.5/2.5/3.5.  Without this bridge the exact-offer diagnostics see many
Bzzoiro offers, but CandidateFactory rejects them as unsupported_total_line and
cannot overlap them with odds-api.io totals.
"""

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
REPORT_PATH = ROOT / ".data" / "exports" / "latest-bzzoiro-total-point-normalization-patch.json"
UTC = timezone.utc


def _write(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        v = float(str(value).replace(",", "."))
        return v if math.isfinite(v) else None
    except Exception:
        return None


def _family(hint: dict[str, Any]) -> str:
    raw = str(hint.get("family") or hint.get("market_key") or hint.get("market") or "").strip().lower()
    if raw in {"total", "totals", "over_under", "goals_over_under"}:
        return "totals"
    return raw


def _normalized_total_point(value: Any) -> float | None:
    p = _float(value)
    if p is None:
        return None
    # Bzzoiro commonly stores 1.5/2.5/3.5 as 15/25/35.  Keep normal Asian
    # totals untouched and only scale plausible soccer-total integers.
    if 10.0 <= p <= 60.0 and abs(p - round(p)) < 1e-9 and int(round(p)) % 5 == 0:
        return round(p / 10.0, 3)
    return p


def _patch_hint(hint: dict[str, Any]) -> tuple[dict[str, Any], bool, float | None, float | None]:
    if _family(hint) != "totals":
        return hint, False, None, None
    keys = ["point", "line", "option_value"]
    raw_value = None
    raw_key = None
    for key in keys:
        if hint.get(key) not in (None, ""):
            raw_key = key
            raw_value = hint.get(key)
            break
    if raw_key is None:
        return hint, False, None, None
    before = _float(raw_value)
    after = _normalized_total_point(raw_value)
    if before is None or after is None or abs(before - after) < 1e-9:
        return hint, False, before, after
    patched = dict(hint)
    # Set all line aliases so the downstream bridge cannot accidentally read the
    # unscaled field first.
    patched["point"] = after
    patched["line"] = after
    patched["option_value"] = after
    patched.setdefault("metadata", {})
    if isinstance(patched.get("metadata"), dict):
        patched["metadata"]["harizon_total_point_normalized_from"] = before
    return patched, True, before, after


def install() -> dict[str, Any]:
    try:
        from app.services import bzzoiro_exact_offer_bridge_patch as bridge
    except Exception as exc:
        result = {"status": "import_error", "error": f"{type(exc).__name__}: {exc}"}
        _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
        return result
    current = getattr(bridge, "_offer_from_hint", None)
    if not callable(current):
        result = {"status": "skipped", "reason": "missing__offer_from_hint"}
        _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
        return result
    if getattr(current, "_harizon_total_point_normalization", False):
        result = {"status": "already_installed"}
        _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
        return result

    original = current
    counters = {"normalized": 0, "seen": 0}

    def offer_from_hint_patched(hint: dict[str, Any], match: Any):
        if isinstance(hint, dict):
            counters["seen"] += 1
            patched, changed, before, after = _patch_hint(hint)
            if changed:
                counters["normalized"] += 1
                hint = patched
        return original(hint, match)

    offer_from_hint_patched._harizon_total_point_normalization = True  # type: ignore[attr-defined]
    offer_from_hint_patched._harizon_original = original  # type: ignore[attr-defined]
    offer_from_hint_patched._harizon_counters = counters  # type: ignore[attr-defined]
    bridge._offer_from_hint = offer_from_hint_patched
    result = {"status": "installed", "version": "bzzoiro-total-point-normalization-v1"}
    _write({"created_at_utc": datetime.now(UTC).isoformat(), **result})
    return result
