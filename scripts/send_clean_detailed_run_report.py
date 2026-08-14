from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib import parse, request

UTC = timezone.utc
TXT_PATH = Path(".data/exports/latest-detailed-run-report.txt")
JSON_PATH = Path(".data/exports/latest-detailed-run-report.json")
OUT_PATH = Path(".data/exports/latest-detailed-run-report-cleaned.txt")
STATE_PATH = Path(".data/detailed-run-report-sent.json")
DEBUG_PATH = Path(".logs/debug-last-run.json")
HEALTH_PATH = Path(".data/exports/latest-api-health-run.json")
BUDGET_PATH = Path(".data/exports/latest-provider-request-budget.json")
REMOVED_PROVIDERS = {"api_football", "bookies_api", "oddspapi"}
HEALTH_ALIASES = {"odds_api_io_events": "odds_api_io", "sharpapi": "sharpapi_configured_base"}
API_ORDER = [
    "odds_api_io", "allsportsapi", "bzzoiro", "sstats", "football_data", "thesportsdb",
    "weather", "weatherapi", "openweathermap", "meteostat", "sportlogic", "openfootball",
    "futrixmetrics", "highlightly", "newsapi", "currents", "gnews", "newsdata", "guardian",
    "sharpapi_configured_base", "oddsfeed", "sportsbook_api", "sportapi", "freeapilivefootball",
]


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on", "force"}


def env_int(name: str, default: int) -> int:
    try:
        raw = os.getenv(name)
        return int(float(str(raw))) if raw not in (None, "") else default
    except Exception:
        return default


def load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def ensure_health_report() -> dict[str, Any]:
    if HEALTH_PATH.exists():
        return {"ran": False, "reason": "already_exists", "path": str(HEALTH_PATH)}
    script = Path("scripts/api_health_run.py")
    if not script.exists():
        return {"ran": False, "reason": "script_missing", "path": str(script)}
    try:
        completed = subprocess.run(
            [sys.executable, str(script), "--mode", os.getenv("API_HEALTH_MODE", "quick"), "--output-dir", ".data/exports"],
            check=False,
            timeout=env_int("API_HEALTH_CLEAN_REPORT_TIMEOUT_SECONDS", 60),
            capture_output=True,
            text=True,
        )
        return {"ran": True, "returncode": completed.returncode, "path_exists": HEALTH_PATH.exists(), "stdout_preview": (completed.stdout or "")[:800], "stderr_preview": (completed.stderr or "")[:800]}
    except Exception as exc:
        return {"ran": False, "reason": f"{type(exc).__name__}: {exc}", "path_exists": HEALTH_PATH.exists()}


def remove_deleted_provider_lines(text: str) -> str:
    out: list[str] = []
    for line in text.splitlines():
        stripped = line.strip().lower()
        if any(stripped.startswith(f"• {provider}:") for provider in REMOVED_PROVIDERS):
            continue
        out.append(line)
    return "\n".join(out).strip() + "\n"


def candidate_section(text: str) -> str:
    for marker in ("⚠️ Пограничные кандидаты", "⚠️ Остальные пограничные кандидаты"):
        idx = text.find(marker)
        if idx >= 0:
            tail = text[idx:]
            for stop in ("\n🧠 ", "\n🧩 ", "\n🔌 ", "\n📌 "):
                j = tail.find(stop)
                if j > 0:
                    return tail[:j]
            return tail
    return ""


def count_reason(text: str, reason: str) -> int:
    return text.lower().count(reason.lower())


def build_conclusion(text: str) -> list[str]:
    lowered = text.lower(); section = candidate_section(text)
    duplicate_count = count_reason(section, "матч уже был опубликован ранее") + count_reason(section, "такой прогноз уже отправлялся ранее")
    confirm_count = count_reason(section, "confirmation sources below min:1/2")
    btts_count = count_reason(section, "BTTS-модель слишком расходится")
    spreads_count = count_reason(section, "закрытая семья рынка: форы")
    selected = "✅ Опубликовано" in text or "прогноз опубликован" in lowered
    lines = ["📌 Вывод"]
    if selected: lines.append("• Контролируемый прогноз выбран; он должен уходить отдельным Telegram-сообщением при live-publish режиме.")
    if duplicate_count: lines.append("• Часть сильных value-кандидатов не дублировалась, потому что матч уже был опубликован ранее.")
    if confirm_count: lines.append("• Лучшие новые value-кандидаты сейчас упёрлись во второй подтверждающий источник: confirmation sources 1/2.")
    if spreads_count: lines.append("• Форы закрыты guard’ом до полной проверки handicap-parser.")
    if btts_count: lines.append("• BTTS-кандидат отклонён из-за расхождения модели с xG.")
    if not any([selected, duplicate_count, confirm_count, spreads_count, btts_count]): lines.append("• Новых publishable-прогнозов нет: кандидаты не прошли финальные guards.")
    lines.append("• Guards не ослаблялись: публикация разрешается только для нового кандидата с достаточным подтверждением и стабильной value.")
    return lines


def replace_conclusion(text: str) -> str:
    conclusion = "\n".join(build_conclusion(text)); marker = "📌 Вывод"; idx = text.find(marker)
    if idx < 0: return text.rstrip() + "\n\n" + conclusion + "\n"
    return text[:idx].rstrip() + "\n\n" + conclusion + "\n"


def extract_provider_lines(text: str) -> list[str]:
    marker = "📡 Источники / фактическая работа"; idx = text.find(marker)
    if idx < 0: return []
    lines: list[str] = []
    for line in text[idx:].splitlines()[1:]:
        if not line.startswith("• "):
            if line.strip().startswith(("📈", "🚫", "⚠️", "✅", "🧠", "🧩", "🔌", "📌")): break
            continue
        stripped = line.strip().lower()
        if any(stripped.startswith(f"• {provider}:") for provider in REMOVED_PROVIDERS): continue
        lines.append(line.strip())
    return lines


def provider_name(line: str) -> str:
    return line.split(":", 1)[0].replace("•", "").strip()


def quota_lines_from_text(text: str) -> list[str]:
    marker = "🔌 API / квоты последнего run"; idx = text.find(marker); out: list[str] = []
    if idx < 0: return out
    for line in text[idx:].splitlines()[1:]:
        if not line.startswith("• "):
            if line.strip().startswith("📌"): break
            continue
        stripped = line.strip().lower()
        if any(stripped.startswith(f"• {provider}:") for provider in REMOVED_PROVIDERS): continue
        out.append(line.strip())
    return out


def quota_from_budget_json() -> dict[str, str]:
    payload = load_json(BUDGET_PATH, {}); decisions = payload.get("decisions") if isinstance(payload, dict) else []; out: dict[str, str] = {}
    if isinstance(decisions, list):
        for row in decisions:
            if not isinstance(row, dict): continue
            name = str(row.get("provider") or "").strip()
            if not name or name.lower() in REMOVED_PROVIDERS: continue
            parts = [f"grant {row.get('grant', 0)}", f"reason {row.get('reason') or 'unknown'}"]
            status = row.get("status")
            if status: parts.append(f"status {status}")
            out[name] = ", ".join(parts)
    return out


def provider_meaning(name: str) -> str:
    meanings = {"odds_api_io":"главный источник линий и списка матчей по целевым букмекерам","allsportsapi":"fixture/secondary odds probe; помогает расширять inventory и проверять доп. линии","bzzoiro":"контекст/прогнозы; независимое подтверждение, но матчинг пока даёт мало совпадений","sstats":"форма/статистика команд; основной near-window context provider","football_data":"fixture/league calendar; добор матчей и алиасы","thesportsdb":"fixture/алиасы команд и лиг","weather":"агрегированный погодный overlay: WeatherAPI первым, OpenWeatherMap fallback","weatherapi":"основной погодный API","openweathermap":"fallback погоды; используется если WeatherAPI не дал payload","meteostat":"резервный weather source; сейчас выключен, потому что weatherapi first","sportlogic":"probe/context; games получает, но odds/parser ещё не подтверждён","openfootball":"публичный календарь/алиасы","futrixmetrics":"доп. context provider; сейчас low_recent_yield","highlightly":"health-probe only: endpoint/key проверяется, в прогнозирование ещё не интегрирован","newsapi":"news context rotation; не нужен каждый run","currents":"news context rotation","gnews":"news context rotation; не нужен каждый run","newsdata":"news context health-probe/rotation","guardian":"news context health-probe/rotation","sharpapi_configured_base":"text/utility probe; не источник спортивных данных для модели","oddsfeed":"RapidAPI odds discovery; выключен до проверки схемы endpoint","sportsbook_api":"RapidAPI sportsbook discovery; выключен до проверки схемы endpoint","sportapi":"RapidAPI SportAPI discovery; выключен до проверки схемы endpoint","freeapilivefootball":"RapidAPI free football discovery; выключен до проверки схемы endpoint"}
    return meanings.get(name.lower(), "вспомогательный источник/health probe")


def debug_provider_rows() -> dict[str, dict[str, Any]]:
    debug = load_json(DEBUG_PATH, {}); diag = debug.get("provider_diagnostics") if isinstance(debug, dict) else {}
    if not isinstance(diag, dict): return {}
    summary = diag.get("summary") if isinstance(diag.get("summary"), dict) else {}
    providers = summary.get("providers") if isinstance(summary.get("providers"), dict) else diag.get("providers")
    statuses = summary.get("provider_status") if isinstance(summary.get("provider_status"), dict) else diag.get("provider_status")
    out: dict[str, dict[str, Any]] = {}
    if isinstance(providers, dict):
        for name, row in providers.items():
            if isinstance(row, dict): out[str(name)] = dict(row)
    if isinstance(statuses, dict):
        for name, row in statuses.items():
            if isinstance(row, dict): out.setdefault(str(name), {})["status"] = row
    return out


def health_rows() -> dict[str, dict[str, Any]]:
    payload = load_json(HEALTH_PATH, {}); rows = payload.get("results") if isinstance(payload, dict) else []; out: dict[str, dict[str, Any]] = {}
    if isinstance(rows, list):
        for row in rows:
            if not isinstance(row, dict): continue
            raw = str(row.get("provider") or "").strip(); name = HEALTH_ALIASES.get(raw, raw)
            if not name: continue
            current = out.get(name)
            if current is None or (current.get("status") != "ok" and row.get("status") == "ok"): out[name] = row
    return out


def compact_runtime(name: str, row: dict[str, Any]) -> str:
    stats = row.get("stats") if isinstance(row.get("stats"), dict) else {}; status = row.get("status") if isinstance(row.get("status"), dict) else {}; src = stats or status or row
    matches = row.get("matches_with_data", src.get("contexts_built", src.get("matches_built", src.get("offers_parsed"))))); items = row.get("items_total"); parts: list[str] = []
    if matches is not None: parts.append(f"data {matches}/{items if items is not None else matches}")
    for key in ("requests", "response_errors", "contexts_built", "matches_considered", "weatherapi_requests", "openweathermap_requests", "weatherapi_enriched", "openweathermap_enriched", "cache_hits", "no_weather_payload", "budget_exhausted", "events_matched", "offers_parsed", "fixtures_fetched", "matches_built"):
        value = src.get(key)
        if value not in (None, "", [], {}): parts.append(f"{{'requests':'req','response_errors':'err'}.get(key,key)} {value}")
    if src.get("enabled") is False and not parts: parts.append("disabled")
    return ", ".join(parts) if parts else "нет runtime-строки"


def compact_health(name: str, row: dict[str, Any]) -> str:
    if not row: return "нет health-probe"
    bits = [str(row.get("status") or "unknown")]
    if row.get("configured") is not None: bits.append(f"configured {str(row.get('configured')).lower()}")
    if row.get("requests") is not None: bits.append(f"req {row.get('requests')}")
    if row.get("useful_rows") is not None: bits.append(f"rows {row.get('useful_rows')}")
    statuses = row.get("http_statuses")
    if isinstance(statuses, list) and statuses: bits.append("http " + "/".join(str(x) for x in statuses[:3]))
    msg = str(row.get("message") or "")
    if msg and msg != "ok": bits.append(msg[:90])
    return ", ".join(bits)


def build_api_work_block(text: str) -> str:
    by_provider: dict[str, dict[str, str]] = {}
    for line in extract_provider_lines(text): by_provider.setdefault(provider_name(line), {})["work"] = line.replace(f"• {provider_name(line)}: ", "")
    for line in quota_lines_from_text(text): by_provider.setdefault(provider_name(line), {})["quota"] = line.replace(f"• {provider_name(line)}: ", "")
    for name, quota in quota_from_budget_json().items(): by_provider.setdefault(name, {})["quota"] = quota
    for name, row in debug_provider_rows().items():
        if name.lower() not in REMOVED_PROVIDERS: by_provider.setdefault(name, {})["runtime"] = compact_runtime(name, row)
    for name, row in health_rows().items():
        if name.lower() not in REMOVED_PROVIDERS: by_provider.setdefault(name, {})["health"] = compact_health(name, row)
    for required in API_ORDER:
        if required not in REMOVED_PROVIDERS: by_provider.setdefault(required, {})
    names = sorted(by_provider, key=lambda x: (API_ORDER.index(x.lower()) if x.lower() in API_ORDER else 99, x.lower()))
    lines = ["🧩 Работа API — разбор", ""]
    for name in names:
        item = by_provider[name]
        if not any(item.values()) and name.lower() not in {"highlightly", "newsdata", "guardian", "sharpapi_configured_base"}: continue
        lines.append(f"• {name}: {provider_meaning(name)}")
        lines.append(f"  - runtime: {item.get('runtime') or item.get('work') or 'не участвовал в прогнозном runtime'}")
        lines.append(f"  - health: {item.get('health') or 'нет health-probe'}")
        lines.append(f"  - квота: {item.get('quota') or 'нет quota-строки'}")
    return "\n".join(lines)


def strip_old_api_work_block(text: str) -> str:
    marker = "🧩 Работа API — разбор"; idx = text.find(marker)
    if idx < 0: return text
    tail = text[idx:]; next_markers = [m for m in [tail.find("\n🔌 "), tail.find("\n📌 ")] if m > 0]
    if not next_markers: return text[:idx].rstrip() + "\n"
    cut = min(next_markers); return text[:idx].rstrip() + "\n\n" + tail[cut + 1:].lstrip()


def insert_api_work_block(text: str) -> str:
    text = strip_old_api_work_block(text); block = build_api_work_block(text); marker = "🔌 API / квоты последнего run"; idx = text.find(marker)
    if idx < 0: return text.rstrip() + "\n\n" + block + "\n"
    return text[:idx].rstrip() + "\n\n" + block + "\n\n" + text[idx:].rstrip() + "\n"


def clean_report(text: str) -> str:
    text = remove_deleted_provider_lines(text); text = insert_api_work_block(text); text = replace_conclusion(text); text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip() + "\n"


def split_messages(text: str, limit: int = 3600) -> list[str]:
    text = text.strip()
    if len(text) <= limit: return [text]
    chunks: list[str] = []; current: list[str] = []; current_len = 0
    for line in text.splitlines():
        extra = len(line) + 1
        if current and current_len + extra > limit:
            chunks.append("\n".join(current).strip()); current = []; current_len = 0
        current.append(line); current_len += extra
    if current: chunks.append("\n".join(current).strip())
    total = len(chunks)
    if total <= 1: return chunks
    return [f"🧾 Подробный отчёт run — часть {i}/{total}\n\n{chunk}" for i, chunk in enumerate(chunks, 1)]


def telegram_send(token: str, chat_id: str, text: str) -> tuple[bool, str]:
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = parse.urlencode({"chat_id": chat_id, "text": text, "disable_web_page_preview": "true"}).encode("utf-8")
    try:
        with request.urlopen(url, data=data, timeout=20) as response:  # noqa: S310
            body = response.read().decode("utf-8", errors="replace")
            return response.status == 200, body[:500]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


def should_send() -> bool:
    return env_bool("DETAILED_RUN_REPORT_SEND_TELEGRAM", True) or env_bool("DETAILED_RUN_REPORT_FORCE_SEND", False)


def main() -> int:
    health_probe = ensure_health_report(); raw = read_text(TXT_PATH)
    if not raw:
        payload = {"status": "skipped", "reason": "latest-detailed-run-report.txt missing", "path": str(TXT_PATH), "health_probe": health_probe}; write_json(Path(".data/exports/latest-detailed-run-report-send-clean.json"), payload); print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)); return 0
    cleaned = clean_report(raw); write_text(OUT_PATH, cleaned); scorecard_patch: dict[str, Any] = {"status": "not_run"}
    try:
        from scripts.patch_ideal_audit_scorecard import main as patch_scorecard_main
        patch_scorecard_main(); scorecard_patch = load_json(Path(".data/exports/latest-ideal-audit-scorecard-patch.json"), {"status": "unknown"}); cleaned = read_text(OUT_PATH) or cleaned
    except Exception as exc:
        scorecard_patch = {"status": "error", "error": f"{type(exc).__name__}: {exc}"}; write_json(Path(".data/exports/latest-ideal-audit-scorecard-patch.json"), scorecard_patch)
    token = str(os.getenv("TELEGRAM_TOKEN") or "").strip(); chat_id = str(os.getenv("TELEGRAM_CHAT_ID") or "").strip(); chunks = split_messages(cleaned, env_int("TELEGRAM_MESSAGE_SOFT_LIMIT", 3600)); sent: list[dict[str, Any]] = []
    if should_send() and token and chat_id:
        for chunk in chunks:
            ok, body = telegram_send(token, chat_id, chunk); sent.append({"ok": ok, "response_preview": body})
    else:
        sent.append({"ok": False, "response_preview": "send_disabled_or_missing_telegram_credentials"})
    payload = {"status": "sent" if sent and all(item.get("ok") for item in sent) else "not_sent_or_partial", "created_at_utc": datetime.now(UTC).isoformat(), "source_path": str(TXT_PATH), "cleaned_path": str(OUT_PATH), "chunks": len(chunks), "removed_providers": sorted(REMOVED_PROVIDERS), "api_work_block_added": "🧩 Работа API" in cleaned, "ideal_audit_scorecard_added": "🧭 Ideal runtime audit" in cleaned, "scorecard_patch": scorecard_patch, "api_health_present": HEALTH_PATH.exists(), "health_probe": health_probe, "sent": sent, "json_report_present": JSON_PATH.exists()}
    write_json(Path(".data/exports/latest-detailed-run-report-send-clean.json"), payload); write_json(STATE_PATH, {"last_sent_at_utc": payload["created_at_utc"], "chunks": len(chunks), "cleaned_path": str(OUT_PATH)}); print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)); return 0


if __name__ == "__main__":
    raise SystemExit(main())
