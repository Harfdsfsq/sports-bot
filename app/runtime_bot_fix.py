
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
_PATCH_APPLIED = False


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(value)
    except Exception:
        return default


def _safe_json_load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def _safe_json_dump(path: Path, payload: Any) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _safe_csv_dump(path: Path, rows: list[dict[str, Any]]) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    headers: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in headers:
                headers.append(key)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=headers)
        writer.writeheader()
        for row in rows:
            writer.writerow(
                {
                    key: (
                        json.dumps(value, ensure_ascii=False, sort_keys=True)
                        if isinstance(value, (dict, list))
                        else value
                    )
                    for key, value in row.items()
                }
            )
    return path


def _exports_root(settings: Any) -> Path:
    return Path(str(getattr(settings, "storage_export_dir", ".data/exports") or ".data/exports"))


def _today_local(settings: Any) -> str:
    tzinfo = getattr(settings, "tzinfo", UTC)
    return datetime.now(UTC).astimezone(tzinfo).date().isoformat()


def _day_predictions_store_path(settings: Any) -> Path:
    root = _exports_root(settings)
    return root / "day-predictions" / f"{_today_local(settings)}.json"


def _latest_day_predictions_json(settings: Any) -> Path:
    return _exports_root(settings) / "latest-day-predictions.json"


def _latest_day_predictions_csv(settings: Any) -> Path:
    return _exports_root(settings) / "latest-day-predictions.csv"


def _latest_day_predictions_text(settings: Any) -> Path:
    return _exports_root(settings) / "latest-day-predictions-report.txt"


def _latest_day_predictions_compare_json(settings: Any) -> Path:
    return _exports_root(settings) / "latest-day-predictions-compare.json"


def _format_local_kickoff(settings: Any, value: Any) -> str:
    try:
        if hasattr(value, "astimezone"):
            dt = value.astimezone(getattr(settings, "tzinfo", UTC))
        else:
            text = str(value or "").strip()
            if not text:
                return ""
            dt = datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(getattr(settings, "tzinfo", UTC))
        return dt.strftime("%d.%m %H:%M")
    except Exception:
        return str(value or "")


def _short_selection(family: str, selection: str, point: Any = None, team_side: str | None = None) -> str:
    family_key = str(family or "").strip().lower()
    sel = str(selection or "").strip()
    low = sel.lower()
    if family_key == "h2h":
        if low in {"п1", "1", "home"}:
            return "П1"
        if low in {"п2", "2", "away"}:
            return "П2"
        if "нич" in low or low in {"x", "draw"}:
            return "Ничья"
        return sel
    if family_key == "totals":
        if "больше" in low or "over" in low or "тб" in low:
            return f"ТБ({float(point):g})" if point not in (None, "") else "ТБ"
        if "меньше" in low or "under" in low or "тм" in low:
            return f"ТМ({float(point):g})" if point not in (None, "") else "ТМ"
    if family_key in {"spreads", "dnb"}:
        side = str(team_side or "").strip().lower()
        code = "1" if side == "home" else "2" if side == "away" else ""
        if code:
            try:
                point_text = f"{float(point):g}" if point not in (None, "") else "0"
            except Exception:
                point_text = str(point or "0")
            if not point_text.startswith("-") and point_text != "0":
                point_text = f"+{point_text}"
            return f"Ф{code}({point_text})"
    if family_key == "btts":
        if "да" in low or "yes" in low:
            return "ОЗ: Да"
        if "нет" in low or "no" in low:
            return "ОЗ: Нет"
    return sel


def _row_fingerprint(row: dict[str, Any]) -> str:
    fp = str(row.get("fingerprint") or "").strip()
    if fp:
        return fp
    parts = [
        str(row.get("match_key") or ""),
        str(row.get("family") or ""),
        str(row.get("selection_key") or row.get("selection") or ""),
        str(row.get("point") or ""),
        str(row.get("commence_time") or ""),
    ]
    return "|".join(parts)


def _normalize_prediction_row(settings: Any, row: Any, *, source: str) -> dict[str, Any] | None:
    if row is None:
        return None
    if hasattr(row, "__dict__"):
        payload = dict(getattr(row, "__dict__", {}) or {})
    elif isinstance(row, dict):
        payload = dict(row)
    else:
        return None
    fingerprint = _row_fingerprint(payload)
    if not fingerprint:
        return None
    publication_score = _to_float(payload.get("publication_score"))
    confidence = _to_float(payload.get("confidence"))
    ev_pct = _to_float(payload.get("ev_pct"))
    edge_pct = _to_float(payload.get("edge_pct"))
    odds = _to_float(payload.get("odds"))
    normalized = {
        "fingerprint": fingerprint,
        "match_key": str(payload.get("match_key") or ""),
        "league_name": str(payload.get("league_name") or ""),
        "home_team": str(payload.get("home_team") or ""),
        "away_team": str(payload.get("away_team") or ""),
        "family": str(payload.get("family") or ""),
        "selection": str(payload.get("selection") or ""),
        "selection_key": str(payload.get("selection_key") or ""),
        "point": payload.get("point"),
        "team_side": str(payload.get("team_side") or ""),
        "odds": round(odds, 3) if odds else 0.0,
        "confidence": round(confidence, 2),
        "ev_pct": round(ev_pct, 3),
        "edge_pct": round(edge_pct, 3),
        "publication_score": round(publication_score, 3),
        "books_count": _to_int(payload.get("books_count")),
        "sources_count": _to_int(payload.get("sources_count")),
        "model_mode": str(payload.get("model_mode") or ""),
        "commence_time": str(payload.get("commence_time") or ""),
        "kickoff_local": _format_local_kickoff(settings, payload.get("commence_time")),
        "source": source,
        "selection_display": _short_selection(
            str(payload.get("family") or ""),
            str(payload.get("selection") or ""),
            payload.get("point"),
            str(payload.get("team_side") or ""),
        ),
        "updated_at": datetime.now(UTC).isoformat(),
    }
    return normalized


def _sort_prediction_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    def key(item: dict[str, Any]) -> tuple[float, float, float, float, str]:
        return (
            _to_float(item.get("publication_score")),
            _to_float(item.get("confidence")),
            _to_float(item.get("ev_pct")),
            _to_float(item.get("edge_pct")),
            str(item.get("commence_time") or ""),
        )
    return sorted(rows, key=key, reverse=True)


def _load_day_predictions(settings: Any) -> dict[str, Any]:
    path = _day_predictions_store_path(settings)
    payload = _safe_json_load(path, {})
    if not isinstance(payload, dict):
        payload = {}
    if str(payload.get("date") or "") != _today_local(settings):
        payload = {"date": _today_local(settings), "items": []}
    items = [dict(item) for item in (payload.get("items") or []) if isinstance(item, dict)]
    payload["date"] = _today_local(settings)
    payload["items"] = _sort_prediction_rows(items)
    return payload


def _save_day_predictions(settings: Any, payload: dict[str, Any], previous_items: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    items = _sort_prediction_rows([dict(item) for item in (payload.get("items") or []) if isinstance(item, dict)])
    payload = {
        "date": _today_local(settings),
        "updated_at": datetime.now(UTC).isoformat(),
        "count": len(items),
        "items": items,
    }
    _safe_json_dump(_day_predictions_store_path(settings), payload)
    _safe_json_dump(_latest_day_predictions_json(settings), payload)
    _safe_csv_dump(_latest_day_predictions_csv(settings), items)
    previous_items = [dict(item) for item in (previous_items or []) if isinstance(item, dict)]
    previous_map = {str(item.get("fingerprint") or ""): dict(item) for item in previous_items if str(item.get("fingerprint") or "")}
    current_map = {str(item.get("fingerprint") or ""): dict(item) for item in items if str(item.get("fingerprint") or "")}
    added = [item for fp, item in current_map.items() if fp not in previous_map]
    removed = [item for fp, item in previous_map.items() if fp not in current_map]
    changed: list[dict[str, Any]] = []
    for fp, item in current_map.items():
        prev = previous_map.get(fp)
        if not prev:
            continue
        if (
            _to_float(prev.get("publication_score")) != _to_float(item.get("publication_score"))
            or _to_float(prev.get("confidence")) != _to_float(item.get("confidence"))
            or _to_float(prev.get("ev_pct")) != _to_float(item.get("ev_pct"))
            or _to_float(prev.get("edge_pct")) != _to_float(item.get("edge_pct"))
        ):
            changed.append({
                "fingerprint": fp,
                "before": prev,
                "after": item,
            })
    compare = {
        "date": payload["date"],
        "updated_at": payload["updated_at"],
        "count": len(items),
        "added": added[:20],
        "removed": removed[:20],
        "changed": changed[:20],
    }
    _safe_json_dump(_latest_day_predictions_compare_json(settings), compare)
    return payload


def _merge_day_predictions(settings: Any, rows: list[Any], *, source: str) -> dict[str, Any]:
    current = _load_day_predictions(settings)
    previous_items = [dict(item) for item in (current.get("items") or []) if isinstance(item, dict)]
    merged: dict[str, dict[str, Any]] = {
        str(item.get("fingerprint") or ""): dict(item)
        for item in previous_items
        if str(item.get("fingerprint") or "")
    }
    for row in rows:
        normalized = _normalize_prediction_row(settings, row, source=source)
        if not normalized:
            continue
        fp = str(normalized.get("fingerprint") or "")
        existing = merged.get(fp)
        if existing is None:
            merged[fp] = normalized
            continue
        existing_score = (
            _to_float(existing.get("publication_score")),
            _to_float(existing.get("confidence")),
            _to_float(existing.get("ev_pct")),
            _to_float(existing.get("edge_pct")),
        )
        new_score = (
            _to_float(normalized.get("publication_score")),
            _to_float(normalized.get("confidence")),
            _to_float(normalized.get("ev_pct")),
            _to_float(normalized.get("edge_pct")),
        )
        if new_score >= existing_score:
            normalized["first_seen_at"] = str(existing.get("first_seen_at") or existing.get("updated_at") or "")
            merged[fp] = {**existing, **normalized}
        else:
            existing["updated_at"] = datetime.now(UTC).isoformat()
            merged[fp] = existing
    payload = {"date": _today_local(settings), "items": list(merged.values())}
    return _save_day_predictions(settings, payload, previous_items=previous_items)


def _load_latest_picks(settings: Any) -> list[dict[str, Any]]:
    path = _exports_root(settings) / "latest-picks.json"
    rows = _safe_json_load(path, [])
    return [dict(item) for item in rows if isinstance(item, dict)]


def _render_day_predictions_message(publisher: Any, settings: Any, payload: dict[str, Any]) -> str | None:
    items = [dict(item) for item in (payload.get("items") or []) if isinstance(item, dict)]
    if not items:
        return None
    max_items = max(3, int(os.getenv("DAY_PREDICTIONS_REPORT_MAX_ITEMS") or getattr(settings, "day_predictions_report_max_items", 12) or 12))
    label = str(getattr(settings, "app_timezone", "UTC") or "UTC")
    lines = [
        f"📚 Прогнозы за день ({label})",
        f"Собрано вариантов: {len(items)} | Отсортировано: от лучшего к худшему",
    ]
    for idx, item in enumerate(items[:max_items], start=1):
        lines.append(
            f"{idx}. {item.get('home_team')} — {item.get('away_team')}\n"
            f"🎯 {item.get('selection_display')} @ {float(item.get('odds') or 0.0):.2f}\n"
            f"🏆 {item.get('league_name')}\n"
            f"🕒 {item.get('kickoff_local')}\n"
            f"📊 score {float(item.get('publication_score') or 0.0):.1f} | conf {float(item.get('confidence') or 0.0):.1f}% | EV {float(item.get('ev_pct') or 0.0):+.2f}% | edge {float(item.get('edge_pct') or 0.0):+.2f} п.п."
        )
    text = "\n\n".join(lines)
    _latest_day_predictions_text(settings).parent.mkdir(parents=True, exist_ok=True)
    _latest_day_predictions_text(settings).write_text(text, encoding="utf-8")
    return text


def _provider_line(name: str, stats: dict[str, Any]) -> str | None:
    if not isinstance(stats, dict):
        return None
    parts: list[str] = []
    contexts = _to_int(stats.get("contexts_built"), -1)
    fixtures = _to_int(stats.get("fixtures_fetched"), -1)
    requests = _to_int(stats.get("requests"), -1)
    errors = _to_int(stats.get("response_errors"), -1)
    cache_hits = _to_int(stats.get("cache_hits"), -1)
    cache_empty_hits = _to_int(stats.get("cache_empty_hits"), -1)
    offers = _to_int(stats.get("offers_built"), -1)
    if offers >= 0:
        parts.append(f"офферы {offers}")
    if contexts >= 0:
        parts.append(f"контекст {contexts}")
    if fixtures >= 0 and contexts < 0:
        parts.append(f"fixtures {fixtures}")
    if requests >= 0:
        parts.append(f"req {requests}")
    if errors > 0:
        parts.append(f"errors {errors}")
    if cache_hits > 0:
        parts.append(f"cache {cache_hits}")
    if cache_empty_hits > 0:
        parts.append(f"empty-cache {cache_empty_hits}")
    if not parts:
        return None
    return f"• {name}: " + " | ".join(parts)


def _render_full_run_report(publisher: Any, summary: dict[str, Any]) -> str | None:
    settings = publisher.settings
    summary = dict(summary or {})
    filtering = dict(summary.get("filtering") or {})
    source_stats = dict(summary.get("source_stats") or {})
    provider_diag = dict(summary.get("provider_diagnostics") or {})
    context_targets = dict(summary.get("provider_context_targets") or {})
    rejections = dict(summary.get("rejections") or {})
    bankroll = dict(summary.get("bankroll") or {})
    published = _to_int(summary.get("published_to_telegram") or summary.get("published"))
    matches_seen = _to_int(summary.get("matches_seen"))
    matches_with_offers = _to_int(summary.get("matches_with_offers"))
    contexts_built = _to_int(summary.get("contexts_built"))
    candidates_before_quality = _to_int(summary.get("candidates_before_quality"))
    candidates_after_quality = _to_int(summary.get("candidates_raw"))
    candidates_publishable = _to_int(summary.get("candidates_publishable"))
    header = [
        "🧾 Полный отчёт по запуску бота",
        f"🕒 Запуск: {summary.get('current_time_local') or summary.get('current_time_utc') or 'н/д'}",
        f"📅 Окно: {filtering.get('publish_window_hours', getattr(settings, 'publish_window_hours', 'н/д'))} ч | Мин. запас: {filtering.get('min_kickoff_lead_minutes', getattr(settings, 'min_kickoff_lead_minutes', 'н/д'))} мин",
        f"⚽ Матчи: {matches_seen} | С офферами: {matches_with_offers} | Контексты: {contexts_built}",
        f"🧠 Кандидаты: до quality {candidates_before_quality} | после quality {candidates_after_quality} | к публикации {candidates_publishable} | опубликовано {published}",
    ]
    if bankroll:
        header.append(
            f"💼 Банк: {publisher._format_money(float(bankroll.get('current_balance') or 0.0), bankroll_summary=bankroll)} | "
            f"Открытый риск: {publisher._format_money(float(bankroll.get('open_exposure') or 0.0), bankroll_summary=bankroll)} | "
            f"ROI: {float(bankroll.get('roi_pct') or 0.0):+.2f}%"
        )

    top_rejections = [(str(k), _to_int(v)) for k, v in rejections.items() if _to_int(v) > 0]
    top_rejections.sort(key=lambda item: item[1], reverse=True)
    reject_lines = ["🚫 Главные стопоры:"] if top_rejections else []
    for key, value in top_rejections[:8]:
        reject_lines.append(f"• {key.replace('_', ' ')} — {value}")

    provider_lines = ["📡 Покрытие по API:"]
    priority_order = [
        "odds_api_io", "oddspapi", "allsportsapi", "sstats", "bzzoiro", "api_football",
        "espn", "thesportsdb", "football_data", "openligadb", "openfootball", "newsapi", "gnews", "self_history"
    ]
    for name in priority_order:
        line = _provider_line(name, dict(source_stats.get(name) or {}))
        if line:
            target = context_targets.get(name)
            if target not in (None, "", 0):
                line += f" | target {target}"
            provider_lines.append(line)

    provider_status = dict(provider_diag.get("provider_status") or {})
    degraded_lines = []
    for name, payload in provider_status.items():
        if not isinstance(payload, dict):
            continue
        if payload.get("runtime_error") or payload.get("rate_limited") or payload.get("loaded") is False:
            chunks = []
            if payload.get("loaded") is False:
                chunks.append("not-loaded")
            if payload.get("rate_limited"):
                chunks.append("rate-limited")
            if payload.get("runtime_error"):
                chunks.append(str(payload.get("runtime_error"))[:90])
            degraded_lines.append(f"• {name}: " + " | ".join(chunks))
    text_parts = ["\n".join(header)]
    if reject_lines:
        text_parts.append("\n".join(reject_lines))
    if len(provider_lines) > 1:
        text_parts.append("\n".join(provider_lines))
    if degraded_lines:
        text_parts.append("⚠️ Проблемы провайдеров:\n" + "\n".join(degraded_lines[:6]))

    latest_day = _load_day_predictions(settings)
    items = [dict(item) for item in (latest_day.get("items") or []) if isinstance(item, dict)]
    if items:
        best = items[0]
        text_parts.append(
            "📚 Накоплено за день:\n"
            f"• прогнозов в пуле: {len(items)}\n"
            f"• лучший сейчас: {best.get('home_team')} — {best.get('away_team')} | {best.get('selection_display')} @ {float(best.get('odds') or 0.0):.2f}\n"
            f"• score {float(best.get('publication_score') or 0.0):.1f} | conf {float(best.get('confidence') or 0.0):.1f}% | EV {float(best.get('ev_pct') or 0.0):+.2f}%"
        )
    return "\n\n".join(part for part in text_parts if part.strip())


def _patch_telegram() -> None:
    from app.services.telegram import TelegramPublisher

    if getattr(TelegramPublisher, "_day_report_patch_applied", False):
        return

    original_publish = TelegramPublisher.publish
    original_publish_run_report = TelegramPublisher.publish_run_report

    async def publish(self, bets, bankroll_summary=None):
        sent, parts = await original_publish(self, bets, bankroll_summary=bankroll_summary)
        enabled = str(os.getenv("DAY_PREDICTIONS_REPORT_ENABLED") or getattr(self.settings, "day_predictions_report_enabled", "true")).strip().lower() != "false"
        if not enabled:
            return sent, parts
        rows = list(bets or [])
        if rows:
            payload = _merge_day_predictions(self.settings, rows, source="published")
            message = _render_day_predictions_message(self, self.settings, payload)
            if message:
                extra_sent, extra_parts = await self._send_message(message)
                sent += extra_sent
                parts = list(parts) + list(extra_parts)
        return sent, parts

    async def publish_run_report(self, summary):
        settings = self.settings
        always = str(os.getenv("RUN_REPORT_ALWAYS") or getattr(settings, "run_report_always", "true")).strip().lower() != "false"
        if always:
            message = _render_full_run_report(self, summary)
            sent, parts = (0, [])
            if message:
                sent, parts = await self._send_message(message)
        else:
            sent, parts = await original_publish_run_report(self, summary)

        enabled = str(os.getenv("DAY_PREDICTIONS_REPORT_ENABLED") or getattr(settings, "day_predictions_report_enabled", "true")).strip().lower() != "false"
        if not enabled:
            return sent, parts
        latest_rows = _load_latest_picks(settings)
        if latest_rows:
            payload = _merge_day_predictions(settings, latest_rows, source="latest_picks")
        else:
            payload = _load_day_predictions(settings)
        message = _render_day_predictions_message(self, settings, payload)
        if message:
            extra_sent, extra_parts = await self._send_message(message)
            sent += extra_sent
            parts = list(parts) + list(extra_parts)
        return sent, parts

    TelegramPublisher.publish = publish
    TelegramPublisher.publish_run_report = publish_run_report
    TelegramPublisher.render_run_report = _render_full_run_report
    TelegramPublisher._day_report_patch_applied = True


def _apply() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    _patch_telegram()
    _PATCH_APPLIED = True


_apply()
