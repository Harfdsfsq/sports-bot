from __future__ import annotations

from typing import Iterable

import httpx

from app.config import Settings
from app.schemas import CandidateBet
from app.utils import round2


class TelegramPublisher:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def publish(self, candidates: Iterable[CandidateBet]) -> tuple[int, list[str]]:
        items = list(candidates)
        if not items:
            return 0, []
        if self.settings.publish_dry_run:
            return 0, [self._render_message(items)]
        if not self.settings.telegram_bot_token or not self.settings.telegram_chat_id:
            return 0, []

        messages = self._split_message(self._render_message(items), 3600)
        sent = 0
        async with httpx.AsyncClient(timeout=20.0) as client:
            for message in messages:
                response = await client.post(
                    f"https://api.telegram.org/bot{self.settings.telegram_bot_token}/sendMessage",
                    json={
                        "chat_id": self.settings.telegram_chat_id,
                        "text": message,
                        "disable_web_page_preview": True,
                    },
                )
                if response.status_code == 200:
                    sent += 1
        return sent, messages

    def _render_message(self, candidates: list[CandidateBet]) -> str:
        lines = ["Value picks"]
        for idx, bet in enumerate(candidates, start=1):
            line = (
                f"{idx}. {bet.home_team} vs {bet.away_team} | {bet.family} | {bet.selection}"
                f"{'' if bet.point is None else f' {round2(bet.point)}'} | odds {round2(bet.odds)}"
                f" | fair {round2(bet.fair_odds)} | edge {round2(bet.edge_pct)}%"
                f" | EV {round2(bet.ev_pct)}% | conf {round2(bet.confidence)}%"
            )
            lines.append(line)
        return "\n".join(lines)

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
