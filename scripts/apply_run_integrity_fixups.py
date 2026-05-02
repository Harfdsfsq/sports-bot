from __future__ import annotations

"""Apply small runtime fixups before the bot run starts.

This script is intentionally deterministic and non-fatal. It patches three
current production issues:
1) detailed report day-inventory numbers can be stale after coverage merge;
2) controlled fallback can publish reserve picks with only one odds source;
3) SportLogic can treat an empty {success,data:[]} envelope as an odds row.
"""

import re
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]


def _read(path: Path) -> str | None:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return None


def _write_if_changed(path: Path, old: str | None, new: str | None) -> bool:
    if old is None or new is None or old == new:
        return False
    try:
        path.write_text(new, encoding="utf-8")
        return True
    except Exception:
        return False


def _patch_file(rel_path: str, patcher: Callable[[str], str]) -> bool:
    path = ROOT / rel_path
    text = _read(path)
    if text is None:
        return False
    try:
        updated = patcher(text)
    except Exception:
        return False
    return _write_if_changed(path, text, updated)


def _patch_detailed_run_report(text: str) -> str:
    if "coverage_merge_counts_applied" in text and "runtime_counts_applied" in text:
        return text
    pattern = (
        r"def day_inventory_summary\(\) -> dict\[str, Any\]:\n"
        r"    return load_json\(\"\.data/exports/latest-day-inventory-summary\.json\", \{\}\)\n"
    )
    replacement = '''def day_inventory_summary() -> dict[str, Any]:
    summary = load_json(".data/exports/latest-day-inventory-summary.json", {})
    if not isinstance(summary, dict):
        summary = {}
    counts = summary.setdefault("counts", {})
    if not isinstance(counts, dict):
        counts = {}
        summary["counts"] = counts

    merge = load_json(".data/exports/latest-day-inventory-coverage-merge.json", {})
    audit = load_json(".data/exports/latest-day-inventory-coverage-audit.json", {})
    debug = load_json(".logs/debug-last-run.json", {})

    runtime_counts = merge.get("runtime_counts") if isinstance(merge, dict) else {}
    if not isinstance(runtime_counts, dict):
        runtime_counts = {}
    if not runtime_counts and isinstance(debug, dict):
        dbg_summary = debug.get("summary") if isinstance(debug.get("summary"), dict) else debug
        runtime_counts = {
            "matches_seen": as_int(dbg_summary.get("matches_seen")),
            "matches_with_odds": as_int(dbg_summary.get("matches_with_offers")),
            "matches_with_context": as_int(dbg_summary.get("contexts_built")),
            "matches_ready_for_model": as_int(dbg_summary.get("contexts_built")),
        }

    merge_total = as_int(merge.get("matches_total")) if isinstance(merge, dict) else 0
    merge_odds = as_int(merge.get("matches_with_odds")) if isinstance(merge, dict) else 0
    merge_context = as_int(merge.get("matches_with_context")) if isinstance(merge, dict) else 0
    merge_ready = as_int(merge.get("matches_ready_for_model")) if isinstance(merge, dict) else 0
    if isinstance(audit, dict):
        merge_total = max(merge_total, as_int(audit.get("matches_total")))
        merge_odds = max(merge_odds, as_int(audit.get("matches_with_odds")))
        merge_context = max(merge_context, as_int(audit.get("matches_with_context")))
        merge_ready = max(merge_ready, as_int(audit.get("matches_ready_for_model")))

    runtime_total = as_int(runtime_counts.get("matches_seen"))
    runtime_odds = as_int(runtime_counts.get("matches_with_odds"))
    runtime_context = as_int(runtime_counts.get("matches_with_context"))
    runtime_ready = as_int(runtime_counts.get("matches_ready_for_model"))

    counts["matches_total"] = max(as_int(counts.get("matches_total")), merge_total, runtime_total)
    counts["matches_with_odds"] = max(as_int(counts.get("matches_with_odds")), merge_odds, runtime_odds)
    counts["matches_with_context"] = max(as_int(counts.get("matches_with_context")), merge_context, runtime_context)
    counts["matches_ready_for_model"] = max(as_int(counts.get("matches_ready_for_model")), merge_ready, runtime_ready)
    summary["runtime_counts_applied"] = bool(runtime_counts)
    summary["coverage_merge_counts_applied"] = bool(merge_total or merge_odds or merge_context or merge_ready)
    return summary
'''
    updated, count = re.subn(pattern, replacement, text, count=1)
    return updated if count else text


def _patch_controlled_fallback(text: str) -> str:
    if "controlled_fallback_odds_sources_below_min" in text:
        return text
    marker = '    if metrics["sources_count"] <= 0:\n        reasons.append("missing_sources")\n'
    if marker not in text:
        marker = "    if metrics['sources_count'] <= 0:\n        reasons.append('missing_sources')\n"
    if marker not in text:
        return text
    injection = marker + '''    if env_bool("CONTROLLED_FALLBACK_REQUIRE_ODDS_SOURCE_DIVERSITY", True):
        source_summary = candidate.get("source_summary") if isinstance(candidate.get("source_summary"), dict) else {}
        odds_sources_count = as_int(
            metrics.get("odds_sources_count")
            or metrics.get("odds_sources")
            or candidate.get("odds_sources_count")
            or candidate.get("odds_sources")
            or source_summary.get("odds_sources_count")
            or source_summary.get("odds_sources")
        )
        if odds_sources_count <= 0:
            selected_source_name = selected_source(candidate)
            odds_sources_count = 1 if selected_source_name else 0
        min_odds_sources = max(1, env_int("CONTROLLED_FALLBACK_MIN_ODDS_SOURCES", 2))
        if odds_sources_count < min_odds_sources:
            reasons.append(f"controlled_fallback_odds_sources_below_min:{odds_sources_count}/{min_odds_sources}")
'''
    return text.replace(marker, injection, 1)


def _patch_sportlogic_provider(text: str) -> str:
    if "sportlogic_empty_envelope_guard" in text:
        return text

    old_limit = '''        self.odds_match_limit = max(
            1,
            int(float(
                getattr(settings, "sportlogic_odds_match_limit", None)
                or os.getenv("SPORTLOGIC_ODDS_MATCH_LIMIT")
                or 40
            )),
        )
'''
    new_limit = '''        self.odds_match_limit = max(
            0,
            int(float(
                getattr(settings, "sportlogic_odds_match_limit", None)
                if getattr(settings, "sportlogic_odds_match_limit", None) is not None
                else os.getenv("SPORTLOGIC_ODDS_MATCH_LIMIT", "40")
            )),
        )
'''
    if old_limit in text:
        text = text.replace(old_limit, new_limit, 1)

    marker = '        prioritized_items = list(mapping.values())[: self.odds_match_limit]\n\n        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:\n'
    if marker in text and "odds_probe_disabled_by_config" not in text:
        text = text.replace(
            marker,
            '        prioritized_items = list(mapping.values())[: self.odds_match_limit]\n'
            '        if self.odds_match_limit <= 0:\n'
            '            stats["events_matched"] = len(mapping)\n'
            '            stats["games_fetched"] = int(stats.get("fixtures_fetched", 0) or 0)\n'
            '            stats["odds_disabled_reason"] = "odds_probe_disabled_by_config"\n'
            '            self._write_debug_export(stats, preview)\n'
            '            return {}, stats, preview\n\n'
            '        async with httpx.AsyncClient(timeout=self.timeout, follow_redirects=True) as client:\n',
            1,
        )

    new_extract = '''    @staticmethod
    def _extract_odds_rows(payload: Any) -> list[dict[str, Any]]:
        # sportlogic_empty_envelope_guard: {"success": true, "data": []} is not an odds row.
        if isinstance(payload, list):
            return [row for row in payload if isinstance(row, dict)]
        if not isinstance(payload, dict):
            return []
        for key in ("data", "response", "results", "fixtures", "matches", "events", "items", "odds", "markets", "bookmakers"):
            if key not in payload:
                continue
            value = payload.get(key)
            if isinstance(value, list):
                return [row for row in value if isinstance(row, dict)]
            if isinstance(value, dict):
                nested = SportLogicProvider._extract_odds_rows(value)
                if nested:
                    return nested
                # A nested empty envelope should not promote the parent envelope to an odds row.
                return []
        keys = {str(key).lower() for key in payload.keys()}
        shape_keys = {
            "price", "decimal_odds", "value", "odd", "odds", "decimal",
            "market", "market_name", "market_key", "selection", "outcome", "option", "option_name",
            "home", "home_odds", "draw", "draw_odds", "away", "away_odds", "odd_1", "odd_x", "odd_2",
            "btts_yes", "btts_no", "both_teams_to_score_yes", "both_teams_to_score_no",
            "bookmaker", "bookmaker_name", "sportsbook", "provider", "book",
        }
        if keys & shape_keys:
            return [payload]
        return []
'''
    text = re.sub(
        r'    @staticmethod\n    def _extract_odds_rows\(payload: Any\) -> list\[dict\[str, Any\]\]:\n.*?\n\n    def _row_to_match',
        new_extract + '\n    def _row_to_match',
        text,
        count=1,
        flags=re.S,
    )

    marker_reason = '''        if int(stats.get("rows_before_parse", 0) or 0) > 0 and int(stats.get("offers_parsed", 0) or 0) <= 0:
            if reject_reasons and "missing_or_invalid_price" in reject_reasons:
                stats["odds_disabled_reason"] = "price_missing_in_payload"
            else:
                stats["odds_disabled_reason"] = "parser_shape_unmatched"
'''
    if marker_reason in text and "no_odds_rows_returned" not in text:
        text = text.replace(
            marker_reason,
            marker_reason + '        elif int(stats.get("odds_requests", 0) or 0) > 0 and int(stats.get("rows_before_parse", 0) or 0) <= 0:\n            stats["odds_disabled_reason"] = "no_odds_rows_returned"\n',
            1,
        )
    return text


def apply_all() -> dict[str, bool]:
    return {
        "detailed_run_report": _patch_file("scripts/build_detailed_run_report.py", _patch_detailed_run_report),
        "controlled_fallback": _patch_file("scripts/publish_controlled_fallback.py", _patch_controlled_fallback),
        "sportlogic_provider": _patch_file("app/providers/sportlogic_provider.py", _patch_sportlogic_provider),
    }


def main() -> int:
    result = apply_all()
    out = ROOT / ".data" / "exports" / "latest-run-integrity-fixups.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(str(result).replace("'", '"').lower() + "\n", encoding="utf-8")
    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
