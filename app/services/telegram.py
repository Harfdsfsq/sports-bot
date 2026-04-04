from __future__ import annotations

from datetime import datetime
from typing import Iterable

import httpx

from app.config import Settings
from app.schemas import CandidateBet


class TelegramPublisher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def publish(self, candidates: Iterable[CandidateBet]) -> tuple[int, list[str]]:
        items = list(candidates)
        if not items:
            return 0, []

        message = self._render_message(items)
        if self.settings.publish_dry_run:
            return 0, [message]

        token = self.settings.telegram_token or self.settings.telegram_bot_token
        if not token or not self.settings.telegram_chat_id:
            return 0, []

        messages = self._split_message(message, 3600)
        sent = 0
        async with httpx.AsyncClient(timeout=20.0) as client:
            for text in messages:
                response = await client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json={
                        "chat_id": self.settings.telegram_chat_id,
                        "text": text,
                        "disable_web_page_preview": True,
                    },
                )
                if response.status_code == 200:
                    sent += 1
        return sent, messages

    def _render_message(self, candidates: list[CandidateBet]) -> str:
        horizon = "48–72 часа" if self.settings.publish_window_hours > 48 else "48 часов"
        lines = [
            f"🔥 {len(candidates)} лучших валуйных ставок на ближайшие {horizon}",
            "",
            "В выдачу попадают только одиночные ставки с подтверждением рынком, достаточным числом букмекеров и модельным контекстом.",
            "На один матч — не более одной ставки, без коррелированных дублей.",
            "",
        ]

        for idx, bet in enumerate(candidates, start=1):
            kickoff = bet.commence_time.astimezone(self.settings.tzinfo).strftime("%d.%m.%Y %H:%M МСК")
            market = self._family_ru(getattr(bet, "family", ""))
            selection = self._selection_ru(getattr(bet, "selection", ""), getattr(bet, "point", None), getattr(bet, "family", ""))
            lines.extend(
                [
                    f"{idx}. {bet.home_team} - {bet.away_team}",
                    f"🎯 Рынок: {market} | Выбор: {selection}",
                    f"💸 Кэф: {self._fmt(getattr(bet, 'odds', None))} | EV: {self._fmt(getattr(bet, 'ev_pct', None))}% | Edge: {self._fmt(getattr(bet, 'edge_pct', None))}%",
                    f"📊 Модель: {self._fmt_pct(getattr(bet, 'model_prob', None))} | скорр.: {self._fmt_pct(getattr(bet, 'confidence', None))} | линия: {self._fmt_pct(getattr(bet, 'market_prob', None))}",
                    f"✅ Уверенность: {self._fmt_pct(getattr(bet, 'confidence', None))} | Книг: {getattr(bet, 'books_count', getattr(bet, 'books', '-'))} | Источников: {getattr(bet, 'sources_count', getattr(bet, 'sources', '-'))}",
                    f"🏆 Лига: {getattr(bet, 'league_name', '')}",
                    f"🕒 Старт: {kickoff}",
                ]
            )
            if getattr(bet, "home_xg", None) is not None or getattr(bet, "away_xg", None) is not None:
                lines.append(f"📈 xG: {self._fmt(getattr(bet, 'home_xg', None))} : {self._fmt(getattr(bet, 'away_xg', None))}")
            reasons = getattr(bet, "reasons", None)
            if reasons:
                if isinstance(reasons, (list, tuple)):
                    reason_text = "; ".join(str(item) for item in reasons)
                else:
                    reason_text = str(reasons)
                lines.append(f"📌 Причины: {reason_text}")
            lines.append("")

        return "\n".join(lines).strip()

    @staticmethod
    def _family_ru(value: str) -> str:
        mapping = {
            "h2h": "Исход",
            "spreads": "Фора",
            "totals": "Тотал",
            "doubleChance": "Двойной шанс",
            "dnb": "Ничья нет ставки",
            "btts": "Обе забьют",
            "teamTotals": "Индивидуальный тотал",
        }
        return mapping.get(str(value), str(value))

    @staticmethod
    def _selection_ru(selection: str, point: float | None, family: str) -> str:
        raw = str(selection or "").strip()
        lowered = raw.lower()
        if lowered == "under":
            return f"Меньше ({TelegramPublisher._fmt(point)})" if point is not None else "Меньше"
        if lowered == "over":
            return f"Больше ({TelegramPublisher._fmt(point)})" if point is not None else "Больше"
        if lowered == "draw":
            return "Ничья"
        if family == "spreads" and point is not None:
            return f"{raw} ({TelegramPublisher._fmt(point)})"
        if point is not None and family in {"totals", "teamTotals"}:
            return f"{raw} ({TelegramPublisher._fmt(point)})"
        return raw

    @staticmethod
    def _fmt(value: object) -> str:
        if value is None or value == "":
            return "-"
        try:
            return f"{float(value):.2f}".rstrip("0").rstrip(".")
        except Exception:
            return str(value)

    @staticmethod
    def _fmt_pct(value: object) -> str:
        if value is None or value == "":
            return "-"
        try:
            number = float(value)
            if number <= 1.0:
                number *= 100.0
            return f"{number:.1f}%".rstrip("0").rstrip(".") + ("%" if not str(number).endswith("%") else "")
        except Exception:
            return str(value)

    @staticmethod
    def _split_message(message: str, limit: int) -> list[str]:
        if len(message) <= limit:
            return [message]
        parts: list[str] = []
        current: list[str] = []
        current_len = 0
        for line in message.splitlines():
            if current and current_len + len(line) + 1 > limit:
                parts.append("\n".join(current))
                current = [line]
                current_len = len(line)
            else:
                current.append(line)
                current_len += len(line) + 1
        if current:
            parts.append("\n".join(current))
        return parts
