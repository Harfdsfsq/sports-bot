#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
import zipfile
from pathlib import Path
from typing import Any


def read_log_from_zip(path: Path) -> str:
    with zipfile.ZipFile(path) as zf:
        for name in ('0_run-bot.txt', 'run-bot/7_Run bot once.txt'):
            try:
                with zf.open(name) as fh:
                    return fh.read().decode('utf-8', errors='replace')
            except KeyError:
                continue
    raise FileNotFoundError('Не найден 0_run-bot.txt или run-bot/7_Run bot once.txt в архиве логов')


def extract_scalar(text: str, key: str) -> Any:
    pattern = rf'"{re.escape(key)}"\s*:\s*("[^"]*"|true|false|null|-?\d+(?:\.\d+)?)'
    m = re.search(pattern, text)
    if not m:
        return None
    raw = m.group(1)
    if raw.startswith('"') and raw.endswith('"'):
        return raw[1:-1]
    if raw == 'true':
        return True
    if raw == 'false':
        return False
    if raw == 'null':
        return None
    return float(raw) if '.' in raw else int(raw)


def extract_rejections(text: str) -> dict[str, int]:
    block = re.search(r'"rejections"\s*:\s*\{(.*?)\n\s*\}', text, flags=re.S)
    if not block:
        return {}
    body = block.group(1)
    pairs = re.findall(r'"([^"]+)"\s*:\s*(\d+)', body)
    return {k: int(v) for k, v in pairs}


def build_markdown(summary: dict[str, Any]) -> str:
    lines = [
        '# Run summary from log archive',
        '',
        f"- Матчей в окне: {summary.get('matches_seen')}",
        f"- С офферами: {summary.get('matches_with_offers')}",
        f"- Контекстов: {summary.get('contexts_built')}",
        f"- Кандидаты до quality: {summary.get('candidates_before_quality')}",
        f"- Кандидаты после quality: {summary.get('candidates_raw')}",
        f"- К публикации: {summary.get('candidates_publishable')}",
        f"- Опубликовано: {summary.get('published')}",
        '',
        '## Top rejections',
        '',
    ]
    for key, value in summary.get('top_rejections', []):
        lines.append(f'- {key}: {value}')
    lines.extend([
        '',
        '## Вывод',
        '',
        summary.get('diagnosis', ''),
        '',
    ])
    return '\n'.join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description='Extract concise summary from HARIZON run log zip')
    parser.add_argument('--zip', required=True, help='Path to ZIP log archive')
    parser.add_argument('--output-dir', required=True, help='Directory for summary files')
    args = parser.parse_args()

    zip_path = Path(args.zip)
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    text = read_log_from_zip(zip_path)
    rejections = extract_rejections(text)
    top_rejections = sorted(rejections.items(), key=lambda x: x[1], reverse=True)[:10]

    summary = {
        'matches_seen': extract_scalar(text, 'matches_seen'),
        'matches_with_offers': extract_scalar(text, 'matches_with_offers'),
        'contexts_built': extract_scalar(text, 'contexts_built'),
        'candidates_before_quality': extract_scalar(text, 'candidates_before_quality'),
        'candidates_raw': extract_scalar(text, 'candidates_raw'),
        'candidates_publishable': extract_scalar(text, 'candidates_publishable'),
        'published': extract_scalar(text, 'published'),
        'published_candidates_single_source_context': extract_scalar(text, 'published_candidates_single_source_context'),
        'published_candidates_with_derived_market_signal': extract_scalar(text, 'published_candidates_with_derived_market_signal'),
        'top_rejections': top_rejections,
        'diagnosis': (
            'Если top rejections возглавляют market_derived_signal_guard_* и publish_books_guard, '
            'узкое место находится не в матчах, а в фильтрах. Если quality_bad_historical_segment_guard '
            'при этом режет почти все кандидаты, historical layer временно нужно ослабить или отложить '
            'до накопления большей выборки.'
        ),
    }

    (out / 'run-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (out / 'run-summary.md').write_text(build_markdown(summary), encoding='utf-8')


if __name__ == '__main__':
    main()
