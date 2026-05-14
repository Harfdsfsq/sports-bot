from __future__ import annotations

"""Persist odds movement snapshots across runs without changing workflow YAML.

The legacy signal stack writes current-run snapshots to `.data/odds_movement_snapshots.jsonl`.
The GitHub workflow intentionally resets that file before commit, so the next run sees
`snapshot_count=0` and the windowed publication filter blocks candidates with
`needs_next_cron_line_movement_recheck`.

This bridge mirrors snapshots into `.data/cache/odds_movement_snapshots.jsonl`, which is
already committed by the workflow, and teaches the windowed movement guard to read both
files. It does not relax movement logic; it only makes the history durable.

v2 also normalizes localized candidate selections. In Telegram/debug artifacts a total can
be rendered as `Меньше 3.5`, while snapshots are stored as `Under` + point 3.5. The old
windowed key compared those strings literally after ASCII normalization and therefore
returned zero snapshots even when the cache contained the correct match/market history.
"""

import json
import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
LIVE_SNAPSHOT_PATH = ROOT / ".data" / "odds_movement_snapshots.jsonl"
CACHE_SNAPSHOT_PATH = ROOT / ".data" / "cache" / "odds_movement_snapshots.jsonl"
REPORT_PATH = ROOT / ".data" / "exports" / "latest-odds-movement-cache-bridge.json"
_INSTALLED = False


def _write_report(payload: dict[str, Any]) -> None:
    try:
        REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
        REPORT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass


def _tail_lines(path: Path, max_lines: int = 30000) -> list[str]:
    try:
        if not path.exists():
            return []
        return path.read_text(encoding="utf-8").splitlines()[-max_lines:]
    except Exception:
        return []


def _dedupe_rows(rows: list[dict[str, Any]], max_rows: int = 60000) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for row in rows[-max_rows:]:
        key = "|".join(str(row.get(k) or "") for k in (
            "captured_at_utc", "match_key", "family", "selection", "point", "team_side", "source", "bookmaker", "price"
        ))
        if key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out[-max_rows:]


def _read_rows_from_paths(paths: list[Path]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path in paths:
        for line in _tail_lines(path):
            if not str(line).strip():
                continue
            try:
                row = json.loads(line)
            except Exception:
                continue
            if isinstance(row, dict):
                rows.append(row)
    rows.sort(key=lambda item: str(item.get("captured_at_utc") or ""))
    return _dedupe_rows(rows)


def _flush_cache_from_live() -> int:
    rows = _read_rows_from_paths([CACHE_SNAPSHOT_PATH, LIVE_SNAPSHOT_PATH])
    if not rows:
        return 0
    CACHE_SNAPSHOT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with CACHE_SNAPSHOT_PATH.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def _norm(value: Any) -> str:
    text = str(value or "").strip().lower().replace("ё", "е")
    text = text.replace("—", " ").replace("–", " ").replace("-", " ")
    return " ".join(re.sub(r"[^a-z0-9а-я.]+", " ", text).split())


def _as_float(value: Any) -> float | None:
    try:
        if value in (None, ""):
            return None
        number = float(str(value).replace(",", "."))
        if math.isfinite(number):
            return number
    except Exception:
        return None
    return None


def _point_from_any(*values: Any) -> float | None:
    for value in values:
        direct = _as_float(value)
        if direct is not None:
            return round(direct, 3)
        text = _norm(value)
        for match in re.finditer(r"(?<!\d)(\d+(?:[.,]\d+)?)(?!\d)", text):
            try:
                number = float(match.group(1).replace(",", "."))
            except Exception:
                continue
            if 0.0 < number <= 20.0:
                return round(number, 3)
    return None


def _family_from_any(*values: Any) -> str:
    text = " ".join(_norm(value) for value in values)
    if any(token in text for token in ("total", "goals", "тотал", "тб", "тм", "больше", "меньше", "over", "under")):
        return "totals"
    if any(token in text for token in ("spread", "handicap", "фора", "asian")):
        return "spreads"
    if any(token in text for token in ("btts", "both teams", "обе забьют", "оз")):
        return "btts"
    if any(token in text for token in ("h2h", "winner", "match winner", "исход", "1x2")):
        return "h2h"
    raw = _norm(values[0]) if values else ""
    raw = raw.replace(" ", "_")
    return raw


def _side_from_any(*values: Any) -> str:
    text = " ".join(_norm(value) for value in values)
    if any(token in text for token in ("under", "меньше", "тм")):
        return "under"
    if any(token in text for token in ("over", "больше", "тб")):
        return "over"
    if any(token in text for token in ("yes", "да")):
        return "yes"
    if any(token in text for token in ("no", "нет")):
        return "no"
    if any(token in text for token in ("home", "хозя", "п1", "team1")):
        return "home"
    if any(token in text for token in ("away", "гост", "п2", "team2")):
        return "away"
    if any(token in text.split() for token in ("draw", "x", "ничья")):
        return "draw"
    return text.replace(" ", "_")


def _candidate_market_key(candidate: Any) -> tuple[str, str, float | None, str]:
    family = _family_from_any(
        getattr(candidate, "family", None),
        getattr(candidate, "market_family", None),
        getattr(candidate, "selection", None),
        getattr(candidate, "selection_key", None),
    )
    side = _side_from_any(
        getattr(candidate, "selection_key", None),
        getattr(candidate, "selection", None),
        getattr(candidate, "team_side", None),
    )
    point = _point_from_any(
        getattr(candidate, "point", None),
        getattr(candidate, "selection", None),
        getattr(candidate, "selection_key", None),
        getattr(candidate, "market_name", None),
    )
    team_side = _side_from_any(getattr(candidate, "team_side", None)) if getattr(candidate, "team_side", None) else ""
    return family, side, point, team_side


def _snapshot_market_key(row: dict[str, Any]) -> tuple[str, str, float | None, str]:
    family = _family_from_any(
        row.get("family"),
        row.get("market_key"),
        row.get("market_name"),
        row.get("selection"),
    )
    side = _side_from_any(row.get("selection"), row.get("selection_key"), row.get("market_name"), row.get("team_side"))
    point = _point_from_any(row.get("point"), row.get("selection"), row.get("market_name"), row.get("market_key"))
    team_side = _side_from_any(row.get("team_side")) if row.get("team_side") else ""
    return family, side, point, team_side


def _same_market(candidate_key: tuple[str, str, float | None, str], row_key: tuple[str, str, float | None, str]) -> bool:
    cf, cs, cp, ct = candidate_key
    rf, rs, rp, rt = row_key
    if cf and rf and cf != rf:
        if not (cf == "totals" and rf in {"goals", "total_goals"}):
            return False
    if cs and rs and cs != rs:
        return False
    if cp is not None or rp is not None:
        if cp is None or rp is None or abs(cp - rp) > 0.001:
            return False
    if ct and rt and ct != rt:
        return False
    return True


def _patch_signal_stack() -> dict[str, Any]:
    try:
        import app.services.signal_stack_runtime_patch as signal
    except Exception as exc:
        return {"signal_stack": f"skip:{type(exc).__name__}: {exc}"}
    original = getattr(signal, "_append_snapshots", None)
    if not callable(original):
        return {"signal_stack": "missing_append_snapshots"}
    if getattr(original, "_harizon_cache_bridge", False):
        return {"signal_stack": "already_patched"}

    def append_snapshots_with_cache(matches: Any, offers_by_match: Any) -> dict[str, Any]:
        report = dict(original(matches, offers_by_match) or {})
        try:
            cache_rows = _flush_cache_from_live()
            report["cache_bridge_enabled"] = True
            report["cache_snapshot_path"] = str(CACHE_SNAPSHOT_PATH.relative_to(ROOT))
            report["cache_rows_total"] = cache_rows
            report["localized_selection_normalization"] = True
        except Exception as exc:
            report["cache_bridge_error"] = f"{type(exc).__name__}: {exc}"
        return report

    append_snapshots_with_cache._harizon_cache_bridge = True  # type: ignore[attr-defined]
    signal._append_snapshots = append_snapshots_with_cache  # type: ignore[attr-defined]
    return {"signal_stack": "patched"}


def _patch_windowed_loader() -> dict[str, Any]:
    try:
        import app.services.windowed_core_coverage_runtime_patch as windowed
    except Exception as exc:
        return {"windowed_loader": f"skip:{type(exc).__name__}: {exc}"}
    original = getattr(windowed, "_load_snapshot_history", None)
    to_float = getattr(windowed, "_to_float", None)
    if not callable(original) or not callable(to_float):
        return {"windowed_loader": "missing_helpers"}
    if getattr(original, "_harizon_cache_bridge_v2", False):
        return {"windowed_loader": "already_patched"}

    def load_snapshot_history_with_cache(match_key: str, candidate: Any) -> list[dict[str, Any]]:
        candidate_key = _candidate_market_key(candidate)
        rows: list[dict[str, Any]] = []
        for row in _read_rows_from_paths([CACHE_SNAPSHOT_PATH, LIVE_SNAPSHOT_PATH]):
            if str(row.get("match_key") or "") != str(match_key):
                continue
            try:
                if not _same_market(candidate_key, _snapshot_market_key(row)):
                    continue
                price = to_float(row.get("price"))
            except Exception:
                continue
            if price is None or not row.get("captured_at_utc"):
                continue
            rows.append(row)
        rows.sort(key=lambda item: str(item.get("captured_at_utc") or ""))
        return _dedupe_rows(rows, max_rows=20000)

    load_snapshot_history_with_cache._harizon_cache_bridge_v2 = True  # type: ignore[attr-defined]
    windowed._load_snapshot_history = load_snapshot_history_with_cache  # type: ignore[attr-defined]
    return {"windowed_loader": "patched_v2_localized_selection"}


def install() -> dict[str, Any]:
    global _INSTALLED
    if _INSTALLED:
        return {"status": "already_installed"}
    _INSTALLED = True
    payload = {
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": "starting",
        "live_snapshot_path": str(LIVE_SNAPSHOT_PATH.relative_to(ROOT)),
        "cache_snapshot_path": str(CACHE_SNAPSHOT_PATH.relative_to(ROOT)),
        "localized_selection_normalization": True,
    }
    try:
        payload.update(_patch_signal_stack())
        payload.update(_patch_windowed_loader())
        payload["cache_rows_total"] = _flush_cache_from_live()
        payload["status"] = "installed"
    except Exception as exc:
        payload["status"] = "error"
        payload["error"] = f"{type(exc).__name__}: {exc}"
    _write_report(payload)
    return payload
