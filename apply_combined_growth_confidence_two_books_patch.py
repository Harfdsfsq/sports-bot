from __future__ import annotations

import re
from pathlib import Path

ROOT = Path('.')


def read(path: str) -> str:
    file_path = ROOT / path
    if not file_path.exists():
        raise FileNotFoundError(f'File not found: {path}')
    return file_path.read_text(encoding='utf-8')


def write(path: str, content: str) -> None:
    file_path = ROOT / path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding='utf-8')


def replace_regex_once(text: str, pattern: str, repl: str, path: str) -> str:
    new_text, count = re.subn(pattern, repl, text, count=1, flags=re.MULTILINE | re.DOTALL)
    if count != 1:
        raise RuntimeError(f'Expected exactly one match in {path} for pattern: {pattern}')
    return new_text


def replace_literal_once(text: str, old: str, new: str, path: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(f'Expected exactly one literal match in {path}, found {count}: {old!r}')
    return text.replace(old, new, 1)


def upsert_env_var(workflow_text: str, key: str, value: str) -> str:
    pattern = re.compile(rf'^(?P<indent>\s*){re.escape(key)}:\s*.*$', re.MULTILINE)
    if pattern.search(workflow_text):
        return pattern.sub(lambda m: f"{m.group('indent')}{key}: {value}", workflow_text, count=1)

    marker = re.search(r'^(?P<indent>\s*)env:\s*$', workflow_text, flags=re.MULTILINE)
    if not marker:
        raise RuntimeError('Could not locate env: block in workflow file')

    insert_at = marker.end()
    indent = marker.group('indent') + '  '
    addition = f"\n{indent}{key}: {value}"
    return workflow_text[:insert_at] + addition + workflow_text[insert_at:]


TELEGRAM_CONTENT = '''from __future__ import annotations
from datetime import UTC

import httpx

from app.config import Settings
from app.schemas import CandidateBet
from app.utils import russian_market_name, russian_selection


class TelegramPublisher:
    def __init__(self, settings: Settings):
        self.settings = settings

    def render_message(self, bets: list[CandidateBet]) -> str:
        header = f"🔥 {len(bets)} лучших валуйных ставок на ближайшие 48 часов\\n\\n"
        notes = (
            "В выдачу попадают только одиночные ставки с подтверждением минимум по 2 букмекерским котировкам "
            "из доступных рыночных источников. На один матч — не более одной ставки.\\n"
        )
        blocks: list[str] = [header + notes]

        for idx, bet in enumerate(bets, start=1):
            point_suffix = f" ({bet.point:g})" if bet.point is not None else ""
            xg = ""
            if bet.expected_home is not None and bet.expected_away is not None:
                xg = f"\\n📈 xG: {bet.expected_home:.2f} : {bet.expected_away:.2f}"
            start_text = bet.commence_time.astimezone(self.settings.tzinfo).strftime('%d.%m.%Y %H:%M МСК')
            blocks.append(
                f"{idx}. {bet.home_team} - {bet.away_team}\\n"
                f"🎯 Рынок: {russian_market_name(bet.family)} | Выбор: {russian_selection(bet.family, bet.selection, bet.point)}{point_suffix}\\n"
                f"💸 Кэф: {bet.odds:.2f} | EV: {bet.ev_pct:.2f}% | Edge: {bet.edge_pct:.2f}%\\n"
                f"📊 Модель: {bet.model_probability * 100:.1f}% | скорр.: {bet.adjusted_probability * 100:.1f}% | линия: {bet.market_probability * 100:.1f}%\\n"
                f"✅ Уверенность: {bet.confidence:.1f}% | Книг: {bet.books_count} | Источников: {bet.sources_count}\\n"
                f"🏆 Лига: {bet.league_name}\\n"
                f"🕒 Старт: {start_text}"
                f"{xg}\\n"
                f"📌 Причины: {'; '.join(bet.reasons[:3])}"
            )

        return '\\n\\n'.join(blocks)

    async def publish(self, bets: list[CandidateBet]) -> tuple[int, list[str]]:
        if not bets:
            return 0, []

        message = self.render_message(bets)
        if self.settings.publish_dry_run or not self.settings.telegram_token or not self.settings.telegram_chat_id:
            return 0, [message]

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f'https://api.telegram.org/bot{self.settings.telegram_token}/sendMessage',
                json={
                    'chat_id': self.settings.telegram_chat_id,
                    'text': message,
                    'disable_web_page_preview': True,
                },
            )
            response.raise_for_status()
            return 1, [message]
'''


def patch_config() -> None:
    path = 'app/config.py'
    text = read(path)

    replacements = {
        r"max_picks_per_run: int = Field\(default=5,": "max_picks_per_run: int = Field(default=7,",
        r"context_enrichment_match_limit: int = Field\(default=90,": "context_enrichment_match_limit: int = Field(default=180,",
        r"odds_api_io_page_limit: int = Field\(default=60,": "odds_api_io_page_limit: int = Field(default=100,",
        r"odds_api_io_max_pages_per_sport: int = Field\(default=4,": "odds_api_io_max_pages_per_sport: int = Field(default=8,",
        r"max_picks_per_league: int = Field\(default=2,": "max_picks_per_league: int = Field(default=3,",
        r"max_picks_per_family: int = Field\(default=2,": "max_picks_per_family: int = Field(default=3,",
    }
    for pattern, repl in replacements.items():
        text = replace_regex_once(text, pattern, repl, path)

    text = replace_regex_once(
        text,
        r"odds_api_io_bookmakers: CsvList = Field\(\s*default_factory=lambda: \['Bet365', 'Unibet'\],\s*validation_alias=AliasChoices\('ODDS_API_IO_BOOKMAKERS'\),\s*\)",
        "odds_api_io_bookmakers: CsvList = Field(\n        default_factory=lambda: ['Bet365', 'Unibet'],\n        validation_alias=AliasChoices('ODDS_API_IO_BOOKMAKERS'),\n    )",
        path,
    )

    if 'confidence_gap_bonus_weight' not in text:
        anchor = "consensus_tight_confidence_bonus: float = Field(default=2.0, validation_alias=AliasChoices('CONSENSUS_TIGHT_CONFIDENCE_BONUS'))"
        insertion = anchor + "\n    confidence_gap_bonus_weight: float = Field(default=0.10, validation_alias=AliasChoices('CONFIDENCE_GAP_BONUS_WEIGHT'))\n    confidence_books_bonus: float = Field(default=0.90, validation_alias=AliasChoices('CONFIDENCE_BOOKS_BONUS'))\n    confidence_sources_bonus: float = Field(default=1.10, validation_alias=AliasChoices('CONFIDENCE_SOURCES_BONUS'))\n    confidence_price_premium_bonus: float = Field(default=0.08, validation_alias=AliasChoices('CONFIDENCE_PRICE_PREMIUM_BONUS'))\n    confidence_dispersion_penalty_weight: float = Field(default=0.18, validation_alias=AliasChoices('CONFIDENCE_DISPERSION_PENALTY_WEIGHT'))"
        text = replace_literal_once(text, anchor, insertion, path)

    write(path, text)


MODEL_REPLACEMENT = '''        consensus_dispersion_cap = float(getattr(self.settings, 'max_consensus_dispersion_pct', 6.5) or 6.5)
        if dispersion_pct is not None and dispersion_pct <= consensus_dispersion_cap:
            confidence += float(getattr(self.settings, 'consensus_tight_confidence_bonus', 2.0) or 2.0)

        raw_gap_pct = abs(model_prob - market_prob) * 100.0
        consensus_fair_odds = 1.0 / max(market_prob, 0.01)
        price_premium_pct = max(0.0, ((best_price / max(consensus_fair_odds, 1.01)) - 1.0) * 100.0)

        confidence += min(4.0, raw_gap_pct * float(getattr(self.settings, 'confidence_gap_bonus_weight', 0.10) or 0.10))
        confidence += max(0.0, len(books) - 1) * float(getattr(self.settings, 'confidence_books_bonus', 0.90) or 0.90)
        confidence += max(0.0, len(sources) - 1) * float(getattr(self.settings, 'confidence_sources_bonus', 1.10) or 1.10)
        confidence += min(2.0, price_premium_pct * float(getattr(self.settings, 'confidence_price_premium_bonus', 0.08) or 0.08))

        if dispersion_pct is not None and dispersion_pct > consensus_dispersion_cap:
            confidence -= min(
                2.5,
                (dispersion_pct - consensus_dispersion_cap)
                * float(getattr(self.settings, 'confidence_dispersion_penalty_weight', 0.18) or 0.18),
            )

        confidence = clamp(confidence, 0, 100)
        adjusted = shrink_probability(model_prob, market_prob, confidence, shrink_min, shrink_max)'''


def patch_model() -> None:
    path = 'app/services/model.py'
    text = read(path)
    pattern = (
        r"if dispersion_pct is not None and dispersion_pct <= float\(getattr\(self\.settings, 'max_consensus_dispersion_pct', 6\.5\) or 6\.5\):\n"
        r"\s*confidence \+= float\(getattr\(self\.settings, 'consensus_tight_confidence_bonus', 2\.0\) or 2\.0\)\n\s*\n"
        r"\s*confidence = clamp\(confidence, 0, 100\)\n\s*adjusted = shrink_probability\(model_prob, market_prob, confidence, shrink_min, shrink_max\)"
    )
    text = replace_regex_once(text, pattern, MODEL_REPLACEMENT, path)
    write(path, text)


ODDS_API_REPLACEMENT = '''        mapping = {
            "bet365": "Bet365",
            "unibet": "Unibet",
            "betfair": "BetFair",
            "betfairexchange": "BetFair",
            "pinnacle": "PinnacleSports",
            "pinnaclesports": "PinnacleSports",
        }

        for item in preferred:
            raw = str(item or "").strip()
            if not raw:
                continue
            key = normalize_bookmaker_name(raw)
            value = mapping.get(key, raw)
            if value and value not in values:
                values.append(value)

        return ",".join(values or ["Bet365", "Unibet"])'''


def patch_odds_api_io() -> None:
    path = 'app/providers/odds_api_io.py'
    text = read(path)
    pattern = (
        r"mapping = \{.*?\n\s*for item in preferred:\n\s*raw = str\(item or \"\"\)\.strip\(\)\n\s*if not raw:\n\s*continue\n\s*key = normalize_bookmaker_name\(raw\)\n\s*value = mapping\.get\(key, raw\)\n\s*if value and value not in values:\n\s*values\.append\(value\)\n\s*return \",\"\.join\(values or \[\"Bet365\", \"Unibet\"\]\)"
    )
    text = replace_regex_once(text, pattern, ODDS_API_REPLACEMENT, path)
    write(path, text)


def patch_workflow() -> None:
    path = '.github/workflows/run-bot.yml'
    text = read(path)

    updates = {
        'ODDS_API_IO_BOOKMAKERS': 'Bet365,Unibet',
        'ODDS_API_IO_PAGE_LIMIT': '100',
        'ODDS_API_IO_MAX_EVENT_PAGES_PER_SPORT': '8',
        'CONTEXT_ENRICHMENT_MATCH_LIMIT': '180',
        'MAX_MATCHES_FOR_ODDS_FETCH': '120',
        'MAX_PICKS_PER_RUN': '7',
        'MAX_PICKS_PER_LEAGUE': '3',
        'MAX_PICKS_PER_FAMILY': '3',
        'CONFIDENCE_GAP_BONUS_WEIGHT': '0.10',
        'CONFIDENCE_BOOKS_BONUS': '0.90',
        'CONFIDENCE_SOURCES_BONUS': '1.10',
        'CONFIDENCE_PRICE_PREMIUM_BONUS': '0.08',
        'CONFIDENCE_DISPERSION_PENALTY_WEIGHT': '0.18',
    }
    for key, value in updates.items():
        text = upsert_env_var(text, key, value)

    write(path, text)


if __name__ == '__main__':
    patch_config()
    patch_model()
    patch_odds_api_io()
    patch_workflow()
    write('app/services/telegram.py', TELEGRAM_CONTENT)
    print('Patched: app/config.py, app/services/model.py, app/providers/odds_api_io.py, app/services/telegram.py, .github/workflows/run-bot.yml')
