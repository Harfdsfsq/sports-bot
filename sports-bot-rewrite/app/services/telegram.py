from __future__ import annotations

import httpx

from app.config import Settings
from app.schemas import CandidateBet


class TelegramPublisher:
    def __init__(self, settings: Settings):
        self.settings = settings

    def render_message(self, bets: list[CandidateBet]) -> str:
        header = f'🔥 {len(bets)} лучших value-ставок на ближайшие 48–72 часа\n\n'
        notes = (
            'Публикуются только одиночные ставки с подтверждением рынком, достаточным числом букмекеров '
            'и модельным контекстом. Одна ставка на матч, без коррелированных дублей.\n'
        )
        blocks: list[str] = [header + notes]
        for idx, bet in enumerate(bets, start=1):
            xg = ''
            if bet.expected_home is not None and bet.expected_away is not None:
                xg = f"\n📊 xG: {bet.expected_home:.2f} : {bet.expected_away:.2f}"
            point_suffix = f' ({bet.point:g})' if bet.point is not None else ''
            blocks.append(
                f"{idx}. {bet.home_team} - {bet.away_team}\n"
                f"🏷 Рынок: {bet.family} | Выбор: {bet.selection}{point_suffix}\n"
                f"💸 Кэф: {bet.odds:.2f} | EV: {bet.ev_pct:.2f}% | Edge: {bet.edge_pct:.2f}%\n"
                f"🧠 Модель: {bet.model_probability * 100:.1f}% | скорр.: {bet.adjusted_probability * 100:.1f}% | линия: {bet.implied_probability * 100:.1f}%\n"
                f"✅ Уверенность: {bet.confidence:.1f}% | Книг: {bet.books_count} | Источников: {bet.sources_count}\n"
                f"🏆 Лига: {bet.league_name}\n"
                f"🕒 Старт: {bet.commence_time.strftime('%d.%m.%Y %H:%M')}"
                f"{xg}\n"
                f"📌 Причины: {'; '.join(bet.reasons[:3])}"
            )
        return '\n\n'.join(blocks)

    async def publish(self, bets: list[CandidateBet]) -> str | None:
        if not bets:
            return None
        message = self.render_message(bets)
        if self.settings.publish_dry_run or not self.settings.telegram_bot_token or not self.settings.telegram_chat_id:
            return message
        async with httpx.AsyncClient(timeout=20.0) as client:
            await client.post(
                f'https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage',
                json={
                    'chat_id': self.settings.telegram_chat_id,
                    'text': message,
                    'disable_web_page_preview': True,
                },
            )
        return message
