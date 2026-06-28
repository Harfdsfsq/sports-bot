from __future__ import annotations

"""Create durable HARIZON publication/run ledgers from runtime artifacts.

The ledger is the source of truth for daily settlement/accounting.  Controlled
fallback publishes outside the main runner, so plain app state can miss Telegram
picks.  This sync collects published rows from JSON artifacts, fallback reports,
state and HARIZON Telegram text, deduplicates them semantically, writes .data/bets
snapshots and mirrors the same pending/settled rows into .data/state.json so the
regular settlement service can close them on the next daily-report run.
"""

import argparse
import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

UTC = timezone.utc
ROOT = Path(".").resolve()
EXPORT_DIR = ROOT / ".data" / "exports"
BET_DIR = ROOT / ".data" / "bets"
STATE_JSON = ROOT / ".data" / "state.json"
PUBLISHED_JSONL = BET_DIR / "published_bets.jsonl"
PENDING_JSON = BET_DIR / "pending_bets.json"
SETTLED_JSONL = BET_DIR / "settled_bets.jsonl"
RUN_LEDGER_JSONL = BET_DIR / "run_report_ledger.jsonl"
REPORT = EXPORT_DIR / "latest-publication-ledger-sync.json"

SETTLED_STATUSES = {"won", "lost", "push", "void", "half_won", "half_lost", "settled", "closed"}
PENDING_STATUSES = {"", "pending", "generated", "published", "telegram_sent", "sent", "posted", "open", "active"}
MSK = ZoneInfo("Europe/Moscow")


def load_json(path: str | Path, default: Any) -> Any:
    try:
        p = Path(path)
        if not p.exists() or p.stat().st_size <= 0:
            return default
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        if not path.exists():
            return rows
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            item = json.loads(line)
            if isinstance(item, dict):
                rows.append(item)
    except Exception:
        pass
    return rows


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n" for row in rows), encoding="utf-8")


def parse_dt(value: Any) -> datetime | None:
    if value in (None, ""):
        return None
    try:
        text = str(value).strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def parse_msk_dt(value: Any) -> datetime | None:
    text = str(value or "").strip()
    m = re.search(r"(\d{2})\.(\d{2})\.(\d{4})\s+(\d{1,2}):(\d{2})", text)
    if not m:
        return parse_dt(value)
    day, month, year, hour, minute = m.groups()
    try:
        return datetime(int(year), int(month), int(day), int(hour), int(minute), tzinfo=MSK).astimezone(UTC)
    except Exception:
        return None


def norm(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = text.replace("ё", "е").replace("—", "-").replace("–", "-")
    text = "".join(ch if ch.isalnum() else " " for ch in text)
    return " ".join(text.split())


def point_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    try:
        number = float(str(value).replace(",", "."))
        return str(int(number)) if number.is_integer() else f"{number:.2f}".rstrip("0").rstrip(".")
    except Exception:
        return norm(value)


def nested(row: dict[str, Any], name: str) -> dict[str, Any]:
    value = row.get(name)
    return value if isinstance(value, dict) else {}


def first_nonempty(*values: Any) -> Any:
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(str(value).replace(",", "."))
    except Exception:
        return default


def selection_key_from_text(value: Any) -> str:
    text = norm(value)
    if any(token in text for token in ("меньше", "under", "menshe", "тм", "tm")):
        return "under"
    if any(token in text for token in ("больше", "over", "bolshe", "тб", "tb")):
        return "over"
    if text in {"under", "over", "home", "away", "draw"}:
        return text
    return text


def normalized_kickoff(row: dict[str, Any]) -> str:
    payload = nested(row, "bet_payload")
    value = first_nonempty(
        row.get("commence_time"), row.get("kickoff"), row.get("kickoff_utc"), row.get("start_time"),
        payload.get("commence_time"), payload.get("kickoff"), payload.get("start_time"),
    )
    dt = parse_dt(value)
    if dt:
        return dt.replace(second=0, microsecond=0).isoformat()
    return str(value or "")[:16]


def semantic_key_raw(row: dict[str, Any]) -> str:
    payload = nested(row, "bet_payload")
    match_key = first_nonempty(row.get("match_key"), row.get("canonical_match_id"), payload.get("match_key"))
    home = first_nonempty(row.get("home_team"), row.get("home"), payload.get("home_team"), payload.get("home"))
    away = first_nonempty(row.get("away_team"), row.get("away"), payload.get("away_team"), payload.get("away"))
    family = first_nonempty(row.get("family"), row.get("market_family"), payload.get("family"), payload.get("market_family"))
    selection = first_nonempty(row.get("selection_key"), row.get("selection"), payload.get("selection_key"), payload.get("selection"))
    point = first_nonempty(row.get("point"), row.get("line"), row.get("handicap"), payload.get("point"), payload.get("line"), payload.get("handicap"))
    return "|".join([
        norm(match_key or ""),
        norm(home or ""),
        norm(away or ""),
        normalized_kickoff(row),
        norm(family or ""),
        selection_key_from_text(selection or ""),
        point_text(point),
    ])


def row_key(row: dict[str, Any]) -> str:
    raw = semantic_key_raw(row)
    if raw.strip("|"):
        return hashlib.sha1(raw.encode("utf-8")).hexdigest()
    fallback = json.dumps(row, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(fallback.encode("utf-8")).hexdigest()


def truthy(value: Any) -> bool:
    return str(value if value is not None else "").strip().lower() in {"1", "true", "yes", "on", "sent", "published"}


def is_sent(row: dict[str, Any]) -> bool:
    summary = nested(row, "source_summary")
    for key in ("telegram_sent", "published", "sent", "is_published"):
        for obj in (row, summary):
            if truthy(obj.get(key)):
                return True
    status = str(row.get("publication_lifecycle_status") or row.get("status") or "").strip().lower()
    return status in {"telegram_sent", "published", "sent", "posted", "pending", "open", "active"} and bool(row.get("published_at_utc") or row.get("published_at") or row.get("sent_at"))


def iter_container_rows(payload: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return out
    for key in (
        "rows", "picks", "bets", "pending", "published", "published_candidates", "published_picks",
        "selected", "selected_rows", "selected_picks", "top_picks", "telegram_picks", "sent_picks", "published_rows",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            out.extend(x for x in value if isinstance(x, dict))
    return out


def collect_state_rows() -> list[dict[str, Any]]:
    state = load_json(STATE_JSON, {})
    rows: list[dict[str, Any]] = []
    if isinstance(state, dict):
        for key in ("bets", "published_candidates"):
            value = state.get(key)
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, dict) and (is_sent(item) or str(item.get("status") or "") in SETTLED_STATUSES):
                        row = dict(item)
                        row.setdefault("ledger_source_file", ".data/state.json")
                        rows.append(row)
    return rows


def parse_pick_line(line: str) -> tuple[str, str] | None:
    m = re.match(r"^\s*\d+\.\s+(.+?)\s+[—-]\s+(.+?)\s*$", line)
    if not m:
        return None
    left, right = m.groups()
    if any(marker in left.lower() for marker in ("причина", "run url", "artifact")):
        return None
    return left.strip(), right.strip()


def parse_telegram_text(text: str, source_name: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    in_fire = False
    current: dict[str, Any] | None = None
    current_header_dt: datetime | None = None

    def flush() -> None:
        nonlocal current
        if not current:
            return
        if current.get("home_team") and current.get("away_team") and current.get("odds") and current.get("commence_time"):
            current.setdefault("family", "totals")
            current.setdefault("selection_key", selection_key_from_text(current.get("selection")))
            current.setdefault("status", "pending")
            current.setdefault("telegram_sent", True)
            current.setdefault("published", True)
            current.setdefault("publication_lifecycle_status", "telegram_sent")
            current.setdefault("publication_lifecycle_stage", "telegram_sent")
            current.setdefault("source", "telegram_text_import")
            current.setdefault("ledger_source_file", source_name)
            current.setdefault("published_at_utc", (current_header_dt or datetime.now(UTC)).isoformat())
            rows.append(dict(current))
        current = None

    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        ts = re.match(r"^\[(\d{2}\.\d{2}\.\d{4}\s+\d{1,2}:\d{2})\]\s+HARIZON:\s*(.*)$", line)
        if ts:
            current_header_dt = parse_msk_dt(ts.group(1)) or current_header_dt
            line = ts.group(2).strip()
            if "🔥" not in line and in_fire and ("🧾" in line or "отч" in line.lower()):
                flush()
                in_fire = False
        if "🔥" in line and "прогноз" in line.lower():
            flush()
            in_fire = True
            continue
        if in_fire and (line.startswith("🧾") or "подробный отч" in line.lower() or line.startswith("🚫") or line.startswith("🔎 Проверенные")):
            flush()
            in_fire = False
            continue
        if not in_fire:
            continue
        pick = parse_pick_line(line)
        if pick:
            flush()
            home, away = pick
            current = {
                "home_team": home,
                "away_team": away,
                "match_key": f"telegram|{norm(home)}|{norm(away)}",
                "sport_key": "soccer",
            }
            continue
        if current is None:
            continue
        if line.startswith("🎯"):
            current["market_label"] = line.split(":", 1)[-1].strip()
            selection_text = current["market_label"]
            m = re.search(r"(Больше|Меньше|Over|Under)\s*\(?\s*([0-9]+(?:[\.,][0-9]+)?)?\s*\)?", selection_text, flags=re.I)
            if m:
                current["selection"] = m.group(1)
                current["selection_key"] = selection_key_from_text(m.group(1))
                if m.group(2):
                    current["point"] = as_float(m.group(2))
            if "тотал" in norm(selection_text):
                current["family"] = "totals"
        elif line.startswith("💸"):
            value = line.split(":", 1)[-1]
            current["odds"] = as_float(value)
            current["selected_odds"] = current["odds"]
        elif line.startswith("✅"):
            m = re.search(r"уровень\s+([ABC])", line, flags=re.I)
            if m:
                current["tier"] = m.group(1).upper()
                current["publication_tier"] = current["tier"]
            m = re.search(r"Уверенность:\s*([0-9]+(?:[\.,][0-9]+)?)", line)
            if m:
                current["confidence"] = as_float(m.group(1))
            m = re.search(r"качество\s+([0-9]+(?:[\.,][0-9]+)?)", line)
            if m:
                current["quality_score"] = as_float(m.group(1))
        elif line.startswith("📚"):
            m = re.search(r"Линии:\s*(\d+)", line)
            if m:
                current["books_count"] = int(m.group(1))
            m = re.search(r"odds sources:\s*(\d+)", line, flags=re.I)
            if m:
                current["odds_sources_count"] = int(m.group(1))
            m = re.search(r"confirmation sources:\s*(\d+)", line, flags=re.I)
            if m:
                current["confirmation_sources_count"] = int(m.group(1))
        elif line.startswith("🔎 Подтверждения"):
            value = line.split(":", 1)[-1].strip()
            current["confirmation_sources"] = [x.strip() for x in re.split(r"[,|/]", value) if x.strip()]
        elif line.startswith("🧮"):
            m = re.search(r"запас\s*([+\-]?[0-9]+(?:[\.,][0-9]+)?)", line)
            if m:
                current["edge_pp"] = as_float(m.group(1))
            m = re.search(r"EV\s*([+\-]?[0-9]+(?:[\.,][0-9]+)?)", line)
            if m:
                current["ev_pct"] = as_float(m.group(1))
        elif line.startswith("🏆"):
            current["league_name"] = line.split(":", 1)[-1].strip()
        elif line.startswith("🕒"):
            dt = parse_msk_dt(line)
            if dt:
                current["commence_time"] = dt.isoformat()
                current["kickoff_utc"] = dt.isoformat()
                current["kickoff_local_text"] = line.split(":", 1)[-1].strip()
        elif line.startswith("💰"):
            m = re.search(r"([0-9]+(?:[\.,][0-9]+)?)", line)
            if m:
                stake = as_float(m.group(1), 0.0)
                current["stake"] = stake
                current["stake_amount"] = stake
    flush()
    return rows


def collect_text_rows() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    paths = list(EXPORT_DIR.glob("latest-harizon-telegram-run-report*.txt")) + [EXPORT_DIR / "latest-run-bot.log"]
    for path in paths:
        try:
            if path.exists() and path.stat().st_size > 0:
                rows.extend(parse_telegram_text(path.read_text(encoding="utf-8", errors="replace"), str(path)))
        except Exception:
            pass
    import_dir = ROOT / ".data" / "imports"
    if import_dir.exists():
        for path in import_dir.glob("*.txt"):
            try:
                rows.extend(parse_telegram_text(path.read_text(encoding="utf-8", errors="replace"), str(path)))
            except Exception:
                pass
    return rows


def collect_publication_rows() -> list[dict[str, Any]]:
    paths = [
        EXPORT_DIR / "latest-controlled-fallback-published-picks.json",
        EXPORT_DIR / "published-picks-ledger.json",
        EXPORT_DIR / "controlled-fallback-published-ledger.json",
        EXPORT_DIR / "published-bets-ledger.json",
        EXPORT_DIR / "latest-picks.json",
        EXPORT_DIR / "latest-bets.json",
        EXPORT_DIR / "latest-pending-bets.json",
    ]
    rows: list[dict[str, Any]] = []
    rows.extend(collect_state_rows())
    for path in paths:
        payload = load_json(path, [])
        for item in iter_container_rows(payload):
            if is_sent(item) or str(item.get("status") or "") in SETTLED_STATUSES:
                row = dict(item)
                row.setdefault("ledger_source_file", str(path))
                rows.append(row)

    fallback = load_json(EXPORT_DIR / "latest-controlled-fallback-report.json", {})
    if isinstance(fallback, dict) and (fallback.get("published") or as_float(fallback.get("published_count")) > 0):
        for item in iter_container_rows(fallback):
            if item.get("ok") is False and not truthy(item.get("published")):
                continue
            row = dict(item)
            row["telegram_sent"] = True
            row["published"] = True
            row.setdefault("published_at_utc", fallback.get("created_at") or fallback.get("created_at_utc") or datetime.now(UTC).isoformat())
            row.setdefault("source", "controlled_fallback")
            row.setdefault("ledger_source_file", "latest-controlled-fallback-report.json")
            rows.append(row)
    rows.extend(collect_text_rows())
    return rows


def normalize_bet(row: dict[str, Any]) -> dict[str, Any]:
    out = dict(row)
    metrics = out.get("metrics") if isinstance(out.get("metrics"), dict) else {}
    payload = out.get("bet_payload") if isinstance(out.get("bet_payload"), dict) else {}
    now = datetime.now(UTC).isoformat()

    if not out.get("family") and not payload.get("family"):
        label = norm(out.get("market_label") or out.get("selection") or "")
        if "тотал" in label or out.get("point") not in (None, ""):
            out["family"] = "totals"
    if not out.get("selection_key"):
        out["selection_key"] = selection_key_from_text(out.get("selection") or payload.get("selection"))
    if out.get("point") in (None, ""):
        label = str(out.get("market_label") or out.get("selection") or "")
        m = re.search(r"\(?\s*([0-9]+(?:[\.,][0-9]+)?)\s*\)?", label)
        if m:
            out["point"] = as_float(m.group(1))

    source_dedupe = out.get("dedupe_key")
    semantic = row_key(out)
    if source_dedupe and source_dedupe != semantic:
        out.setdefault("source_dedupe_key", source_dedupe)
    out["dedupe_key"] = semantic
    out["ledger_semantic_key"] = semantic
    out["ledger_semantic_key_raw"] = semantic_key_raw(out)
    out.setdefault("fingerprint", semantic)
    out.setdefault("prediction_id", semantic)

    out.setdefault("source", "controlled_fallback")
    out.setdefault("published_by", out.get("source") or "controlled_fallback")
    out.setdefault("telegram_sent", True)
    out.setdefault("published", True)
    out.setdefault("publication_lifecycle_status", "telegram_sent")
    out.setdefault("publication_lifecycle_stage", "telegram_sent")
    out.setdefault("status", "pending")
    out.setdefault("published_at_utc", out.get("published_at") or out.get("sent_at") or out.get("created_at_utc") or now)

    kickoff = first_nonempty(out.get("commence_time"), out.get("kickoff_utc"), out.get("kickoff"), payload.get("commence_time"), payload.get("kickoff"))
    kickoff_dt = parse_dt(kickoff)
    if kickoff_dt:
        out["commence_time"] = kickoff_dt.isoformat()
        out.setdefault("kickoff_utc", kickoff_dt.isoformat())

    stake = first_nonempty(out.get("stake"), out.get("stake_amount"), payload.get("stake"), payload.get("stake_amount"))
    stake_num = as_float(stake, 0.0)
    if stake_num > 0:
        out["stake"] = stake_num
        out["stake_amount"] = stake_num
    else:
        out.setdefault("stake", 0)
        out.setdefault("stake_amount", 0)

    odds = first_nonempty(out.get("odds"), out.get("selected_odds"), out.get("price"), payload.get("odds"), metrics.get("odds"))
    if odds is not None:
        out["odds"] = as_float(odds, 0.0) or odds
        out.setdefault("selected_odds", out["odds"])
    out.setdefault("ev_pct", out.get("ev_pct") if out.get("ev_pct") is not None else metrics.get("canonical_ev_pct"))
    out.setdefault("edge_pp", out.get("edge_pp") if out.get("edge_pp") is not None else metrics.get("canonical_edge_pp"))
    out.setdefault("quality_score", out.get("quality_score") if out.get("quality_score") is not None else metrics.get("quality_score"))
    out.setdefault("books_count", out.get("books_count") if out.get("books_count") is not None else metrics.get("books_count"))
    out.setdefault("confirmation_sources", out.get("confirmation_sources") or metrics.get("confirmation_sources"))
    out.setdefault("confirmation_sources_count", out.get("confirmation_sources_count") or metrics.get("confirmation_sources_count"))

    settlement = out.get("settlement") if isinstance(out.get("settlement"), dict) else {}
    outcome = str(settlement.get("outcome") or out.get("outcome") or out.get("result") or "").strip().lower()
    if outcome in SETTLED_STATUSES:
        out["status"] = outcome
        out.setdefault("settlement", settlement or {"outcome": outcome})
    return out


def row_score(row: dict[str, Any]) -> tuple[int, int, int, int, float, int]:
    metrics = row.get("metrics") if isinstance(row.get("metrics"), dict) else {}
    stake = max(as_float(row.get("stake")), as_float(row.get("stake_amount")), as_float(nested(row, "bet_payload").get("stake")), as_float(nested(row, "bet_payload").get("stake_amount")))
    has_payload = 1 if isinstance(row.get("bet_payload"), dict) and row.get("bet_payload") else 0
    source_count = len(row.get("confirmation_sources") or metrics.get("confirmation_sources") or []) if isinstance(row.get("confirmation_sources") or metrics.get("confirmation_sources"), list) else 0
    metric_size = len(json.dumps(metrics, ensure_ascii=False, sort_keys=True)) if metrics else 0
    published = 1 if is_sent(row) else 0
    settled = 2 if str(row.get("status") or "").lower() in SETTLED_STATUSES else 0
    created = parse_dt(row.get("published_at_utc") or row.get("published_at") or row.get("sent_at") or row.get("created_at_utc"))
    created_ts = int(created.timestamp()) if created else 0
    return (settled, published, 1 if stake > 0 else 0, has_payload + source_count, stake, metric_size + created_ts // 100000)


def merge_rows(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    base, extra = (new, old) if row_score(new) >= row_score(old) else (old, new)
    out = dict(base)
    for key, value in extra.items():
        if out.get(key) in (None, "", [], {}, 0) and value not in (None, "", [], {}):
            out[key] = value
    source_keys = []
    for row in (old, new):
        for key in (row.get("source_dedupe_key"), row.get("dedupe_key")):
            if key and key not in source_keys:
                source_keys.append(str(key))
    if source_keys:
        out["source_dedupe_keys"] = source_keys
    stake = max(as_float(old.get("stake")), as_float(old.get("stake_amount")), as_float(new.get("stake")), as_float(new.get("stake_amount")))
    if stake > 0:
        out["stake"] = stake
        out["stake_amount"] = stake
    out["dedupe_key"] = row_key(out)
    out["ledger_semantic_key"] = out["dedupe_key"]
    out["ledger_semantic_key_raw"] = semantic_key_raw(out)
    out.setdefault("fingerprint", out["dedupe_key"])
    out.setdefault("prediction_id", out["dedupe_key"])
    return out


def merge_by_key(existing: list[dict[str, Any]], rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    duplicate_rows = 0
    input_rows = 0
    for row in existing + rows:
        if not isinstance(row, dict):
            continue
        input_rows += 1
        normalized = normalize_bet(row)
        key = row_key(normalized)
        if key in out:
            duplicate_rows += 1
            out[key] = merge_rows(out[key], normalized)
        else:
            out[key] = normalized
    merged = sorted(out.values(), key=lambda item: str(item.get("published_at_utc") or item.get("published_at") or item.get("sent_at") or item.get("created_at_utc") or ""))
    return merged, {"input_rows": input_rows, "unique_rows": len(merged), "duplicates_removed": duplicate_rows}


def is_pending(row: dict[str, Any]) -> bool:
    status = str(row.get("status") or "pending").strip().lower()
    settlement = row.get("settlement") if isinstance(row.get("settlement"), dict) else {}
    result = str(settlement.get("outcome") or settlement.get("result") or row.get("result") or row.get("outcome") or "").strip().lower()
    return status in PENDING_STATUSES and status not in SETTLED_STATUSES and result not in SETTLED_STATUSES


def mirror_to_state(merged: list[dict[str, Any]]) -> dict[str, Any]:
    state = load_json(STATE_JSON, {})
    if not isinstance(state, dict):
        state = {}
    state.setdefault("version", 4)
    bets = [dict(x) for x in (state.get("bets") or []) if isinstance(x, dict)]
    by_key: dict[str, dict[str, Any]] = {}
    for row in bets:
        key = str(row.get("ledger_semantic_key") or row.get("dedupe_key") or row.get("fingerprint") or row_key(row))
        by_key[key] = normalize_bet(row)
    added = 0
    updated = 0
    for row in merged:
        item = normalize_bet(row)
        key = str(item.get("ledger_semantic_key") or item.get("dedupe_key") or row_key(item))
        if not is_pending(item) and str(item.get("status") or "") not in SETTLED_STATUSES:
            continue
        before = by_key.get(key)
        if before is None:
            by_key[key] = item
            added += 1
        else:
            merged_row = merge_rows(before, item)
            if json.dumps(merged_row, ensure_ascii=False, sort_keys=True) != json.dumps(before, ensure_ascii=False, sort_keys=True):
                updated += 1
            by_key[key] = merged_row
    state["bets"] = sorted(by_key.values(), key=lambda item: str(item.get("published_at_utc") or item.get("published_at") or item.get("commence_time") or ""))
    bank = state.setdefault("bankroll", {})
    defaults = {"enabled": True, "currency": "units", "starting_balance": 1000.0, "current_balance": 1000.0, "peak_balance": 1000.0}
    for k, v in defaults.items():
        bank.setdefault(k, v)
    pending_stake = sum(as_float(row.get("stake_amount") or row.get("stake")) for row in state["bets"] if is_pending(row))
    total_staked = sum(as_float(row.get("stake_amount") or row.get("stake")) for row in state["bets"] if truthy(row.get("telegram_sent")) or str(row.get("status") or "") in SETTLED_STATUSES)
    closed_pnl = sum(as_float(nested(row, "settlement").get("pnl")) for row in state["bets"] if str(row.get("status") or "") in SETTLED_STATUSES)
    bank["open_exposure"] = round(pending_stake, 2)
    bank["total_staked"] = round(total_staked, 2)
    bank["closed_pnl"] = round(closed_pnl, 2)
    bank["bets_published"] = len([row for row in state["bets"] if truthy(row.get("telegram_sent")) or str(row.get("status") or "") in SETTLED_STATUSES])
    bank["bets_settled"] = len([row for row in state["bets"] if str(row.get("status") or "") in SETTLED_STATUSES])
    bank["wins"] = len([row for row in state["bets"] if str(row.get("status") or "") in {"won", "half_won"}])
    bank["losses"] = len([row for row in state["bets"] if str(row.get("status") or "") in {"lost", "half_lost"}])
    bank["pushes"] = len([row for row in state["bets"] if str(row.get("status") or "") == "push"])
    bank["voids"] = len([row for row in state["bets"] if str(row.get("status") or "") == "void"])
    state["updated_at"] = datetime.now(UTC).isoformat()
    write_json(STATE_JSON, state)
    return {"state_bets": len(state["bets"]), "state_added": added, "state_updated": updated, "state_open_exposure": bank["open_exposure"]}


def sync_bets() -> dict[str, Any]:
    existing = iter_jsonl(PUBLISHED_JSONL) + iter_jsonl(SETTLED_JSONL)
    collected = collect_publication_rows()
    rows = [normalize_bet(row) for row in collected]
    merged, stats = merge_by_key(existing, rows)
    pending, pending_stats = merge_by_key([], [row for row in merged if is_pending(row)])
    settled, settled_stats = merge_by_key([], [row for row in merged if not is_pending(row)])
    if rows or existing or merged:
        write_jsonl(PUBLISHED_JSONL, merged)
        write_json(PENDING_JSON, pending)
        write_jsonl(SETTLED_JSONL, settled)
        write_json(EXPORT_DIR / "latest-pending-bets.json", pending)
        write_json(EXPORT_DIR / "latest-picks.json", merged)
        write_json(EXPORT_DIR / "latest-settled-bets.json", settled)
    state_stats = mirror_to_state(merged) if merged else {"state_bets": 0, "state_added": 0, "state_updated": 0, "state_open_exposure": 0.0}
    return {
        "new_rows_seen": len(rows),
        "published_ledger_rows": len(merged),
        "unique_published_bets": len(merged),
        "duplicates_removed": stats.get("duplicates_removed", 0),
        "published_input_rows": stats.get("input_rows", 0),
        "pending_unique_rows": pending_stats.get("unique_rows", 0),
        "pending_duplicates_removed": pending_stats.get("duplicates_removed", 0),
        "settled_unique_rows": settled_stats.get("unique_rows", 0),
        "settled_duplicates_removed": settled_stats.get("duplicates_removed", 0),
        "text_import_rows": len(collect_text_rows()),
        "state_mirror": state_stats,
        "dedupe_policy": "semantic_match_market_selection_point_kickoff",
    }


def safe_int(value: Any) -> int:
    try:
        return int(float(value)) if value not in (None, "") else 0
    except Exception:
        return 0


def sync_run_ledger() -> dict[str, Any]:
    report = load_json(EXPORT_DIR / "latest-harizon-telegram-run-report.json", {})
    fallback = load_json(EXPORT_DIR / "latest-controlled-fallback-report.json", {})
    debug = load_json(ROOT / ".logs" / "debug-last-run.json", {})
    day_summary = load_json(EXPORT_DIR / "latest-day-inventory-summary.json", {})
    if not any(isinstance(x, dict) and x for x in (report, fallback, debug, day_summary)):
        return {"run_appended": False, "reason": "no_runtime_artifacts"}
    summary = debug.get("summary") if isinstance(debug.get("summary"), dict) else {}
    coverage = report.get("coverage") if isinstance(report.get("coverage"), dict) else {}
    funnel = report.get("funnel") if isinstance(report.get("funnel"), dict) else {}
    counts = day_summary.get("counts") if isinstance(day_summary.get("counts"), dict) else {}
    created = report.get("created_at_utc") or fallback.get("created_at") or fallback.get("created_at_utc") or summary.get("current_time_utc") or datetime.now(UTC).isoformat()
    published_count = safe_int(funnel.get("published_count")) or safe_int(fallback.get("published_count")) or (1 if fallback.get("published") else 0)
    row = {
        "created_at_utc": created,
        "source": "run-bot",
        "github_run_id": report.get("github_run_id") or report.get("run_id") or fallback.get("github_run_id"),
        "summary": {
            "matches_seen": coverage.get("matches_seen") or counts.get("matches_seen_latest_run") or summary.get("matches_seen"),
            "matches_with_offers": coverage.get("matches_with_offers") or counts.get("runtime_matches_with_odds_last_run") or summary.get("matches_with_offers"),
            "contexts_built": coverage.get("matches_with_context") or counts.get("runtime_matches_with_context_last_run") or summary.get("contexts_built"),
            "candidates_raw": funnel.get("raw_candidates") or summary.get("candidates_raw"),
            "candidates_before_quality": funnel.get("candidates_before_quality") or summary.get("candidates_before_quality"),
            "candidates_publishable": funnel.get("publishable_candidates") or summary.get("candidates_publishable"),
            "published": published_count,
            "published_to_telegram": published_count,
            "telegram_messages_sent": 1 if published_count else 0,
            "status": report.get("status") or summary.get("status") or "ok",
        },
        "fallback_status": fallback.get("status") if isinstance(fallback, dict) else None,
        "fallback_published": bool(fallback.get("published")) if isinstance(fallback, dict) else False,
    }
    existing = iter_jsonl(RUN_LEDGER_JSONL)
    stable_raw = str(row.get("github_run_id") or "") + "|" + str(row.get("created_at_utc") or "")[:16]
    key = hashlib.sha1((stable_raw if stable_raw.strip("|") else json.dumps(row, ensure_ascii=False, sort_keys=True)).encode("utf-8")).hexdigest()
    rows_by_key = {hashlib.sha1((str(item.get("github_run_id") or "") + "|" + str(item.get("created_at_utc") or "")[:16] if item.get("github_run_id") else json.dumps(item, ensure_ascii=False, sort_keys=True)).encode("utf-8")).hexdigest(): item for item in existing}
    rows_by_key[key] = row
    write_jsonl(RUN_LEDGER_JSONL, list(rows_by_key.values()))
    return {"run_appended": True, "run_ledger_rows": len(rows_by_key)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--phase", default="default")
    args, _ = parser.parse_known_args()
    BET_DIR.mkdir(parents=True, exist_ok=True)
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "created_at_utc": datetime.now(UTC).isoformat(),
        "phase": args.phase,
        "bets": sync_bets(),
        "runs": sync_run_ledger(),
    }
    write_json(REPORT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
