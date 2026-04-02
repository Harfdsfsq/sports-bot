from __future__ import annotations

from datetime import UTC
from html import escape
from typing import Any, Iterable
from zoneinfo import ZoneInfo

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
        count = len(candidates)
        lines = [
            f"🔥 <b>{count} лучших валуйных ставок на ближайшие 48 часов</b>",
            "",
            "В выдачу попадают только одиночные ставки с рыночным подтверждением, модельной оценкой и фильтром качества.",
            "На один матч — не более одной ставки. Мультиставки внутри одного матча отключены как слишком коррелированные.",
            "",
        ]

        for idx, bet in enumerate(candidates, start=1):
            if idx > 1:
                lines.append("")
            lines.extend(self._render_bet_block(bet, idx))

        if count < self.settings.max_picks_per_run:
            lines.extend(
                [
                    "",
                    "ℹ️ <i>Ставок меньше лимита, потому что остальные кандидаты не прошли фильтры качества, цены и рыночного подтверждения.</i>",
                ]
            )

        return "\n".join(lines)

    def _render_bet_block(self, bet: CandidateBet, number: int) -> list[str]:
        sport_label = self._sport_label(bet.sport_key)
        match_date = self._format_dt(bet.commence_time)
        market_label = self._market_label(bet.family)
        selection = escape(self._display_selection(bet))
        line = ""
        if bet.point is not None:
            line = f" <b>({self._fmt_num(bet.point, 2)})</b>"

        source_label = self._best_source_label(bet)
        headline = self._analysis_headline(bet)
        analysis_text = self._analysis_body(bet)
        factors = self._factor_lines(bet)
        note = self._market_note(bet)

        parts = [
            f"{sport_label} <b>{escape(self._sport_name(bet.sport_key))}. {escape(bet.league_name)}</b>",
            f"<b>{number}. {escape(bet.home_team)} - {escape(bet.away_team)}</b>",
            "",
            f"🎯 <b>{escape(market_label)}</b>",
            f"Прогноз: <b>{selection}</b>{line}",
            (
                f"💸 Коэффициент: <b>{self._fmt_num(bet.odds, 2)}</b> | "
                f"EV: <b>{self._fmt_pct(bet.ev_pct, 2)}</b> | "
                f"Edge: <b>{self._fmt_pct(bet.edge_pct, 2)}</b>"
            ),
            (
                f"📊 Модель: <b>{self._fmt_pct(self._to_percent(bet.model_probability), 1)}</b> | "
                f"скорр.: <b>{self._fmt_pct(self._to_percent(bet.adjusted_probability), 1)}</b> | "
                f"линия: <b>{self._fmt_pct(self._to_percent(bet.market_probability), 1)}</b>"
            ),
            f"🧠 Уверенность: <b>{self._fmt_pct(bet.confidence, 1)}</b> | Источник: <b>{escape(source_label)}</b>",
            f"🕒 Начало: <b>{escape(match_date)}</b>",
            "",
            f"🏷 <b>{escape(headline)}</b>",
            escape(analysis_text),
        ]

        if factors:
            parts.append("")
            parts.append("📌 <b>Ключевые факторы:</b>")
            parts.extend(f"• {escape(item)}" for item in factors)

        parts.append("")
        parts.append(f"📝 <i>{escape(note)}</i>")
        return parts

    def _analysis_body(self, bet: CandidateBet) -> str:
        paragraphs: list[str] = []

        if bet.expected_home is not None or bet.expected_away is not None:
            home = self._fmt_num(bet.expected_home or 0.0, 2)
            away = self._fmt_num(bet.expected_away or 0.0, 2)
            total = self._fmt_num((bet.expected_home or 0.0) + (bet.expected_away or 0.0), 2)
            paragraphs.append(
                f"Модельный сценарий: xG {home} : {away}, ожидаемый тотал около {total}."
            )

        consensus_bits: list[str] = []
        if bet.books_count:
            consensus_bits.append(f"подтверждение по рынку от {bet.books_count} букмекеров")
        if bet.sources_count:
            consensus_bits.append(f"источников данных: {bet.sources_count}")
        if consensus_bits:
            paragraphs.append("Рыночное подтверждение: " + "; ".join(consensus_bits) + ".")

        if bet.model_mode:
            mode_text = {
                "market_only": "Кандидат прошёл как market-backed вариант без полного внешнего контекста.",
                "model_plus_market": "Кандидат подтверждён и моделью, и рыночной оценкой.",
                "context_plus_market": "Кандидат подтверждён матчевым контекстом и рынком.",
            }.get(bet.model_mode)
            if mode_text:
                paragraphs.append(mode_text)

        reasons = [self._clean_reason(item) for item in bet.reasons if self._clean_reason(item)]
        if reasons:
            paragraphs.append("Ключевой сценарий: " + "; ".join(reasons[:3]) + ".")

        if not paragraphs:
            paragraphs.append(
                "Ставка прошла по цене, модельной вероятности и рыночному подтверждению, но расширенный матчевый контекст для этого кандидата не был собран."
            )

        return " ".join(paragraphs)

    def _factor_lines(self, bet: CandidateBet) -> list[str]:
        factors: list[str] = []

        if bet.expected_home is not None or bet.expected_away is not None:
            factors.append(
                f"модель ожидает xG {self._fmt_num(bet.expected_home or 0.0, 2)} - {self._fmt_num(bet.expected_away or 0.0, 2)}"
            )
            factors.append(
                f"ожидаемый тотал около {self._fmt_num((bet.expected_home or 0.0) + (bet.expected_away or 0.0), 2)}"
            )

        factors.append(f"рыночное подтверждение от {bet.books_count} букмекеров")
        factors.append(f"источников в расчёте: {bet.sources_count}")
        factors.append(f"модельный режим: {bet.model_mode}")

        for reason in bet.reasons:
            clean = self._clean_reason(reason)
            if clean and clean not in factors:
                factors.append(clean)
            if len(factors) >= 6:
                break

        return factors[:6]

    def _market_note(self, bet: CandidateBet) -> str:
        bits = [
            f"линия выше fair-оценки {self._fmt_num(bet.fair_odds, 2)}",
            f"edge {self._fmt_pct(bet.edge_pct, 2)}",
            f"EV {self._fmt_pct(bet.ev_pct, 2)}",
        ]
        if bet.books_count:
            bits.append(f"книг в подтверждении: {bet.books_count}")
        return "; ".join(bits)

    def _best_source_label(self, bet: CandidateBet) -> str:
        summary = bet.source_summary or {}
        if isinstance(summary, dict) and summary:
            ranked = []
            for name, payload in summary.items():
                if isinstance(payload, dict):
                    score = payload.get("offers") or payload.get("weight") or payload.get("count") or 0
                else:
                    score = 0
                ranked.append((float(score or 0), str(name)))
            ranked.sort(reverse=True)
            if ranked:
                return self._source_name(ranked[0][1])

        diagnostics = bet.diagnostics or {}
        source_name = diagnostics.get("best_source") or diagnostics.get("source")
        if source_name:
            return self._source_name(str(source_name))

        return "Model"

    @staticmethod
    def _source_name(name: str) -> str:
        mapping = {
            "bookies_api": "BookiesApi",
            "the_odds_api": "TheOddsApi",
            "odds_api_io": "OddsApiIO",
            "sstats": "SStats",
        }
        return mapping.get(name, name)

    @staticmethod
    def _sport_label(sport_key: str) -> str:
        return {
            "soccer": "⚽️",
            "basketball": "🏀",
            "baseball": "⚾️",
            "icehockey": "🏒",
        }.get(sport_key, "🏟️")

    @staticmethod
    def _sport_name(sport_key: str) -> str:
        return {
            "soccer": "Футбол",
            "basketball": "Баскетбол",
            "baseball": "Бейсбол",
            "icehockey": "Хоккей",
        }.get(sport_key, sport_key)

    @staticmethod
    def _market_label(family: str) -> str:
        return {
            "h2h": "Исход",
            "totals": "Тотал",
            "spreads": "Фора",
            "dnb": "Победа без ничьи",
            "doubleChance": "Двойной шанс",
            "btts": "Обе забьют",
            "teamTotals": "Индивидуальный тотал",
        }.get(family, family)

    def _analysis_headline(self, bet: CandidateBet) -> str:
        return {
            "h2h": "Прогноз на исход",
            "totals": "Прогноз на тотал",
            "spreads": "Прогноз на фору",
            "dnb": "Прогноз на победу без ничьи",
            "doubleChance": "Прогноз на двойной шанс",
            "btts": "Прогноз на обе забьют",
            "teamTotals": "Прогноз на индивидуальный тотал",
        }.get(bet.family, "Анализ ставки")

    def _display_selection(self, bet: CandidateBet) -> str:
        if bet.family == "h2h":
            if bet.selection.lower() == "draw":
                return "Ничья"
            return bet.selection
        if bet.family in {"totals", "teamTotals"}:
            if bet.selection.lower() == "over":
                return f"ТБ {self._fmt_num(bet.point or 0.0, 2)}"
            if bet.selection.lower() == "under":
                return f"ТМ {self._fmt_num(bet.point or 0.0, 2)}"
        if bet.family == "btts":
            return "Обе забьют — Да" if bet.selection.lower() == "yes" else "Обе забьют — Нет"
        if bet.family == "doubleChance":
            return bet.selection.upper()
        return bet.selection

    def _format_dt(self, value) -> str:
        try:
            zone = ZoneInfo(self.settings.app_timezone)
        except Exception:
            zone = UTC
        return value.astimezone(zone).strftime("%d.%m.%Y %H:%M")

    @staticmethod
    def _fmt_num(value: float, digits: int = 2) -> str:
        rounded = round2(value, digits)
        text = f"{rounded:.{digits}f}"
        if digits > 0:
            text = text.rstrip("0").rstrip(".")
        return text.replace(".", ",")

    @classmethod
    def _fmt_pct(cls, value: float, digits: int = 2) -> str:
        return f"{cls._fmt_num(value, digits)}%"

    @staticmethod
    def _to_percent(value: float) -> float:
        if value <= 1.0:
            return value * 100.0
        return value

    @staticmethod
    def _clean_reason(text: str) -> str:
        return " ".join(str(text or "").replace("_", " ").split()).strip(" .")

    @staticmethod
    def _split_message(message: str, limit: int) -> list[str]:
        if len(message) <= limit:
            return [message]

        parts: list[str] = []
        current: list[str] = []
        current_len = 0

        for line in message.splitlines():
            line_len = len(line) + 1
            if current and current_len + line_len > limit:
                parts.append("\n".join(current))
                current = [line]
                current_len = line_len
            else:
                current.append(line)
                current_len += line_len

        if current:
            parts.append("\n".join(current))
        return parts
