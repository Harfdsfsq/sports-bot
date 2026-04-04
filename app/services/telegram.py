from __future__ import annotations

from datetime import UTC

import httpx

from app.config import Settings
from app.schemas import CandidateBet
from app.utils import russian_market_name


class TelegramPublisher:
    def __init__(self, settings: Settings):
        self.settings = settings

    def render_message(self, bets: list[CandidateBet]) -> str:
        header = f'🔥 {len(bets)} лучших валуйных ставок на ближайшие 48 часов\n\n'
        notes = (
            'В выдачу попадают только одиночные ставки с подтверждённым рыночным сигналом. '
            'На один матч — не более одной ставки.\n'
        )
        blocks: list[str] = [header + notes]

        for idx, bet in enumerate(bets, start=1):
            point_suffix = f' ({bet.point:g})' if bet.point is not None else ''
            xg = ''
            if bet.expected_home is not None and bet.expected_away is not None:
                xg = f"\n📈 xG: {bet.expected_home:.2f} : {bet.expected_away:.2f}"

            start_text = bet.commence_time.astimezone(self.settings.tzinfo).strftime('%d.%m.%Y %H:%M МСК')
            blocks.append(
                f"{idx}. {bet.home_team} - {bet.away_team}\n"
                f"🎯 Рынок: {russian_market_name(bet.family)} | Выбор: {bet.selection}{point_suffix}\n"
                f"💸 Кэф: {bet.odds:.2f} | EV: {bet.ev_pct:.2f}% | Edge: {bet.edge_pct:.2f}%\n"
                f"📊 Модель: {bet.model_probability * 100:.1f}% | скорр.: {bet.adjusted_probability * 100:.1f}% | линия: {bet.market_probability * 100:.1f}%\n"
                f"✅ Уверенность: {bet.confidence:.1f}% | Книг: {bet.books_count} | Источников: {bet.sources_count}\n"
                f"🏆 Лига: {bet.league_name}\n"
                f"🕒 Старт: {start_text}"
                f"{xg}\n"
                f"📌 Причины: {'; '.join(bet.reasons[:3])}"
            )
        return '\n\n'.join(blocks)

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
