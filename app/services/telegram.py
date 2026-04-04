from __future__ import annotations

from typing import Iterable

import httpx

from app.config import Settings
from app.schemas import CandidateBet

FAMILY_LABELS = {
    "h2h": "Исход",
    "totals": "Тотал",
    "spreads": "Фора",
    "dnb": "Без ничьей",
    "doubleChance": "Двойной шанс",
    "btts": "Обе забьют",
    "teamTotals": "Инд. тотал",
}

SELECTION_LABELS = {
    "1": "П1",
    "home": "П1",
    "2": "П2",
    "away": "П2",
    "x": "Ничья",
    "draw": "Ничья",
    "1x": "1X",
    "home_or_draw": "1X",
    "x2": "X2",
    "draw_or_away": "X2",
    "12": "12",
    "home_or_away": "12",
    "yes": "Да",
    "no": "Нет",
    "over": "Больше",
    "under": "Меньше",
}


class TelegramPublisher:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _family_label(self, family: str) -> str:
        return FAMILY_LABELS.get(family, family)

    def _selection_label(self, bet: CandidateBet) -> str:
        raw = (bet.selection or "").strip()
        key = raw.lower().replace(" ", "_")

        if bet.family in {"totals", "teamTotals"}:
            return SELECTION_LABELS.get(key, raw.title())

        if bet.family == "spreads":
            if key in {"home", "1", bet.home_team.lower()}:
                return bet.home_team
            if key in {"away", "2", bet.away_team.lower()}:
                return bet.away_team
            return SELECTION_LABELS.get(key, raw)

        if bet.family == "h2h":
            if key in {"home", "1"}:
                return "П1"
            if key in {"away", "2"}:
                return "П2"
            if key in {"draw", "x"}:
                return "Ничья"

        return SELECTION_LABELS.get(key, raw)

    @staticmethod
    def _chunk(items: list[CandidateBet], size: int) -> Iterable[list[CandidateBet]]:
        for idx in range(0, len(items), size):
            yield items[idx : idx + size]

    def render_message(self, bets: list[CandidateBet]) -> str:
        header = f"🔥 {len(bets)} лучших валуйных ставок на ближайшие 48–72 часа\n\n"
        notes = (
            "В выдачу попадают только одиночные ставки с подтверждением рынком, "
            "достаточным числом букмекеров и модельным контекстом.\n"
            "На один матч — не более одной ставки, без коррелированных дублей."
        )
        blocks: list[str] = [header + notes]

        for idx, bet in enumerate(bets, start=1):
            xg = ""
            if bet.expected_home is not None and bet.expected_away is not None:
                xg = f"\n📈 xG: {bet.expected_home:.2f} : {bet.expected_away:.2f}"

            point_suffix = f" ({bet.point:g})" if bet.point is not None else ""
            family_label = self._family_label(bet.family)
            selection_label = self._selection_label(bet)

            blocks.append(
                f"{idx}. {bet.home_team} - {bet.away_team}\n"
                f"🎯 Рынок: {family_label} | Выбор: {selection_label}{point_suffix}\n"
                f"💸 Кэф: {bet.odds:.2f} | EV: {bet.ev_pct:.2f}% | Edge: {bet.edge_pct:.2f}%\n"
                f"📊 Модель: {bet.model_probability * 100:.1f}% | скорр.: {bet.adjusted_probability * 100:.1f}% | "
                f"линия: {bet.implied_probability * 100:.1f}%\n"
                f"✅ Уверенность: {bet.confidence:.1f}% | Книг: {bet.books_count} | Источников: {bet.sources_count}\n"
                f"🏆 Лига: {bet.league_name}\n"
                f"🕒 Старт: {bet.commence_time.strftime('%d.%m.%Y %H:%M')}"
                f"{xg}\n"
                f"📌 Причины: {'; '.join(bet.reasons[:3])}"
            )

        return "\n\n".join(blocks)

    async def publish(self, bets: list[CandidateBet]) -> tuple[int, list[str]]:
        if not bets:
            return 0, []

        messages = [self.render_message(chunk) for chunk in self._chunk(bets, 5)]

        if self.settings.publish_dry_run or not self.settings.telegram_token or not self.settings.telegram_chat_id:
            return 0, messages

        sent = 0
        async with httpx.AsyncClient(timeout=20.0) as client:
            for message in messages:
                response = await client.post(
                    f"https://api.telegram.org/bot{self.settings.telegram_token}/sendMessage",
                    json={
                        "chat_id": self.settings.telegram_chat_id,
                        "text": message,
                        "disable_web_page_preview": True,
                    },
                )
                response.raise_for_status()
                sent += 1

        return sent, messages
