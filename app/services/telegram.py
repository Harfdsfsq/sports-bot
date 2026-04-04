from __future__ import annotations

from typing import Iterable

import httpx

from app.config import Settings
from app.schemas import CandidateBet


class TelegramPublisher:
    def __init__(self, settings: Settings):
        self.settings = settings

    @staticmethod
    def _market_label(family: str) -> str:
        mapping = {
            'h2h': 'Исход',
            'totals': 'Тотал',
            'spreads': 'Фора',
            'dnb': 'Фора 0',
            'doubleChance': 'Двойной шанс',
            'btts': 'Обе забьют',
            'teamTotals': 'Инд. тотал',
        }
        return mapping.get(family, family)

    @staticmethod
    def _selection_label(bet: CandidateBet) -> str:
        value = (bet.selection or '').strip()
        lower = value.lower()

        if lower == 'over':
            return 'Больше'
        if lower == 'under':
            return 'Меньше'
        if lower == 'draw':
            return 'Ничья'
        if lower in {'1', 'home'}:
            return bet.home_team
        if lower in {'2', 'away'}:
            return bet.away_team
        if lower in {'x'}:
            return 'Ничья'
        return value

    @staticmethod
    def _format_point(point: float | None) -> str:
        if point is None:
            return ''
        if float(point).is_integer():
            return f' ({int(point)})'
        return f' ({point:g})'

    def render_message(self, bets: list[CandidateBet]) -> str:
        header = f'🔥 {len(bets)} лучших валуйных ставок на ближайшие 48 часов\n\n'
        notes = (
            'В выдачу попадают только одиночные ставки с подтверждённым рыночным сигналом. '
            'На один матч — не более одной ставки.'
        )
        blocks: list[str] = [header + notes]

        for idx, bet in enumerate(bets, start=1):
            point_suffix = self._format_point(bet.point)
            xg_block = ''
            if bet.expected_home is not None and bet.expected_away is not None:
                xg_block = f"\n📈 xG: {bet.expected_home:.2f} : {bet.expected_away:.2f}"

            blocks.append(
                (
                    f"{idx}. {bet.home_team} - {bet.away_team}\n"
                    f"🎯 Рынок: {self._market_label(bet.family)} | Выбор: {self._selection_label(bet)}{point_suffix}\n"
                    f"💸 Кэф: {bet.odds:.2f} | EV: {bet.ev_pct:.2f}% | Edge: {bet.edge_pct:.2f}%\n"
                    f"📊 Модель: {bet.model_probability * 100:.1f}% | скорр.: {bet.adjusted_probability * 100:.1f}% | линия: {bet.implied_probability * 100:.1f}%\n"
                    f"✅ Уверенность: {bet.confidence:.1f}% | Книг: {bet.books_count} | Источников: {bet.sources_count}\n"
                    f"🏆 Лига: {bet.league_name}\n"
                    f"🕒 Старт: {bet.commence_time.strftime('%d.%m.%Y %H:%M')}"
                    f"{xg_block}\n"
                    f"📌 Причины: {'; '.join(bet.reasons[:3]) if bet.reasons else 'модельный сигнал'}"
                )
            )

        return '\n\n'.join(blocks)

    def _chunk_messages(self, bets: list[CandidateBet], max_chars: int = 3500) -> list[str]:
        if not bets:
            return []

        chunks: list[str] = []
        current: list[CandidateBet] = []

        for bet in bets:
            trial = current + [bet]
            message = self.render_message(trial)
            if current and len(message) > max_chars:
                chunks.append(self.render_message(current))
                current = [bet]
            else:
                current = trial

        if current:
            chunks.append(self.render_message(current))

        return chunks

    async def publish(self, bets: list[CandidateBet]) -> tuple[int, list[str]]:
        if not bets:
            return 0, []

        payloads = self._chunk_messages(bets)
        token = self.settings.telegram_bot_token
        chat_id = self.settings.telegram_chat_id

        if self.settings.publish_dry_run or not token or not chat_id:
            return 0, payloads

        sent = 0
        async with httpx.AsyncClient(timeout=20.0) as client:
            for message in payloads:
                response = await client.post(
                    f'https://api.telegram.org/bot{token}/sendMessage',
                    json={
                        'chat_id': chat_id,
                        'text': message,
                        'disable_web_page_preview': True,
                    },
                )
                response.raise_for_status()
                sent += 1

        return sent, payloads
