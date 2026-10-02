"""Read-only report for a manual pipeline run; never refetches provider data."""
from __future__ import annotations

import json
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.request import Request, urlopen

UTC = UTC


def timestamp(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.astimezone(UTC) if parsed.tzinfo else None
    except (ValueError, TypeError):
        return None


def fresh_summary(root: Path, started: datetime) -> dict:
    for name in ('.logs/debug-last-run.json', '.data/exports/latest-run-summary.json'):
        try:
            payload = json.loads((root / name).read_text())
            summary = payload.get('summary', payload)
            created = timestamp(summary.get('started_time_utc'))
            if created and started - timedelta(seconds=5) <= created <= datetime.now(UTC) + timedelta(seconds=5):
                return summary
        except (OSError, ValueError, AttributeError):
            continue
    return {}


def build_report(root: Path, started: datetime) -> dict:
    summary = fresh_summary(root, started)
    fallback = {}
    try:
        candidate = json.loads((root / '.data/exports/latest-controlled-fallback-report.json').read_text())
        created = timestamp(candidate.get('created_at') or candidate.get('created_at_utc'))
        if created and started - timedelta(seconds=5) <= created <= datetime.now(UTC) + timedelta(seconds=5):
            fallback = candidate
    except (OSError, ValueError, AttributeError):
        pass
    return {
        'created_at_utc': datetime.now(UTC).isoformat(),
        'run_id': os.getenv('GITHUB_RUN_ID', 'local'),
        'branch': os.getenv('GITHUB_REF_NAME', 'local'),
        'commit': os.getenv('GITHUB_SHA', ''),
        'dry_run': os.getenv('PUBLISH_DRY_RUN', 'true').lower() in {'true', '1', 'yes', 'on'},
        'pipeline_outcome': os.getenv('PIPELINE_OUTCOME', 'unknown'),
        'fallback_outcome': os.getenv('FALLBACK_OUTCOME', 'unknown'),
        'fresh_summary_found': bool(summary),
        'summary': summary,
        'fallback': fallback,
        'telegram_report_sent': False,
    }


def render(report: dict) -> str:
    summary = report['summary']
    lines = [f"sports-bot · {report['branch']} · run {report['run_id']}",
             'Режим: ' + ('проверка без отправки' if report['dry_run'] else 'Telegram'),
             f"Pipeline: {report['pipeline_outcome']}; fallback: {report['fallback_outcome']}"]
    if not report['fresh_summary_found']:
        lines.append('Нет сводки текущего запуска. Проверьте лог pipeline; старые результаты не использованы.')
    else:
        for key, label in (
            ('matches_before_publish_window', 'Матчи до отбора'),
            ('matches_seen', 'Матчи модели'), ('matches_with_offers', 'Матчи с линиями'),
            ('contexts_built', 'Контексты'), ('candidates_before_quality', 'Кандидаты до quality'),
            ('candidates_raw', 'Кандидаты после quality'), ('candidates_publishable', 'Готовы к публикации'),
            ('published_to_telegram', 'Прогнозы Telegram из pipeline')):
            lines.append(f"{label}: {summary.get(key, 'нет данных')}")
        # Service messages are intentionally not counted as forecasts.
        reasons = summary.get('filtering') or {}
        if isinstance(reasons, dict):
            counts = [(str(k), v) for k, v in reasons.items() if isinstance(v, (int, float)) and v > 0]
            for key, value in sorted(counts, key=lambda item: item[1], reverse=True)[:5]:
                lines.append(f'Отбор: {key} = {value}')
    fallback = report['fallback']
    lines.append('Резерв: ' + str(fallback.get('status', 'нет отчёта')))
    fallback_picks = int(fallback.get('selected_count') or 0) if fallback.get('published') is True else 0
    lines.append(f'Прогнозы Telegram из резерва: {fallback_picks}')
    return '\n'.join(lines)[:3500]


def send_telegram(text: str) -> bool:
    token = os.getenv('TELEGRAM_BOT_TOKEN') or os.getenv('TELEGRAM_TOKEN')
    chat = os.getenv('TELEGRAM_CHAT_ID')
    if not token or not chat:
        return False
    request = Request(f'https://api.telegram.org/bot{token}/sendMessage',
                      data=json.dumps({'chat_id': chat, 'text': text}).encode(),
                      headers={'Content-Type': 'application/json'}, method='POST')
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read())
        return result.get('ok') is True and bool((result.get('result') or {}).get('message_id'))
    except Exception as exc:
        # Exception URLs may contain the bot token; log only the error type.
        print(f'Telegram report failed: {type(exc).__name__}')
        return False


def main(root: Path | None = None) -> int:
    root = root or Path.cwd()
    started = timestamp(os.getenv('RUN_STARTED_AT_UTC'))
    if started is None:
        print('RUN_STARTED_AT_UTC is required for a current-run report')
        return 1
    report = build_report(root, started)
    text = render(report)
    if not report['dry_run']:
        report['telegram_report_sent'] = send_telegram(text)
    directory = root / '.data/exports'
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'latest-manual-run-report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    (directory / 'latest-manual-run-report.txt').write_text(text + '\n')
    print(text)
    delivered = report['dry_run'] or report['telegram_report_sent']
    return 0 if delivered and report['fresh_summary_found'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
