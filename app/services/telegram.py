from __future__ import annotations

from datetime import UTC
from html import escape
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

        rendered = self._render_message(items)

        if self.settings.publish_dry_run:
            return 0, [rendered]

        if not self.settings.telegram_bot_token or not self.settings.telegram_chat_id:
            return 0, []

        messages = self._split_message(rendered, 3600)
        sent = 0

        async with httpx.AsyncClient(timeout=20.0) as client:
            for message in messages:
                response = await client.post(
                    f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage",
                    json={
                        "chat_id": self.settings.telegram_chat_id,
                        "text": message,
                        "parse_mode": "HTML",
                        "disable_web_page_preview": True,
                    },
                )
                if response.status_code == 200:
                    sent += 1

        return sent, messages

    def _render_message(self, candidates: list[CandidateBet]) -> str:
        title = f"🔥 {len(candidates)} лучших валуйных ставок на ближайшие 48 часов"
        intro = (
            "В выдачу попадают только одиночные ставки с подтверждённым рыночным сигналом. "
            "На один матч — не более одной ставки."
        )

        blocks = [f"<b>{escape(title)}</b>", "", escape(intro)]

        for idx, bet in enumerate(candidates, start=1):
            blocks.extend(["", self._render_candidate(idx, bet)])

        return "\n".join(blocks).strip()

    def _render_candidate(self, idx: int, bet: CandidateBet) -> str:
        sport_name = self._sport_label(getattr(bet, "sport_key", ""))
        family_name = self._family_label(getattr(bet, "family", ""))

        point_text = ""
        if getattr(bet, "point", None) is not None:
            point_text = f" ({self._fmt_num(bet.point, 2)})"

        commence_time = getattr(bet, "commence_time", None)
        if commence_time is not None:
            start_text = commence_time.astimezone(UTC).strftime("%d.%m.%Y %H:%M UTC")
        else:
            start_text = "—"

        source_label = self._source_label(bet)

        lines = [
            f"⚽️ {escape(sport_name)}. {escape(getattr(bet, 'league_name', ''))}",
            f"{idx}. <b>{escape(getattr(bet, 'home_team', ''))} - {escape(getattr(bet, 'away_team', ''))}</b>",
            "",
            f"🎯 <b>{escape(family_name)}</b>",
            f"Прогноз: <b>{escape(str(getattr(bet, 'selection', '')))}{escape(point_text)}</b>",
            (
                f"💸 Коэффициент: <b>{self._fmt_num(getattr(bet, 'odds', None), 2)}</b> | "
                f"EV: <b>{self._fmt_num(getattr(bet, 'ev_pct', None), 2)}%</b> | "
                f"Edge: <b>{self._fmt_num(getattr(bet, 'edge_pct', None), 2)}%</b>"
            ),
            (
                f"📊 Модель: {self._fmt_pct(getattr(bet, 'model_probability', None))} | "
                f"скорр.: {self._fmt_pct(getattr(bet, 'adjusted_probability', None))} | "
                f"линия: {self._fmt_pct(getattr(bet, 'market_probability', None))}"
            ),
            (
                f"🧠 Уверенность: <b>{self._fmt_num(getattr(bet, 'confidence', None), 1)}%</b> | "
                f"Источник: {escape(source_label)}"
            ),
            f"🕒 Начало: {escape(start_text)}",
        ]

        reasons = [str(r).strip() for r in (getattr(bet, "reasons", None) or []) if str(r).strip()]
        if reasons:
            lines.extend(["", "📌 <b>Ключевые факторы:</b>"])
            for reason in reasons[:5]:
                lines.append(f"• {escape(reason)}")

        books_count = getattr(bet, "books_count", 0) or 0
        sources_count = getattr(bet, "sources_count", 0) or 0
        model_mode = getattr(bet, "model_mode", "") or "market_only"

        if books_count or sources_count:
            lines.extend(
                [
                    "",
                    (
                        f"📝 Подтверждение: букмекеров — {books_count}, "
                        f"источников — {sources_count}, режим — {escape(str(model_mode))}"
                    ),
                ]
            )

        return "\n".join(lines)

    @staticmethod
    def _split_message(message: str, limit: int) -> list[str]:
        if len(message) <= limit:
            return [message]

        parts: list[str] = []
        current: list[str] = []
        current_len = 0

        for line in message.splitlines():
            extra = len(line) + 1
            if current and current_len + extra > limit:
                parts.append("\n".join(current))
                current = [line]
                current_len = extra
            else:
                current.append(line)
                current_len += extra

        if current:
            parts.append("\n".join(current))

        return parts

    @staticmethod
    def _fmt_num(value: object, digits: int = 2) -> str:
        try:
            if value is None:
                return "—"
            number = float(value)
        except Exception:
            return "—"

        rounded = round(number, digits)
        text = f"{rounded:.{digits}f}".rstrip("0").rstrip(".")
        return text.replace(".", ",")

    @classmethod
    def _fmt_pct(cls, value: object) -> str:
        try:
            if value is None:
                return "—"
            number = float(value) * 100.0
        except Exception:
            return "—"
        return f"{cls._fmt_num(number, 1)}%"

    @staticmethod
    def _sport_label(sport_key: str) -> str:
        return {
            "soccer": "Футбол",
            "basketball": "Баскетбол",
            "baseball": "Бейсбол",
            "icehockey": "Хоккей",
        }.get(sport_key, str(sport_key).title() if sport_key else "Спорт")

    @staticmethod
    def _family_label(family: str) -> str:
        return {
            "h2h": "Исход",
            "totals": "Тотал",
            "spreads": "Фора",
            "dnb": "Draw No Bet",
            "doubleChance": "Двойной шанс",
            "btts": "Обе забьют",
            "teamTotals": "Индивидуальный тотал",
        }.get(family, family or "Ставка")

    @staticmethod
    def _source_label(bet: CandidateBet) -> str:
        source_summary = getattr(bet, "source_summary", None) or {}
        if isinstance(source_summary, dict):
            source = source_summary.get("best_source") or source_summary.get("source")
            if source:
                return str(source)
        return "Market"
