#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any


def read_csv(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open('r', encoding='utf-8', newline='') as handle:
        return list(csv.DictReader(handle))


def read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}


def to_float(value: Any) -> float | None:
    try:
        if value in (None, ''):
            return None
        return float(value)
    except Exception:
        return None


def probability_bucket(value: float | None) -> str:
    if value is None:
        return 'unknown'
    if value < 50:
        return '<50'
    if value < 55:
        return '50-55'
    if value < 60:
        return '55-60'
    if value < 65:
        return '60-65'
    return '65+'


def build_markdown(summary: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append('# Bot export analysis')
    lines.append('')
    lines.append(f"- Прогнозов в matches CSV: {summary['counts']['matches_rows']}")
    lines.append(f"- Ставок в ledger CSV: {summary['counts']['ledger_rows']}")
    lines.append(f"- Settled ставок: {summary['portfolio']['settled_count']}")
    lines.append(f"- Hit rate: {summary['portfolio']['hit_rate_pct']:.2f}%")
    lines.append(f"- ROI: {summary['portfolio']['roi_pct']:.2f}%")
    lines.append('')
    lines.append('## По семействам')
    lines.append('')
    lines.append('| family | count | hit_rate_pct | avg_odds | avg_confidence |')
    lines.append('|---|---:|---:|---:|---:|')
    for item in summary['by_family']:
        lines.append(
            f"| {item['family']} | {item['count']} | {item['hit_rate_pct']:.2f} | {item['avg_odds']:.2f} | {item['avg_confidence']:.2f} |"
        )
    lines.append('')
    lines.append('## По bucket вероятности')
    lines.append('')
    lines.append('| bucket | count | win_rate_pct |')
    lines.append('|---|---:|---:|')
    for item in summary['by_probability_bucket']:
        lines.append(f"| {item['bucket']} | {item['count']} | {item['win_rate_pct']:.2f} |")
    lines.append('')
    lines.append('## Top guard reasons')
    lines.append('')
    for key, value in summary['top_guard_reasons'].items():
        lines.append(f'- {key}: {value}')
    return "\n".join(lines) + "\n"



def main() -> None:
    parser = argparse.ArgumentParser(description='Analyze sports-bot export files.')
    parser.add_argument('--export-dir', required=True, help='Directory with sheet-matches.csv / sheet-bet-ledger.csv / guard-report.json')
    parser.add_argument('--output-dir', required=True, help='Where to write summary files')
    args = parser.parse_args()

    export_dir = Path(args.export_dir)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    matches_rows = read_csv(export_dir / 'sheet-matches.csv')
    ledger_rows = read_csv(export_dir / 'sheet-bet-ledger.csv')
    guard_report = read_json(export_dir / 'guard-report.json')

    settled = []
    for row in ledger_rows:
        result = row.get('result_binary') or row.get('binary_result') or row.get('is_win')
        pnl = to_float(row.get('pnl'))
        stake = to_float(row.get('stake_amount'))
        odds = to_float(row.get('odds')) or 0.0
        confidence = to_float(row.get('confidence')) or 0.0
        family = str(row.get('family') or 'unknown')
        try:
            result_value = float(result)
        except Exception:
            result_value = None
        if result_value is None:
            continue
        settled.append({
            'family': family,
            'result': result_value,
            'pnl': pnl or 0.0,
            'stake': stake or 0.0,
            'odds': odds,
            'confidence': confidence,
        })

    total_stake = sum(item['stake'] for item in settled)
    total_pnl = sum(item['pnl'] for item in settled)
    hit_rate = mean(item['result'] for item in settled) * 100.0 if settled else 0.0
    roi = (total_pnl / total_stake * 100.0) if total_stake else 0.0

    grouped_family: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in settled:
        grouped_family[item['family']].append(item)

    by_family = []
    for family, group in sorted(grouped_family.items()):
        by_family.append({
            'family': family,
            'count': len(group),
            'hit_rate_pct': mean(x['result'] for x in group) * 100.0 if group else 0.0,
            'avg_odds': mean(x['odds'] for x in group) if group else 0.0,
            'avg_confidence': mean(x['confidence'] for x in group) if group else 0.0,
        })

    bucket_counter: Counter[str] = Counter()
    bucket_wins: dict[str, list[float]] = defaultdict(list)
    for row in matches_rows:
        prob = to_float(row.get('forecast_adjusted_probability_pct'))
        bucket = probability_bucket(prob)
        bucket_counter[bucket] += 1
        result = to_float(row.get('result_binary') or row.get('binary_result'))
        if result is not None:
            bucket_wins[bucket].append(result)

    by_probability_bucket = []
    for bucket, count in bucket_counter.items():
        wins = bucket_wins.get(bucket, [])
        by_probability_bucket.append({
            'bucket': bucket,
            'count': count,
            'win_rate_pct': mean(wins) * 100.0 if wins else 0.0,
        })
    by_probability_bucket.sort(key=lambda item: item['bucket'])

    summary = {
        'counts': {
            'matches_rows': len(matches_rows),
            'ledger_rows': len(ledger_rows),
        },
        'portfolio': {
            'settled_count': len(settled),
            'hit_rate_pct': round(hit_rate, 4),
            'roi_pct': round(roi, 4),
            'stake': round(total_stake, 4),
            'pnl': round(total_pnl, 4),
        },
        'by_family': by_family,
        'by_probability_bucket': by_probability_bucket,
        'top_guard_reasons': dict((guard_report.get('top_rejections') or {})),
    }

    (output_dir / 'analysis-summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    (output_dir / 'analysis-summary.md').write_text(build_markdown(summary), encoding='utf-8')


if __name__ == '__main__':
    main()
