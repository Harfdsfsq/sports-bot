from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))


def _script_name() -> str:
    return Path(str(sys.argv[0] or "")).name


def _argv_text() -> str:
    return " ".join(str(x) for x in sys.argv)


def _is_main_run_once() -> bool:
    text = _argv_text()
    return os.getenv("HARIZON_FORCE_RUNTIME_PATCH_INSTALL") == "1" or (
        "run-once" in text and ("app.cli" in text or "python -m app.cli" in text or _script_name() in {"app.cli", "python -m app.cli"})
    )


def _is_readonly_helper() -> bool:
    name = _script_name()
    return (
        name.startswith("send_harizon_telegram_run_report")
        or name in {
            "publish_controlled_fallback.py",
            "publish_controlled_fallback_guarded.py",
            "day_inventory_cumulative_coverage.py",
            "apply_publication_family_policy.py",
            "apply_provider_quota_governor.py",
            "apply_provider_request_budget.py",
            "apply_per_run_api_quota_contract.py",
        }
        or os.getenv("HARIZON_CONTROLLED_FALLBACK_REDIRECTED") == "1"
    )


def _redirect_controlled_fallback_entrypoint() -> None:
    try:
        if os.getenv("HARIZON_CONTROLLED_FALLBACK_REDIRECTED"):
            return
        if _script_name() != "publish_controlled_fallback.py":
            return
        target = SCRIPTS / "publish_controlled_fallback_guarded.py"
        if not target.exists():
            return
        os.environ["HARIZON_CONTROLLED_FALLBACK_REDIRECTED"] = "1"
        os.execv(sys.executable, [sys.executable, str(target), *sys.argv[1:]])
    except Exception as exc:
        try:
            print(f"root sitecustomize controlled fallback redirect skipped: {type(exc).__name__}: {exc}")
        except Exception:
            pass


_redirect_controlled_fallback_entrypoint()


def _set(name: str, value: str) -> None:
    os.environ[name] = str(value)


def _local_hour() -> int:
    tz_name = os.getenv("APP_TIMEZONE") or os.getenv("TZ") or "Europe/Moscow"
    try:
        tz = ZoneInfo(str(tz_name))
    except Exception:
        tz = timezone.utc
    return datetime.now(timezone.utc).astimezone(tz).hour


def _runtime_phase() -> str:
    explicit = str(os.getenv("HARIZON_RUN_PHASE") or os.getenv("RUN_PHASE") or "").strip().lower()
    if explicit:
        return explicit
    hour = _local_hour()
    if 0 <= hour <= 2:
        return "full_inventory"
    if 3 <= hour <= 10:
        return "morning_backfill"
    return "live_refresh"


def _apply_common_prediction_contract() -> None:
    common = {
        "HARIZON_PHASE_POLICY_ENABLED": "true",
        "DAY_INVENTORY_USE_FOR_RUN": "true",
        "DAY_INVENTORY_NEAR_WINDOW_PRIORITY": "true",
        "DAY_INVENTORY_NEAR_WINDOW_HOURS": "12",
        "CONTEXT_ENRICHMENT_REQUIRES_OFFERS": "false",
        "PROVIDER_CONTEXT_SOURCES_DO_NOT_CONFIRM_PRICE": "true",
        "PUBLISH_PRICE_CONFIRMATION_MODE": "bookmakers",
        "PUBLISH_MIN_ODDS_SOURCES": "1",
        "PUBLISH_MIN_BOOKS": "2",
        "CORE_COVERAGE_MIN_ODDS_SOURCES": "1",
        "CORE_COVERAGE_MIN_BOOKMAKERS": "2",
        "CONTROLLED_FALLBACK_TIER_A_MIN_ODDS_SOURCES": "1",
        "CONTROLLED_FALLBACK_TIER_B_MIN_ODDS_SOURCES": "1",
        "CONTROLLED_FALLBACK_TIER_C_MIN_ODDS_SOURCES": "1",
        "CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS": "2",
        "CONTROLLED_FALLBACK_TIER_B_MIN_BOOKS": "2",
        "CONTROLLED_FALLBACK_TIER_C_MIN_BOOKS": "2",
        "MIN_BOOKS_FOR_CONSENSUS": "2",
        "MIN_BOOKS_PUBLISH": "2",
        "MIN_SOURCES_PUBLISH": "1",
        "MARKET_DERIVED_MIN_BOOKS": "2",
        "MARKET_DERIVED_MIN_SOURCES": "1",
        "CONTROLLED_FALLBACK_MIN_ODDS_SOURCES": "1",
        "CONTROLLED_FALLBACK_REQUIRE_2_BOOKS_FOR_TELEGRAM": "true",
        "CONTROLLED_FALLBACK_REQUIRE_2_ODDS_SOURCES_FOR_TELEGRAM": "false",
        "CONTROLLED_FALLBACK_VISIBLE_MIN_CANONICAL_EV_PCT": "0.0",
        "CONTROLLED_FALLBACK_VISIBLE_MIN_CANONICAL_EDGE_PP": "0.0",
        "TELEGRAM_MAIN_PICK_MIN_ODDS_SOURCES": "1",
        "TELEGRAM_MAIN_PICK_MIN_EDGE_PP": "3.0",
        "SECONDARY_ODDS_RESCUE_ENABLED": "true",
        "BZZOIRO_ODDS_REKEY_ENABLED": "true",
        "MARKET_DERIVED_SINGLE_SNAPSHOT_CONSENSUS_ENABLED": "true",
    }
    for key, value in common.items():
        _set(key, value)


def _phase_env(phase: str) -> dict[str, str]:
    base = {
        "MATCH_BOOTSTRAP_PROVIDER": "odds_api_io",
        "DAY_INVENTORY_BOOTSTRAP_PROVIDER": "odds_api_io",
        "FIXTURE_EXPANSION_ENABLED": "true",
        "RUN_DAYS_AHEAD": "1",
        "SECONDARY_ODDS_RESCUE_TRIGGER": "thin_primary_market_depth",
    }
    if phase == "full_inventory":
        base.update({
            "PUBLISH_WINDOW_HOURS": "24",
            "MAX_MATCHES_FOR_ODDS_FETCH": "900",
            "ANALYSIS_MATCH_CAP_PER_RUN": "900",
            "CONTEXT_ENRICHMENT_MATCH_LIMIT": "120",
        })
    elif phase == "morning_backfill":
        base.update({
            "PUBLISH_WINDOW_HOURS": "12",
            "MAX_MATCHES_FOR_ODDS_FETCH": "650",
            "ANALYSIS_MATCH_CAP_PER_RUN": "650",
            "CONTEXT_ENRICHMENT_MATCH_LIMIT": "260",
        })
    else:
        base.update({
            "PUBLISH_WINDOW_HOURS": "12",
            "MAX_MATCHES_FOR_ODDS_FETCH": "520",
            "ANALYSIS_MATCH_CAP_PER_RUN": "520",
            "CONTEXT_ENRICHMENT_MATCH_LIMIT": "240",
        })
    return base


def _apply_phase_policy() -> None:
    phase = _runtime_phase()
    _set("HARIZON_RUN_PHASE_EFFECTIVE", phase)
    _apply_common_prediction_contract()
    env = _phase_env(phase)
    for key, value in env.items():
        _set(key, value)
    try:
        out = ROOT / ".data" / "exports" / "latest-run-phase-policy.env"
        out.parent.mkdir(parents=True, exist_ok=True)
        lines = [f"HARIZON_RUN_PHASE_EFFECTIVE={phase}"] + [f"{k}={v}" for k, v in sorted(env.items())]
        out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except Exception:
        pass


try:
    _apply_phase_policy()
except Exception as exc:
    try:
        print(f"root sitecustomize phase policy skipped: {type(exc).__name__}: {exc}")
    except Exception:
        pass


_LIST_EVIDENCE_FIELDS = (
    "odds_sources",
    "line_sources",
    "books",
    "price_confirmations",
    "context_sources",
    "context_confirmations",
    "fixture_sources",
)
_COUNT_METADATA_FIELDS = (
    "fixture_sources_count",
    "independent_odds_sources_count",
    "odds_sources_count",
    "books_count",
    "price_confirmation_sources_count",
    "price_sources_count",
    "context_sources_count",
    "confirmation_sources_count",
)
_SAMPLE_METADATA_FIELDS = (
    "source_evidence_samples",
    "odds_api_io_backfill_samples",
    "context_source_projection_reasons",
)


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "force"}


def _provider_smoke_preserve_enabled() -> bool:
    return (
        str(os.getenv("APP_ENV") or "").strip().lower() == "provider-smoke-minimal-repair"
        or _truthy(os.getenv("PROVIDER_SMOKE_MINIMAL_REPAIR"))
        or _truthy(os.getenv("DAY_INVENTORY_PRESERVE_CACHED_EVIDENCE"))
    )


def _as_int(value, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def _norm_key_text(value) -> str:
    return re.sub(r"[^a-z0-9]+", "_", str(value or "").strip().lower()).strip("_")


def _row_keys(row: dict) -> list[str]:
    keys: list[str] = []
    for field in ("canonical_match_id", "match_key", "loose_key"):
        value = str(row.get(field) or "").strip()
        if value:
            keys.append(value)
    home = _norm_key_text(row.get("home_team"))
    away = _norm_key_text(row.get("away_team"))
    kickoff = str(row.get("kickoff_utc") or row.get("commence_time") or row.get("kickoff_local") or "")[:16]
    league = _norm_key_text(row.get("league_name"))
    if home and away and kickoff:
        keys.append(f"{home}__{away}__{kickoff}")
        keys.append(f"{league}__{home}__{away}__{kickoff}")
    return list(dict.fromkeys(keys))


def _listify(value) -> list[str]:
    if isinstance(value, list):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, tuple):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, set):
        return [str(x).strip() for x in value if str(x).strip()]
    if isinstance(value, str) and value.strip():
        return [x.strip() for x in re.split(r"[,|;/]+", value) if x.strip()]
    return []


def _uniq(values) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        low = text.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(text)
    return out


def _price_count(row: dict) -> int:
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return max(
        _as_int(md.get("price_confirmation_sources_count")),
        _as_int(md.get("price_sources_count")),
        len(row.get("price_confirmations") or []),
        len(row.get("books") or []),
    )


def _context_count(row: dict) -> int:
    md = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return max(
        _as_int(md.get("context_sources_count")),
        _as_int(md.get("confirmation_sources_count")),
        len(row.get("context_confirmations") or []),
        len(row.get("context_sources") or []),
    )


def _merge_cached_evidence(dst: dict, src: dict, now_iso: str) -> bool:
    before = json.dumps(dst, ensure_ascii=False, sort_keys=True)
    for field in _LIST_EVIDENCE_FIELDS:
        dst[field] = _uniq(_listify(dst.get(field)) + _listify(src.get(field)))
    for field in ("price_backfill", "coverage_gaps"):
        src_val = src.get(field)
        if isinstance(src_val, dict):
            dst_val = dst.get(field) if isinstance(dst.get(field), dict) else {}
            merged = dict(dst_val)
            merged.update({k: v for k, v in src_val.items() if v not in (None, "", [], {})})
            dst[field] = merged
    src_md = src.get("metadata") if isinstance(src.get("metadata"), dict) else {}
    dst_md = dst.get("metadata") if isinstance(dst.get("metadata"), dict) else {}
    for field in _COUNT_METADATA_FIELDS:
        dst_md[field] = max(_as_int(dst_md.get(field)), _as_int(src_md.get(field)))
    for field in _SAMPLE_METADATA_FIELDS:
        if src_md.get(field) and not dst_md.get(field):
            dst_md[field] = src_md[field]
        elif isinstance(src_md.get(field), list) and isinstance(dst_md.get(field), list):
            dst_md[field] = (dst_md[field] + src_md[field])[:12]
    for field in ("odds_api_io_backfill_updated_utc", "source_evidence_updated_utc", "context_source_projection_updated_utc"):
        if src_md.get(field):
            dst_md[field] = max(str(dst_md.get(field) or ""), str(src_md[field])) or src_md[field]
    dst_md["cached_evidence_preserved_utc"] = now_iso
    dst["metadata"] = dst_md
    pc = max(_price_count(dst), _price_count(src))
    cc = max(_context_count(dst), _context_count(src))
    min_price = max(2, _as_int(os.getenv("PUBLISH_MIN_BOOKS") or os.getenv("MIN_BOOKS_PUBLISH") or os.getenv("CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS"), 2))
    min_context = max(2, _as_int(os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH"), 2))
    cov = dst.get("coverage") if isinstance(dst.get("coverage"), dict) else {}
    scov = src.get("coverage") if isinstance(src.get("coverage"), dict) else {}
    cov["odds"] = bool(cov.get("odds")) or bool(scov.get("odds")) or pc > 0
    cov["context"] = bool(cov.get("context")) or bool(scov.get("context")) or cc > 0
    cov["odds_2plus_sources"] = pc >= min_price
    cov["context_2plus_sources"] = cc >= min_context
    cov["ready_for_model"] = bool(cov.get("ready_for_model")) or bool(scov.get("ready_for_model")) or (pc > 0 and cc > 0)
    cov["ready_for_publish"] = bool(cov.get("ready_for_publish")) or bool(scov.get("ready_for_publish")) or (pc >= min_price and cc >= min_context)
    dst["coverage"] = cov
    ref = dst.get("refresh") if isinstance(dst.get("refresh"), dict) else {}
    sref = src.get("refresh") if isinstance(src.get("refresh"), dict) else {}
    for field in ("last_odds_refresh_utc", "last_context_refresh_utc"):
        if sref.get(field):
            ref[field] = max(str(ref.get(field) or ""), str(sref[field])) or sref[field]
    if ref:
        dst["refresh"] = ref
    dst["last_enriched_at"] = max(str(dst.get("last_enriched_at") or ""), str(src.get("last_enriched_at") or ""), now_iso)
    return before != json.dumps(dst, ensure_ascii=False, sort_keys=True)


def _recompute_cached_evidence_counts(rows: list[dict], counts: dict, now_iso: str) -> dict:
    min_price = max(2, _as_int(os.getenv("PUBLISH_MIN_BOOKS") or os.getenv("MIN_BOOKS_PUBLISH") or os.getenv("CONTROLLED_FALLBACK_TIER_A_MIN_BOOKS"), 2))
    min_context = max(2, _as_int(os.getenv("PUBLISH_MIN_CONTEXT_SOURCES") or os.getenv("MIN_CONTEXT_SOURCES_PUBLISH"), 2))
    price2 = context2 = odds_any = context_any = ready_model = ready_publish = 0
    for row in rows:
        pc = _price_count(row)
        cc = _context_count(row)
        cov = row.get("coverage") if isinstance(row.get("coverage"), dict) else {}
        odds_any += int(bool(cov.get("odds")) or pc > 0)
        context_any += int(bool(cov.get("context")) or cc > 0)
        price2 += int(pc >= min_price)
        context2 += int(cc >= min_context)
        ready_model += int(bool(cov.get("ready_for_model")))
        ready_publish += int(bool(cov.get("ready_for_publish")))
    out = dict(counts or {})
    out.update({
        "matches_with_odds": odds_any,
        "matches_with_context": context_any,
        "matches_with_2plus_price_confirmations": price2,
        "matches_with_2plus_odds_sources": price2,
        "matches_with_2plus_context_sources": context2,
        "matches_ready_for_model": ready_model,
        "matches_ready_for_publish": ready_publish,
        "matches_missing_price_2plus": max(0, len(rows) - price2),
        "matches_missing_context_2plus": max(0, len(rows) - context2),
        "cached_evidence_preserve_updated_utc": now_iso,
    })
    return out


def _install_day_inventory_preserve_patch() -> None:
    if not _provider_smoke_preserve_enabled():
        return
    try:
        from app.services.day_inventory import DayInventoryStore
    except Exception:
        return
    if getattr(DayInventoryStore, "_cached_evidence_preserve_patch_installed", False):
        return
    original = DayInventoryStore.build_payload

    def patched_build_payload(self, *args, **kwargs):
        existing = kwargs.get("existing") if isinstance(kwargs.get("existing"), dict) else {}
        payload = original(self, *args, **kwargs)
        try:
            now_iso = datetime.now(timezone.utc).isoformat()
            existing_rows = existing.get("matches") if isinstance(existing.get("matches"), list) else []
            if not existing_rows:
                return payload
            source_by_key: dict[str, dict] = {}
            evidence_rows = 0
            for row in existing_rows:
                if not isinstance(row, dict):
                    continue
                if _price_count(row) <= 0 and _context_count(row) <= 0:
                    continue
                evidence_rows += 1
                for key in _row_keys(row):
                    source_by_key.setdefault(key, row)
            changed = 0
            restored = 0
            rows = payload.get("matches") if isinstance(payload.get("matches"), list) else []
            for row in rows:
                if not isinstance(row, dict):
                    continue
                src = None
                for key in _row_keys(row):
                    src = source_by_key.get(key)
                    if src:
                        break
                if not src:
                    continue
                restored += 1
                changed += int(_merge_cached_evidence(row, src, now_iso))
            payload["counts"] = _recompute_cached_evidence_counts(rows, dict(payload.get("counts") or {}), now_iso)
            sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
            sources["cached_evidence_preserve_patch"] = {
                "updated_at_utc": now_iso,
                "existing_evidence_rows": evidence_rows,
                "restored_matching_rows": restored,
                "rows_changed": changed,
            }
            payload["sources"] = sources
            try:
                out = ROOT / ".data" / "exports" / "latest-day-inventory-cached-evidence-preserve-patch.json"
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps(sources["cached_evidence_preserve_patch"], ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            except Exception:
                pass
        except Exception as exc:
            try:
                print(f"cached evidence preserve patch skipped: {type(exc).__name__}: {exc}")
            except Exception:
                pass
        return payload

    DayInventoryStore.build_payload = patched_build_payload
    DayInventoryStore._cached_evidence_preserve_patch_installed = True


try:
    _install_day_inventory_preserve_patch()
except Exception as exc:
    try:
        print(f"root sitecustomize cached evidence preserve patch skipped: {type(exc).__name__}: {exc}")
    except Exception:
        pass

# No model/provider runtime wrappers are installed here. usercustomize.py installs
# the gated runtime_startup_chain only for the main app.cli run-once process.
