from __future__ import annotations

import re
from typing import Any

import httpx

from app.config import Settings
from app.schemas import CandidateBet
from app.utils import russian_market_name, russian_selection


class TelegramPublisher:
    def __init__(self, settings: Settings):
        self.settings = settings

    def render_message(self, bets: list[CandidateBet]) -> str:
        count = len(bets)
        min_books = max(1, int(getattr(self.settings, 'min_books_publish', 1) or 1))
        books_note = (
            'есть рыночное подтверждение; приоритет — совпадение как минимум у двух котировок, а исключения допускаются только при очень сильном сигнале и глубоком контексте.'
            if min_books <= 1
            else 'есть подтверждение как минимум по двум котировкам.'
        )
        header = f"🔥 {count} лучших ставок на ближайшие 48 часов\n\n"
        notes = (
            'Показываем только одиночные ставки. '
            'На один матч — не больше одной рекомендации. '
            f'В список попадают варианты, где модель видит перевес над линией и {books_note}'
        )
        blocks: list[str] = [header + notes]

        for idx, bet in enumerate(bets, start=1):
            selection_text = russian_selection(bet.family, bet.selection, bet.point)
            point_suffix = f" ({bet.point:g})" if bet.point is not None else ""
            start_text = bet.commence_time.astimezone(self.settings.tzinfo).strftime('%d.%m.%Y %H:%M МСК')
            xg_text = ""
            if bet.expected_home is not None and bet.expected_away is not None:
                xg_text = f"\n📈 Ожидаемые голы: {bet.expected_home:.2f} : {bet.expected_away:.2f}"
            used_text = "\n⚠️ Прогноз использован" if (bet.already_used and getattr(self.settings, 'telegram_writeup_show_used_marker', False)) else ""
            explanation = self._build_explanation(bet, selection_text)
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
                f"📝 Разбор:\n{explanation}"
            )

        return "\n\n".join(blocks)

    def _normalize_selection(self, value: str | None) -> str:
        text = (value or "").strip().lower()
        text = text.replace("ё", "е")
        text = re.sub(r"[^a-zа-я0-9+\-. ]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _selection_kind(self, family: str, selection: str | None, selection_text: str | None = None) -> str:
        raw = " ".join(
            part for part in [self._normalize_selection(selection), self._normalize_selection(selection_text)] if part
        )

        if family == "totals":
            if any(token in raw for token in ["over", "больше", "+", "tb", "тб"]):
                return "over"
            if any(token in raw for token in ["under", "меньше", "tm", "тм"]):
                return "under"
            return "unknown_total"

        if family == "btts":
            if any(token in raw for token in ["yes", "обе забьют да", "обе забьют", "да"]):
                return "yes"
            if any(token in raw for token in ["no", "обе забьют нет", "нет"]):
                return "no"
            return "unknown_btts"

        if family == "h2h":
            if any(token == raw or f" {token} " in f" {raw} " for token in ["home", "1", "п1"]):
                return "home"
            if any(token == raw or f" {token} " in f" {raw} " for token in ["away", "2", "п2"]):
                return "away"
            if any(token == raw or f" {token} " in f" {raw} " for token in ["draw", "x", "ничья"]):
                return "draw"
            return "unknown_h2h"

        if family == "spreads":
            if any(token == raw or f" {token} " in f" {raw} " for token in ["home", "1", "п1"]):
                return "home"
            if any(token == raw or f" {token} " in f" {raw} " for token in ["away", "2", "п2"]):
                return "away"
            return "unknown_spread"

        return "unknown"

    def _build_explanation(self, bet: CandidateBet, selection_text: str) -> str:
        analysis = dict(getattr(bet, 'analysis', {}) or {})
        blocks = analysis.get('analysis_blocks') or []
        if isinstance(blocks, list) and blocks:
            max_points = max(3, int(getattr(self.settings, 'telegram_writeup_max_points', 5) or 5))
            min_sample = max(0, int(getattr(self.settings, 'telegram_writeup_min_recent_sample', 3) or 3))
            selected: list[str] = []
            seen: set[str] = set()
            for block in sorted((item for item in blocks if isinstance(item, dict)), key=lambda item: (-float(item.get('priority', 0) or 0), str(item.get('tag', '')))):
                text = ' '.join(str(block.get('text') or '').split())
                if not text:
                    continue
                if text in seen:
                    continue
                tag = str(block.get('tag') or '')
                reliability = str(block.get('reliability') or 'medium')
                block_sample = int(block.get('min_sample') or 0)
                if reliability == 'low':
                    continue
                if tag in {'profile', 'recent', 'form'} and block_sample and block_sample < min_sample:
                    continue
                if tag == 'basis' and len(selected) >= 3:
                    continue
                seen.add(text)
                selected.append(text)
                if len(selected) >= max_points:
                    break
            if selected:
                return "\n\n".join(selected)

        summary_points = [str(item).strip() for item in (analysis.get('summary_points') or []) if str(item).strip()]
        if summary_points:
            return "\n\n".join(summary_points[: max(3, int(getattr(self.settings, 'telegram_writeup_max_points', 5) or 5))])

        raw_reasons = " ".join(bet.reasons).lower()
        parts: list[str] = []
        kind = self._selection_kind(bet.family, bet.selection, selection_text)

        if bet.family == "totals":
            if kind == "over":
                parts.append("Модель ждёт более результативный матч, чем это предполагает коэффициент.")
            elif kind == "under":
                parts.append("Модель ждёт менее результативный и более осторожный матч, чем это предполагает коэффициент.")
            else:
                parts.append("По тоталу модель видит перевес над текущим коэффициентом.")
        elif bet.family == "h2h":
            if kind == "home":
                parts.append(f"По нашим данным у {bet.home_team} есть перевес, а коэффициент всё ещё выглядит интересным.")
            elif kind == "away":
                parts.append(f"По нашим данным у {bet.away_team} есть перевес, а коэффициент всё ещё выглядит интересным.")
            elif kind == "draw":
                parts.append("Модель допускает более равный матч, чем это видно по линии.")
            else:
                parts.append("По исходу модель видит перевес над текущим коэффициентом.")
        else:
            parts.append("По модели этот вариант выглядит сильнее, чем его сейчас оценивает рынок.")

        if bet.expected_home is not None and bet.expected_away is not None:
            total_xg = bet.expected_home + bet.expected_away
            if bet.family == "totals" and kind == "over" and total_xg >= 2.6:
                parts.append("По ожидаемым голам матч тянет на открытую игру с моментами у обеих сторон.")
            elif bet.family == "totals" and kind == "under" and total_xg <= 2.2:
                parts.append("По ожидаемым голам матч больше похож на осторожную игру с небольшим числом моментов.")

        if "injuries" in raw_reasons:
            parts.append("Есть кадровые новости, которые могут заметно повлиять на игру.")
        if "form" in raw_reasons:
            parts.append("Текущая форма команд не противоречит этой ставке.")
        if "table" in raw_reasons:
            parts.append("Положение команд в таблице тоже поддерживает такой сценарий.")

        cleaned: list[str] = []
        seen: set[str] = set()
        for part in parts:
            key = part.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            cleaned.append(key)
        return "\n\n".join(cleaned[:3])

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
