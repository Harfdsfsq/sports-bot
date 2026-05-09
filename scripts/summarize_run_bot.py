from __future__ import annotations

import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc


def _read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _latest_run_archive() -> Path | None:
    candidates = [p for p in Path(".logs/runs").glob("*/*-run.json") if p.is_file()] if Path(".logs/runs").exists() else []
    return sorted(candidates, key=lambda p: (p.parent.name, p.name))[-1] if candidates else None


def _load_payload() -> tuple[dict[str, Any], str]:
    debug = _read_json(Path(".logs/debug-last-run.json"), None)
    if isinstance(debug, dict) and debug:
        return debug, ".logs/debug-last-run.json"
    latest = _latest_run_archive()
    if latest:
        return _read_json(latest, {}), str(latest)
    return {}, ""


def _as_int(value: Any, default: int = 0) -> int:
    if value is None:
        return default
    try:
        return int(float(str(value).replace(",", ".").strip()))
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    if value is None:
        return default
    try:
        return float(str(value).replace(",", ".").strip())
    except (TypeError, ValueError):
        return default


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on"}:
        return True
    if text in {"0", "false", "no", "n", "off"}:
        return False
    return None


def _pick(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return None


def _ratio(numerator: Any, denominator: Any) -> float | None:
    num = _as_float(numerator)
    den = _as_float(denominator)
    if den <= 0:
        return None
    return round(num / den, 4)


def _pct(value: float | None) -> float | None:
    if value is None:
        return None
    return round(value * 100.0, 2)


def _quality_stops(payload: dict[str, Any]) -> dict[str, int]:
    decisions = ((payload.get("quality_debug") or {}).get("decisions") or [])
    counts: Counter[str] = Counter()
    for item in decisions:
        reasons = item.get("reasons") if isinstance(item, dict) else []
        reason = str((reasons or [""])[0] or "unknown")
        if reason:
            counts[reason] += 1
    return dict(counts.most_common())


def _candidate_snapshot(payload: dict[str, Any]) -> list[dict[str, Any]]:
    rows = []
    for item in payload.get("candidates_before_quality") or []:
        if not isinstance(item, dict):
            continue
        ss = item.get("source_summary") if isinstance(item.get("source_summary"), dict) else {}
        rows.append({
            "match_key": item.get("match_key"),
            "league_name": item.get("league_name"),
            "family": item.get("family"),
            "selection": item.get("selection"),
            "point": item.get("point"),
            "odds": item.get("odds"),
            "bookmaker": item.get("bookmaker") or ss.get("selected_bookmaker"),
            "market_probability": item.get("market_probability"),
            "model_probability": item.get("model_probability"),
            "adjusted_probability": item.get("adjusted_probability"),
            "source_summary_adjusted_probability": ss.get("adjusted_probability"),
            "edge_pct": item.get("edge_pct"),
            "ev_pct": item.get("ev_pct"),
            "confidence": item.get("confidence"),
            "publication_score": item.get("publication_score"),
            "quality_status": ss.get("quality_status"),
            "quality_reasons": ss.get("quality_reasons"),
            "model_mode": item.get("model_mode"),
            "sources_count": ss.get("sources_count") or ss.get("source_count") or item.get("sources_count"),
            "bookmakers_count": ss.get("bookmakers_count") or ss.get("bookmaker_count") or item.get("bookmakers_count"),
        })
    return rows


def _summary_int(summary: dict[str, Any], *keys: str) -> int:
    return _as_int(_pick(summary, *keys), 0)


def _runtime_kpis(summary: dict[str, Any]) -> dict[str, Any]:
    matches_seen = _summary_int(summary, "matches_seen", "matches")
    matches_with_offers = _summary_int(summary, "matches_with_offers")
    contexts_built = _summary_int(summary, "contexts_built", "contexts")
    candidates_before_quality = _summary_int(summary, "candidates_before_quality", "raw_candidates")
    candidates_after_quality = _summary_int(summary, "candidates", "candidates_after_quality")
    candidates_publishable = _summary_int(summary, "candidates_publishable", "publishable")
    published = _summary_int(summary, "published")

    offer_coverage = _ratio(matches_with_offers, matches_seen)
    context_coverage = _ratio(contexts_built, matches_with_offers or matches_seen)
    quality_pass_rate = _ratio(candidates_after_quality, candidates_before_quality)
    publishable_rate = _ratio(candidates_publishable, candidates_after_quality or candidates_before_quality)
    published_rate = _ratio(published, candidates_publishable)

    return {
        "matches_seen": matches_seen,
        "matches_before_publish_window": summary.get("matches_before_publish_window"),
        "matches_with_offers": matches_with_offers,
        "contexts_built": contexts_built,
        "candidates_before_quality": candidates_before_quality,
        "candidates_after_quality": candidates_after_quality,
        "candidates_publishable": candidates_publishable,
        "published": published,
        "offer_coverage_pct": _pct(offer_coverage),
        "context_coverage_pct": _pct(context_coverage),
        "quality_pass_rate_pct": _pct(quality_pass_rate),
        "publishable_rate_pct": _pct(publishable_rate),
        "published_rate_pct": _pct(published_rate),
        "dry_run": summary.get("dry_run"),
        "prediction_publication_enabled": summary.get("prediction_publication_enabled"),
    }


def _normalise_provider_stats(summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    source_stats = summary.get("source_stats") if isinstance(summary.get("source_stats"), dict) else {}
    target_counts = summary.get("provider_target_counts") if isinstance(summary.get("provider_target_counts"), dict) else {}
    provider_rows: dict[str, dict[str, Any]] = {}
    provider_names = sorted(source_stats)

    for name in provider_names:
        raw = source_stats.get(name)
        if not isinstance(raw, dict):
            raw = {"value": raw}
        nested_stats = raw.get("stats") if isinstance(raw.get("stats"), dict) else {}
        flat = dict(raw)
        flat.update(nested_stats)

        target_count = _as_int(target_counts.get(name), 0)
        request_count = _as_int(_pick(
            flat,
            "requests",
            "request_count",
            "requests_used",
            "http_requests",
            "http_request_count",
            "req",
            "calls",
            "api_calls",
            "attempted_requests",
        ), 0)
        items_total = _as_int(_pick(
            flat,
            "items_total",
            "items",
            "offers_total",
            "contexts_total",
            "events_total",
            "rows_total",
            "raw_items",
            "data",
            "rows",
        ), 0)
        matches_with_data = _as_int(_pick(
            flat,
            "matches_with_data",
            "matched_matches",
            "events_matched",
            "matched_events",
            "contexts",
            "matches",
            "with_data",
            "data_matches",
        ), 0)
        fetched = _as_int(_pick(flat, "events_fetched", "fixtures_fetched", "rows_fetched", "fetched", "total"), 0)
        matched = _as_int(_pick(flat, "events_matched", "matched_events", "matched", "matches_with_data"), 0)
        rate_limited = _as_bool(_pick(flat, "rate_limited", "cooldown", "is_rate_limited"))
        api_key_present = _as_bool(_pick(flat, "api_key_present", "has_api_key", "auth_ready"))
        enabled = _as_bool(_pick(flat, "enabled"))
        loaded = _as_bool(_pick(flat, "loaded"))

        if not target_count:
            target_count = _as_int(_pick(flat, "target_count", "targets", "matches_targeted", "requested_matches"), 0)
        if not matches_with_data and matched:
            matches_with_data = matched
        if not items_total and fetched:
            items_total = fetched

        issues: list[str] = []
        if enabled is False or loaded is False:
            issues.append("disabled_or_not_loaded")
        if api_key_present is False:
            issues.append("missing_api_key")
        if rate_limited is True:
            issues.append("rate_limited")
        if target_count > 0 and matches_with_data == 0 and items_total == 0:
            issues.append("zero_yield")
        if items_total > 0 and matches_with_data == 0 and name not in {"match_bootstrap"}:
            issues.append("data_without_match")
        if target_count >= 10 and matches_with_data > 0:
            match_rate = _ratio(matches_with_data, target_count)
            if match_rate is not None and match_rate < 0.25:
                issues.append("low_match_rate")

        provider_rows[name] = {
            "target_count": target_count or None,
            "request_count": request_count or None,
            "items_total": items_total or None,
            "matches_with_data": matches_with_data or None,
            "fetched": fetched or None,
            "matched": matched or None,
            "target_match_rate_pct": _pct(_ratio(matches_with_data, target_count)),
            "fetched_match_rate_pct": _pct(_ratio(matched or matches_with_data, fetched or items_total)),
            "api_key_present": api_key_present,
            "enabled": enabled,
            "loaded": loaded,
            "rate_limited": rate_limited,
            "issues": issues,
        }

    return provider_rows


def _market_anomalies(payload: dict[str, Any], snapshot: list[dict[str, Any]]) -> list[dict[str, Any]]:
    all_rows = list(snapshot)
    for key in ("candidates", "publishable_candidates", "published_candidates"):
        for item in payload.get(key) or []:
            if isinstance(item, dict):
                all_rows.append(item)

    anomalies: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in all_rows:
        family = str(row.get("family") or row.get("market_family") or "").lower()
        selection = str(row.get("selection") or row.get("side") or "").lower()
        point = _as_float(row.get("point") or row.get("line"))
        odds = _as_float(row.get("odds") or row.get("price"))
        if family != "totals" or "over" not in selection or abs(point - 1.5) > 0.05 or odds <= 0:
            continue
        if odds <= 1.65:
            continue
        level = "hard_guard" if odds > 1.85 else "warning"
        key = f"{row.get('match_key')}|{family}|{selection}|{point}|{odds}"
        if key in seen:
            continue
        seen.add(key)
        anomalies.append({
            "level": level,
            "reason": "suspicious_over_1_5_price",
            "match_key": row.get("match_key"),
            "league_name": row.get("league_name"),
            "selection": row.get("selection"),
            "point": row.get("point"),
            "odds": row.get("odds"),
            "bookmaker": row.get("bookmaker"),
            "sources_count": row.get("sources_count"),
            "bookmakers_count": row.get("bookmakers_count"),
        })
    return anomalies


def _build_bottlenecks(
    kpis: dict[str, Any],
    provider_audit: dict[str, dict[str, Any]],
    top_rejections: dict[str, int],
    quality_stops: dict[str, int],
    anomalies: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    bottlenecks: list[dict[str, Any]] = []

    if _as_int(kpis.get("matches_seen")) <= 0:
        bottlenecks.append({"severity": "critical", "code": "no_inventory", "message": "Не собран список матчей."})
    if _as_int(kpis.get("matches_seen")) > 0 and _as_float(kpis.get("offer_coverage_pct")) < 55.0:
        bottlenecks.append({
            "severity": "high",
            "code": "low_offer_coverage",
            "message": "Мало матчей получили линию. Сначала чинить odds/bootstrap и bookmaker coverage.",
        })
    if _as_int(kpis.get("matches_with_offers")) > 0 and _as_float(kpis.get("context_coverage_pct")) < 65.0:
        bottlenecks.append({
            "severity": "medium",
            "code": "low_context_coverage",
            "message": "Контекст собирается слабее, чем линии. Нужна shortlist-first дозагрузка контекста.",
        })
    if _as_int(kpis.get("matches_with_offers")) > 0 and _as_int(kpis.get("candidates_before_quality")) <= 0:
        bottlenecks.append({
            "severity": "high",
            "code": "no_raw_candidates",
            "message": "Линии есть, но CandidateFactory почти ничего не строит.",
        })
    if _as_int(kpis.get("candidates_before_quality")) > 0 and _as_int(kpis.get("candidates_after_quality")) <= 0:
        bottlenecks.append({
            "severity": "high",
            "code": "quality_blocks_all",
            "message": "Кандидаты строятся, но quality layer режет всё.",
        })
    if _as_int(kpis.get("candidates_after_quality")) > 0 and _as_int(kpis.get("candidates_publishable")) <= 0:
        bottlenecks.append({
            "severity": "high",
            "code": "no_publishable",
            "message": "После quality есть кандидаты, но publication policy не пропускает в Telegram.",
        })

    if top_rejections:
        total_rejections = sum(_as_int(value) for value in top_rejections.values())
        reason, count = next(iter(top_rejections.items()))
        if total_rejections > 0 and count / total_rejections >= 0.35:
            bottlenecks.append({
                "severity": "medium",
                "code": "dominant_rejection_reason",
                "message": f"Доминирующая причина отказа: {reason} ({count}/{total_rejections}).",
            })

    if quality_stops:
        total_quality_stops = sum(quality_stops.values())
        reason, count = next(iter(quality_stops.items()))
        if total_quality_stops > 0 and count / total_quality_stops >= 0.35:
            bottlenecks.append({
                "severity": "medium",
                "code": "dominant_quality_stop",
                "message": f"Доминирующий quality stop: {reason} ({count}/{total_quality_stops}).",
            })

    bad_providers = [name for name, row in provider_audit.items() if row.get("issues")]
    if bad_providers:
        bottlenecks.append({
            "severity": "medium",
            "code": "provider_yield_issues",
            "message": "Есть провайдеры с нулевой/низкой отдачей: " + ", ".join(bad_providers[:8]),
        })

    hard_anomalies = [row for row in anomalies if row.get("level") == "hard_guard"]
    if hard_anomalies:
        bottlenecks.append({
            "severity": "critical",
            "code": "market_price_anomaly",
            "message": "Найдены подозрительные цены, например Over 1.5 выше абсолютного лимита.",
        })

    return bottlenecks


def _build_next_actions(bottlenecks: list[dict[str, Any]], provider_audit: dict[str, dict[str, Any]]) -> list[str]:
    codes = {str(item.get("code")) for item in bottlenecks}
    actions: list[str] = []

    if "no_inventory" in codes:
        actions.append("Проверить MATCH_BOOTSTRAP_PROVIDER/DAY_INVENTORY_BOOTSTRAP_PROVIDER и живой ответ primary inventory API.")
    if "low_offer_coverage" in codes:
        actions.append("Снять raw odds payload по odds_api_io и проверить лимиты, bookmaker aliases, page limit и max event pages.")
    if "no_raw_candidates" in codes:
        actions.append("Проверить CandidateFactory: какие market families приходят в merged_offers и почему они не превращаются в candidates.")
    if "quality_blocks_all" in codes:
        actions.append("Разобрать top quality_stops; не ослаблять thresholds до проверки причин отсева.")
    if "no_publishable" in codes:
        actions.append("Проверить publication policy: source_count/bookmaker_count/stake/open risk/seen-candidate fingerprints.")
    if "market_price_anomaly" in codes:
        actions.append("Оставить Over 1.5 hard guard включённым и сравнить exact market signature по всем букмекерам.")

    zero_yield = [
        name
        for name, row in provider_audit.items()
        if "zero_yield" in (row.get("issues") or []) or "data_without_match" in (row.get("issues") or [])
    ]
    if zero_yield:
        actions.append("Для провайдеров " + ", ".join(zero_yield[:6]) + " чинить matching/alias map до увеличения лимитов.")

    if not actions:
        actions.append("Основных блокеров в audit summary нет; следующий шаг — сравнивать CLV и качество выбранных кандидатов.")
    return actions


def _markdown_table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    for row in rows:
        lines.append("| " + " | ".join("" if value is None else str(value) for value in row) + " |")
    return lines


def main() -> int:
    out = Path(".data/exports")
    out.mkdir(parents=True, exist_ok=True)
    payload, source_path = _load_payload()
    summary = dict(payload.get("summary") or {})
    integrity = _read_json(out / "latest-candidate-integrity.json", {})
    quality_report = _read_json(out / "latest-quality-report.json", {})
    qsum = dict((quality_report.get("summary") or {}))

    top_rejections = dict(Counter(summary.get("rejections") or {}).most_common(20))
    quality_stops = _quality_stops(payload)
    candidate_snapshot = _candidate_snapshot(payload)
    provider_audit = _normalise_provider_stats(summary)
    kpis = _runtime_kpis(summary)
    anomalies = _market_anomalies(payload, candidate_snapshot)
    bottlenecks = _build_bottlenecks(kpis, provider_audit, top_rejections, quality_stops, anomalies)
    next_actions = _build_next_actions(bottlenecks, provider_audit)

    run_summary = {
        "created_at": payload.get("created_at") or datetime.now(UTC).isoformat(),
        "source_path": source_path,
        **kpis,
        "top_rejections": top_rejections,
        "quality_stops": quality_stops,
        "integrity": dict((integrity.get("summary") or {})),
        "candidate_snapshot": candidate_snapshot,
        "market_anomalies": anomalies,
        "bottlenecks": bottlenecks,
        "next_actions": next_actions,
        "quality_summary": {
            "settled_binary_bets": qsum.get("settled_binary_bets"),
            "wins": qsum.get("wins"),
            "losses": qsum.get("losses"),
            "roi_pct": qsum.get("roi_pct"),
            "hit_rate_pct": qsum.get("hit_rate_pct"),
            "avg_odds": qsum.get("avg_odds"),
        },
        "provider_audit": provider_audit,
    }

    (out / "latest-run-summary.json").write_text(json.dumps(run_summary, ensure_ascii=False, indent=2), encoding="utf-8")

    md_lines = [
        "# Run bot summary",
        "",
        f"- source_path: `{source_path or 'not_found'}`",
        f"- matches_seen: {run_summary['matches_seen']}",
        f"- matches_with_offers: {run_summary['matches_with_offers']} ({run_summary['offer_coverage_pct']}%)",
        f"- contexts_built: {run_summary['contexts_built']} ({run_summary['context_coverage_pct']}%)",
        f"- candidates_before_quality: {run_summary['candidates_before_quality']}",
        f"- candidates_after_quality: {run_summary['candidates_after_quality']} ({run_summary['quality_pass_rate_pct']}%)",
        f"- candidates_publishable: {run_summary['candidates_publishable']} ({run_summary['publishable_rate_pct']}%)",
        f"- published: {run_summary['published']}",
        f"- integrity_suspicious: {(run_summary['integrity'] or {}).get('suspicious_candidates')}",
        "",
        "## Bottlenecks",
    ]
    for item in bottlenecks:
        md_lines.append(f"- **{item.get('severity')} / {item.get('code')}**: {item.get('message')}")
    if not bottlenecks:
        md_lines.append("- Нет явных bottleneck-сигналов.")

    md_lines.extend(["", "## Next actions"])
    for action in next_actions:
        md_lines.append(f"- {action}")

    md_lines.extend(["", "## Provider audit"])
    provider_rows = [
        [
            name,
            row.get("target_count"),
            row.get("request_count"),
            row.get("items_total"),
            row.get("matches_with_data"),
            row.get("target_match_rate_pct"),
            ",".join(row.get("issues") or []),
        ]
        for name, row in provider_audit.items()
    ]
    md_lines.extend(_markdown_table(
        ["provider", "target", "req", "items", "matched", "match_rate_%", "issues"],
        provider_rows[:40],
    ))

    md_lines.extend(["", "## Quality stops"])
    for key, value in quality_stops.items():
        md_lines.append(f"- {key}: {value}")
    if not quality_stops:
        md_lines.append("- Нет quality_debug decisions.")

    md_lines.extend(["", "## Top rejections"])
    for key, value in top_rejections.items():
        md_lines.append(f"- {key}: {value}")
    if not top_rejections:
        md_lines.append("- Нет rejection summary.")

    if anomalies:
        md_lines.extend(["", "## Market anomalies"])
        for item in anomalies[:20]:
            md_lines.append(
                "- "
                f"{item.get('level')}: {item.get('match_key')} "
                f"{item.get('selection')} {item.get('point')} @ {item.get('odds')} "
                f"book={item.get('bookmaker')}"
            )

    (out / "latest-run-summary.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")
    print(json.dumps(run_summary, ensure_ascii=False, indent=2)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
