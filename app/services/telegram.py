from __future__ import annotations

import httpx

from app.config import Settings
from app.schemas import CandidateBet
from app.utils import russian_market_name, russian_selection


class TelegramPublisher:
    def __init__(self, settings: Settings):
        self.settings = settings

    def render_message(self, bets: list[CandidateBet]) -> str:
        count = len(bets)
        header = f"🔥 {count} лучших ставок на ближайшие 48 часов\n\n"
        notes = (
            "Показываем только одиночные ставки. "
            "На один матч — не больше одной рекомендации. "
            "В список попадают варианты, где модель видит перевес над линией и есть подтверждение как минимум по двум котировкам."
        )
        blocks: list[str] = [header + notes]

        for idx, bet in enumerate(bets, start=1):
            selection_text = russian_selection(bet.family, bet.selection, bet.point)
            point_suffix = f" ({bet.point:g})" if bet.point is not None else ""
            start_text = bet.commence_time.astimezone(self.settings.tzinfo).strftime('%d.%m.%Y %H:%M МСК')
            xg_text = ""
            if bet.expected_home is not None and bet.expected_away is not None:
                xg_text = f"\n📈 Ожидаемые голы: {bet.expected_home:.2f} : {bet.expected_away:.2f}"
            used_text = "\n⚠️ Прогноз использован" if bet.already_used else ""
            explanation = self._build_explanation(bet)
            blocks.append(
                f"{idx}. {bet.home_team} — {bet.away_team}\n"
                f"🎯 Ставка: {russian_market_name(bet.family)} — {selection_text}{point_suffix}\n"
                f"💸 Коэффициент: {bet.odds:.2f}\n"
                f"📊 Вероятность по модели: {bet.adjusted_probability * 100:.1f}% | по линии: {bet.market_probability * 100:.1f}%\n"
                f"✅ Уверенность: {bet.confidence:.1f}% | Букмекеров: {bet.books_count}\n"
                f"🏆 Турнир: {bet.league_name}\n"
                f"🕒 Начало: {start_text}"
                f"{xg_text}"
                f"{used_text}\n"
                f"📝 Почему ставка интересна: {explanation}"
            )

        return "\n\n".join(blocks)

    def _build_explanation(self, bet: CandidateBet) -> str:
        raw_reasons = " ".join(bet.reasons).lower()
        parts: list[str] = []

        if bet.family == "totals":
            if "over" in bet.selection.lower():
                parts.append("Модель ждёт более результативный матч, чем это предполагает коэффициент.")
            else:
                parts.append("Модель ждёт более закрытый матч, чем это предполагает коэффициент.")
        elif bet.family == "h2h":
            team = bet.home_team if bet.selection.lower() == "home" else bet.away_team if bet.selection.lower() == "away" else "этот исход"
            parts.append(f"По нашим данным у {team} есть перевес, а коэффициент всё ещё остаётся интересным.")
        elif bet.family == "spreads":
            parts.append("Модель видит запас по форе и считает линию чуть мягче, чем должна быть.")
        elif bet.family == "btts":
            if "yes" in bet.selection.lower():
                parts.append("Есть хорошие шансы, что обе команды забьют.")
            else:
                parts.append("Есть причины ждать, что хотя бы одна команда останется без гола.")
        else:
            parts.append("По модели этот вариант выглядит сильнее, чем его оценивает рынок.")

        if bet.expected_home is not None and bet.expected_away is not None:
            total_xg = bet.expected_home + bet.expected_away
            if bet.family == "totals" and "over" in bet.selection.lower() and total_xg >= 2.6:
                parts.append("По ожидаемым голам матч тянет на открытую игру с моментами у обеих сторон.")
            elif bet.family == "totals" and "under" in bet.selection.lower() and total_xg <= 2.2:
                parts.append("По ожидаемым голам матч больше похож на осторожную игру с небольшим числом моментов.")
            elif bet.family == "h2h":
                if bet.expected_home > bet.expected_away + 0.25:
                    parts.append(f"{bet.home_team} должен создавать больше опасных моментов.")
                elif bet.expected_away > bet.expected_home + 0.25:
                    parts.append(f"{bet.away_team} должен создавать больше опасных моментов.")

        if "form" in raw_reasons:
            parts.append("Текущая форма команд не противоречит этой ставке.")
        if "table" in raw_reasons:
            parts.append("Положение команд в таблице тоже поддерживает такой сценарий.")
        if "injuries" in raw_reasons:
            parts.append("Есть кадровые новости, которые могут заметно повлиять на игру.")
        if "news" in raw_reasons:
            parts.append("Свежий новостной фон не ломает идею ставки.")
        if "xg" in raw_reasons and all("ожидаемым голам" not in p for p in parts):
            parts.append("По качеству создаваемых моментов ставка выглядит логично.")

        cleaned: list[str] = []
        seen: set[str] = set()
        for part in parts:
            key = part.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            cleaned.append(key)
            if len(cleaned) >= 3:
                break
        return " ".join(cleaned)

    async def publish(self, bets: list[CandidateBet]) -> tuple[int, list[str]]:
        if not bets:
            return 0, []

        message = self.render_message(bets)
        if self.settings.publish_dry_run or not self.settings.telegram_token or not self.settings.telegram_chat_id:
            return 0, [message]

        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.post(
                f"https://api.telegram.org/bot{self.settings.telegram_token}/sendMessage",
                json={
                    "chat_id": self.settings.telegram_chat_id,
                    "text": message,
                    "disable_web_page_preview": True,
                },
            )
            response.raise_for_status()
            return 1, [message]
