from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

UTC = timezone.utc
_PATCH_APPLIED = False


def _to_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
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


def _exports_root(settings: Any) -> Path:
    return Path(str(getattr(settings, "storage_export_dir", ".data/exports") or ".data/exports"))


def _run_progress_path(settings: Any) -> Path:
    return _exports_root(settings) / "latest-run-progress.json"


def _parse_dt(value: Any, settings: Any | None = None):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt


def _summary_snapshot(summary: dict[str, Any]) -> dict[str, Any]:
    source_stats = dict(summary.get("source_stats") or {})
    return {
        "current_time_local": str(summary.get("current_time_local") or summary.get("current_time_utc") or ""),
        "matches_seen": _to_int(summary.get("matches_seen")),
        "matches_with_offers": _to_int(summary.get("matches_with_offers")),
        "contexts_built": _to_int(summary.get("contexts_built")),
        "candidates_before_quality": _to_int(summary.get("candidates_before_quality")),
        "candidates_raw": _to_int(summary.get("candidates_raw")),
        "candidates_publishable": _to_int(summary.get("candidates_publishable")),
        "published_to_telegram": _to_int(summary.get("published_to_telegram") or summary.get("published")),
        "source_stats": source_stats,
    }


def _load_previous_snapshot(settings: Any) -> dict[str, Any]:
    payload = _safe_json_load(_run_progress_path(settings), {})
    return dict(payload) if isinstance(payload, dict) else {}


def _save_snapshot(settings: Any, summary: dict[str, Any]) -> dict[str, Any]:
    payload = _summary_snapshot(dict(summary or {}))
    payload["saved_at"] = datetime.now(UTC).isoformat()
    _safe_json_dump(_run_progress_path(settings), payload)
    return payload


def _signed(n: int) -> str:
    return f"+{n}" if n >= 0 else str(n)


def _delta_line(current: dict[str, Any], previous: dict[str, Any]) -> str | None:
    if not previous:
        return None
    fields = [
        ("матчи", "matches_seen"),
        ("офферы", "matches_with_offers"),
        ("контексты", "contexts_built"),
        ("кандидаты", "candidates_before_quality"),
        ("к публикации", "candidates_publishable"),
    ]
    parts = []
    for label, key in fields:
        delta = _to_int(current.get(key)) - _to_int(previous.get(key))
        parts.append(f"{label} {_signed(delta)}")
    return "Δ с прошлого прогона: " + " | ".join(parts)


def _provider_status_map(summary: dict[str, Any]) -> dict[str, Any]:
    provider_diag = dict(summary.get("provider_diagnostics") or {})
    return dict(provider_diag.get("provider_status") or {})


def _provider_effect_score(stats: dict[str, Any]) -> float:
    if not isinstance(stats, dict):
        return 0.0
    offers = _to_int(stats.get("offers_built"))
    contexts = _to_int(stats.get("contexts_built"))
    fixtures = _to_int(stats.get("fixtures_fetched"))
    cache = _to_int(stats.get("cache_hits"))
    return offers * 5.0 + contexts * 3.0 + fixtures * 1.5 + cache * 0.1


def _good_bad_providers(summary: dict[str, Any]) -> tuple[list[str], list[str]]:
    source_stats = dict(summary.get("source_stats") or {})
    status_map = _provider_status_map(summary)

    good_rows: list[tuple[float, str, str]] = []
    bad_rows: list[str] = []

    for name, raw_stats in source_stats.items():
        stats = dict(raw_stats or {})
        status = dict(status_map.get(name) or {})
        score = _provider_effect_score(stats)
        errors = _to_int(stats.get("response_errors"))
        contexts = _to_int(stats.get("contexts_built"))
        offers = _to_int(stats.get("offers_built"))
        fixtures = _to_int(stats.get("fixtures_fetched"))
        target = _to_int((dict(summary.get("provider_context_targets") or {})).get(name))
        req = _to_int(stats.get("requests"))
        if score > 0 and errors == 0 and not status.get("rate_limited") and not status.get("runtime_error") and status.get("loaded", True) is not False:
            text_bits = []
            if offers:
                text_bits.append(f"офферы {offers}")
            if contexts:
                text_bits.append(f"контекст {contexts}")
            if fixtures and not contexts:
                text_bits.append(f"fixtures {fixtures}")
            good_rows.append((score, name, " | ".join(text_bits) or "данные есть"))
        problem_bits = []
        if status.get("loaded") is False:
            problem_bits.append("not-loaded")
        if status.get("rate_limited"):
            problem_bits.append("rate-limited")
        if status.get("runtime_error"):
            problem_bits.append(str(status.get("runtime_error"))[:60])
        if errors > 0:
            problem_bits.append(f"errors {errors}")
        if req > 0 and score <= 0 and target > 0:
            problem_bits.append("запросы были, результата нет")
        if problem_bits:
            bad_rows.append(f"{name}: " + ", ".join(problem_bits))

    good_rows.sort(key=lambda item: item[0], reverse=True)
    return [f"{name}: {desc}" for _, name, desc in good_rows[:4]], bad_rows[:4]


def _current_day_predictions(settings: Any) -> list[dict[str, Any]]:
    payload = _safe_json_load(_exports_root(settings) / "latest-day-predictions.json", {})
    items = [dict(item) for item in (payload.get("items") or []) if isinstance(item, dict)]
    now = datetime.now(UTC)
    keep: list[dict[str, Any]] = []
    for item in items:
        dt = _parse_dt(item.get("commence_time"), settings)
        if dt is not None and dt <= now - timedelta(minutes=5):
            continue
        keep.append(item)
    keep.sort(
        key=lambda row: (
            _to_float(row.get("publication_score")),
            _to_float(row.get("confidence")),
            _to_float(row.get("ev_pct")),
            _to_float(row.get("edge_pct")),
        ),
        reverse=True,
    )
    return keep


def _render_day_predictions(settings: Any, max_items: int = 10) -> str:
    items = _current_day_predictions(settings)
    if not items:
        return "📚 Текущий пул прогнозов дня\n\nПул пока пуст."
    lines = [
        f"📚 Текущий пул прогнозов дня ({getattr(settings, 'app_timezone', 'Europe/Moscow')})",
        f"Собрано вариантов: {len(items)} | Отсортировано: от лучшего к худшему",
    ]
    for idx, item in enumerate(items[:max_items], start=1):
        selection = str(item.get("selection_display") or item.get("selection") or "").strip()
        kickoff = ""
        dt = _parse_dt(item.get("commence_time"), settings)
        if dt is not None:
            try:
                kickoff = dt.astimezone(getattr(settings, "tzinfo", UTC)).strftime("%d.%m %H:%M")
            except Exception:
                kickoff = str(item.get("kickoff_local") or "")
        else:
            kickoff = str(item.get("kickoff_local") or "")
        lines.append(
            f"{idx}. {item.get('home_team')} — {item.get('away_team')}\n"
            f"🎯 {selection} @ {_to_float(item.get('odds')):.2f}\n"
            f"🏆 {item.get('league_name')}\n"
            f"🕒 {kickoff}\n"
            f"📊 score {_to_float(item.get('publication_score')):.1f} | conf {_to_float(item.get('confidence')):.1f}% | EV {_to_float(item.get('ev_pct')):+.2f}% | edge {_to_float(item.get('edge_pct')):+.2f} п.п."
        )
    return "\n\n".join(lines)


def _render_progress_report(publisher: Any, summary: dict[str, Any]) -> str:
    settings = publisher.settings
    summary = dict(summary or {})
    current = _summary_snapshot(summary)
    previous = _load_previous_snapshot(settings)
    filtering = dict(summary.get("filtering") or {})
    bankroll = dict(summary.get("bankroll") or {})
    rejections = dict(summary.get("rejections") or {})

    lines = [
        "🧭 Прогон дня — накопление покрытия",
        f"🕒 Запуск: {current.get('current_time_local') or 'н/д'}",
        f"📅 Окно: {filtering.get('publish_window_hours', getattr(settings, 'publish_window_hours', 'н/д'))} ч | Мин. запас: {filtering.get('min_kickoff_lead_minutes', getattr(settings, 'min_kickoff_lead_minutes', 'н/д'))} мин",
        f"⚽ Матчи: {_to_int(current.get('matches_seen'))} | С офферами: {_to_int(current.get('matches_with_offers'))} | Контексты: {_to_int(current.get('contexts_built'))}",
        f"🧠 Кандидаты: до quality {_to_int(current.get('candidates_before_quality'))} | после quality {_to_int(current.get('candidates_raw'))} | к публикации {_to_int(current.get('candidates_publishable'))} | опубликовано {_to_int(current.get('published_to_telegram'))}",
    ]
    delta = _delta_line(current, previous)
    if delta:
        lines.append(delta)
    if bankroll:
        lines.append(
            f"💼 Банк: {publisher._format_money(_to_float(bankroll.get('current_balance')), bankroll_summary=bankroll)} | "
            f"Открытый риск: {publisher._format_money(_to_float(bankroll.get('open_exposure')), bankroll_summary=bankroll)} | "
            f"ROI: {_to_float(bankroll.get('roi_pct')):+.2f}%"
        )

    top_rejections = [(str(k), _to_int(v)) for k, v in rejections.items() if _to_int(v) > 0]
    top_rejections.sort(key=lambda item: item[1], reverse=True)
    if top_rejections:
        lines.append("🚫 Что мешает сейчас:")
        for key, count in top_rejections[:6]:
            lines.append(f"• {key.replace('_', ' ')} — {count}")

    good, bad = _good_bad_providers(summary)
    if good:
        lines.append("✅ Что отработало хорошо:")
        for item in good:
            lines.append(f"• {item}")
    if bad:
        lines.append("⚠️ Что отработало плохо:")
        for item in bad:
            lines.append(f"• {item}")

    lines.append(
        "ℹ️ Логика дня: бот копит покрытие и shortlist, поэтому в каждом следующем прогоне число матчей/офферов/контекстов может расти, а итоговый список лучших сигналов пересобирается."
    )
    return "\n".join(lines)


def _patch_telegram() -> None:
    from app.services.telegram import TelegramPublisher

    if getattr(TelegramPublisher, "_runtime_day_cycle_fix_applied", False):
        return

    original_publish = TelegramPublisher.publish

    async def patched_publish(self, bets, bankroll_summary=None):
        # suppress duplicate day-predictions message from older runtime patches;
        # current sorted pool is sent from publish_run_report on every run.
        previous = os.environ.get("DAY_PREDICTIONS_REPORT_ENABLED")
        os.environ["DAY_PREDICTIONS_REPORT_ENABLED"] = "false"
        try:
            return await original_publish(self, bets, bankroll_summary=bankroll_summary)
        finally:
            if previous is None:
                os.environ.pop("DAY_PREDICTIONS_REPORT_ENABLED", None)
            else:
                os.environ["DAY_PREDICTIONS_REPORT_ENABLED"] = previous

    async def patched_publish_run_report(self, summary):
        sent = 0
        parts: list[str] = []
        report_text = _render_progress_report(self, summary)
        if report_text:
            extra_sent, extra_parts = await self._send_message(report_text)
            sent += extra_sent
            parts.extend(extra_parts)

        shortlist_text = _render_day_predictions(self.settings, max_items=max(5, int(os.getenv("DAY_CYCLE_SHORTLIST_MAX_ITEMS", "10"))))
        if shortlist_text:
            extra_sent, extra_parts = await self._send_message(shortlist_text)
            sent += extra_sent
            parts.extend(extra_parts)

        _save_snapshot(self.settings, summary)
        return sent, parts

    TelegramPublisher.publish = patched_publish
    TelegramPublisher.publish_run_report = patched_publish_run_report
    TelegramPublisher._runtime_day_cycle_fix_applied = True


def _apply() -> None:
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return
    try:
        _patch_telegram()
    except Exception:
        return
    _PATCH_APPLIED = True


_apply()
