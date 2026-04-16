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

    async def _send_message(self, message: str) -> tuple[int, list[str]]:
        if not str(message or "").strip():
            return 0, []
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

    def _timezone_label(self, value: Any) -> str:
        try:
            label = value.astimezone(self.settings.tzinfo).tzname()
        except Exception:
            label = None
        return str(label or getattr(self.settings, "app_timezone", "UTC"))

    def _money_suffix(
        self,
        bankroll_summary: dict[str, Any] | None = None,
        candidate: CandidateBet | None = None,
    ) -> str:
        currency = ""
        if bankroll_summary:
            currency = str(bankroll_summary.get("currency") or "").strip()
        if not currency and candidate is not None:
            currency = str(getattr(candidate, "bankroll_currency", "") or "").strip()
        if currency.lower() in {"", "u", "unit", "units"}:
            return ""
        return f" {currency}"

    def _format_money(
        self,
        value: float,
        bankroll_summary: dict[str, Any] | None = None,
        candidate: CandidateBet | None = None,
    ) -> str:
        return f"{float(value or 0.0):.2f}{self._money_suffix(bankroll_summary=bankroll_summary, candidate=candidate)}"

    def _format_outcome(self, value: str) -> str:
        mapping = {
            "won": "выигрыш",
            "half_won": "половина выигрыша",
            "lost": "проигрыш",
            "half_lost": "половина проигрыша",
            "push": "возврат",
            "void": "возврат",
        }
        return mapping.get(str(value or "").strip().lower(), str(value or "н/д"))

    def render_message(
        self,
        bets: list[CandidateBet],
        bankroll_summary: dict[str, Any] | None = None,
    ) -> str:
        count = len(bets)
        single_book_count = sum(1 for bet in bets if int(getattr(bet, "books_count", 0) or 0) <= 1)
        min_books = 1 if single_book_count else 2
        publish_window_hours = max(1, int(getattr(self.settings, "publish_window_hours", 48) or 48))
        books_note = (
            "есть рыночное подтверждение; приоритет — совпадение как минимум у двух котировок, а исключения допускаются только при очень сильном сигнале и глубоком контексте."
            if min_books <= 1
            else "есть подтверждение как минимум по двум котировкам."
        )

        bank_line = ""
        if bankroll_summary:
            current_bank = float(bankroll_summary.get("current_balance") or 0.0)
            open_exposure = float(bankroll_summary.get("open_exposure") or 0.0)
            available = float(bankroll_summary.get("available_balance") or max(0.0, current_bank - open_exposure))
            bank_line = (
                f"💼 Банк: {self._format_money(current_bank, bankroll_summary=bankroll_summary)} | "
                f"Открытый риск: {self._format_money(open_exposure, bankroll_summary=bankroll_summary)} | "
                f"Доступно: {self._format_money(available, bankroll_summary=bankroll_summary)}\n\n"
            )

        header = f"🔥 {self._best_bets_title(count, publish_window_hours)}\n\n" + bank_line
        notes = (
            "Показываем только одиночные ставки. "
            "На один матч — не больше одной рекомендации. "
            f"В список попадают варианты, где модель видит перевес над линией и {books_note}"
        )
        if single_book_count:
            notes += " Допускаем одиночные линии, когда free-tier не дает полного консенсуса, но сигнал модели остается рабочим."
        blocks: list[str] = [header + notes]

        for idx, bet in enumerate(bets, start=1):
            selection_text = russian_selection(bet.family, bet.selection, bet.point)
            point_suffix = f" ({bet.point:g})" if bet.point is not None else ""
            start_text = (
                f"{bet.commence_time.astimezone(self.settings.tzinfo).strftime('%d.%m.%Y %H:%M')} "
                f"{self._timezone_label(bet.commence_time)}"
            )
            xg_text = ""
            if bet.expected_home is not None and bet.expected_away is not None:
                xg_text = f"\n📈 Ожидаемые голы: {bet.expected_home:.2f} : {bet.expected_away:.2f}"
            stake_text = ""
            if float(getattr(bet, "stake_amount", 0.0) or 0.0) > 0:
                stake_text = (
                    f"\n💰 Сумма ставки: {self._format_money(bet.stake_amount, candidate=bet)} "
                    f"({bet.stake_pct:.2f}% от банка {self._format_money(bet.bankroll_snapshot, candidate=bet)})"
                )
            used_text = (
                "\n⚠️ Прогноз уже использовался"
                if bet.already_used and bool(getattr(self.settings, "telegram_writeup_show_used_marker", False))
                else ""
            )
            explanation = self._build_explanation(bet, selection_text)
            blocks.append(
                f"{idx}. {bet.home_team} — {bet.away_team}\n"
                f"🎯 Ставка: {russian_market_name(bet.family)} — {selection_text}{point_suffix}\n"
                f"💸 Коэффициент: {bet.odds:.2f}\n"
                f"📊 Вероятность по модели: {bet.adjusted_probability * 100:.1f}% | по линии (консенсус): {bet.market_probability * 100:.1f}%\n"
                f"✅ Уверенность: {bet.confidence:.1f}% | Букмекеров: {bet.books_count}\n"
                f"🏆 Турнир: {bet.league_name}\n"
                f"🕒 Начало: {start_text}"
                f"{stake_text}"
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

    def _selection_kind(
        self,
        family: str,
        selection: str | None,
        selection_text: str | None = None,
        selection_key: str | None = None,
    ) -> str:
        raw_key = str(selection_key or "").strip().lower()
        if family == "totals" and raw_key in {"over", "under"}:
            return raw_key
        if family == "btts" and raw_key in {"yes", "no"}:
            return raw_key
        if family in {"h2h", "spreads", "dnb"} and raw_key in {"home", "away", "draw"}:
            return raw_key

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

        if family in {"h2h", "spreads", "dnb"}:
            if any(token == raw or f" {token} " in f" {raw} " for token in ["home", "1", "п1"]):
                return "home"
            if any(token == raw or f" {token} " in f" {raw} " for token in ["away", "2", "п2"]):
                return "away"
            if family == "h2h" and any(token == raw or f" {token} " in f" {raw} " for token in ["draw", "x", "ничья"]):
                return "draw"
            return "unknown_side"

        return "unknown"

    def _build_explanation(self, bet: CandidateBet, selection_text: str) -> str:
        analysis = dict(getattr(bet, "analysis", {}) or {})
        summary_points = [str(item).strip() for item in (analysis.get("summary_points") or []) if str(item).strip()]
        if summary_points:
            return "\n\n".join(summary_points)

        raw_reasons = " ".join(bet.reasons).lower()
        parts: list[str] = []
        kind = self._selection_kind(
            bet.family,
            bet.selection,
            selection_text,
            getattr(bet, "selection_key", None),
        )

        if bet.family == "totals":
            if kind == "over":
                parts.append("Модель ждёт более результативный матч, чем это предполагает текущий коэффициент.")
            elif kind == "under":
                parts.append("Модель ждёт более осторожный матч и более низкий тотал, чем сейчас закладывает линия.")
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
        elif bet.family == "btts":
            if kind == "yes":
                parts.append("Обе команды создают достаточно моментов, чтобы сценарий с голами с двух сторон был вероятнее рынка.")
            elif kind == "no":
                parts.append("Модель ждёт менее открытый матч, чем предполагает рынок, и снижает шанс обмена голами.")
            else:
                parts.append("По рынку обе забьют модель видит перевес над текущей ценой.")
        else:
            parts.append("По модели этот вариант выглядит сильнее, чем его сейчас оценивает рынок.")

        if bet.expected_home is not None and bet.expected_away is not None:
            total_xg = bet.expected_home + bet.expected_away
            if bet.family == "totals" and kind == "over" and total_xg >= 2.6:
                parts.append("По ожидаемым голам матч тянет на открытую игру с моментами у обеих сторон.")
            elif bet.family == "totals" and kind == "under" and total_xg <= 2.2:
                parts.append("По ожидаемым голам матч больше похож на осторожную игру с небольшим числом моментов.")

        if "injuries" in raw_reasons:
            parts.append("Есть кадровые новости, которые могут заметно повлиять на рисунок игры.")
        if "form" in raw_reasons:
            parts.append("Текущая форма команд не противоречит этому сценарию.")
        if "table" in raw_reasons:
            parts.append("Положение команд в таблице тоже поддерживает такой сценарий матча.")

        cleaned: list[str] = []
        seen: set[str] = set()
        for part in parts:
            key = part.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            cleaned.append(key)
        return "\n\n".join(cleaned[:3])

    def render_settlement_summary(self, settlement_summary: dict[str, Any]) -> str | None:
        items = list(settlement_summary.get("items") or [])
        if not items:
            return None

        bankroll = dict(settlement_summary.get("bankroll") or {})
        lines = ["📒 Проверка завершённых ставок"]
        for idx, item in enumerate(items[:5], start=1):
            settlement = dict(item.get("settlement") or item)
            outcome = str(settlement.get("outcome") or "")
            emoji = "✅" if outcome in {"won", "half_won"} else "❌" if outcome in {"lost", "half_lost"} else "➖"
            score = None
            if settlement.get("final_home_goals") is not None and settlement.get("final_away_goals") is not None:
                score = f"{int(float(settlement['final_home_goals']))}:{int(float(settlement['final_away_goals']))}"
            point = item.get("point")
            point_suffix = f" ({float(point):g})" if point not in (None, "") else ""
            lines.append(
                f"{idx}. {item.get('home_team')} — {item.get('away_team')}\n"
                f"{emoji} Итог: {self._format_outcome(outcome)} | Счёт: {score or 'н/д'}\n"
                f"Ставка: {russian_market_name(str(item.get('family') or ''))} — "
                f"{russian_selection(str(item.get('family') or ''), str(item.get('selection') or ''), point)}{point_suffix} "
                f"@ {float(item.get('odds') or 0.0):.2f}\n"
                f"Сумма: {self._format_money(float(item.get('stake_amount') or 0.0), bankroll_summary=bankroll)} | "
                f"P&L: {float(settlement.get('pnl') or 0.0):+.2f}"
            )

        if bankroll:
            lines.append(
                f"💼 Банк: {self._format_money(float(bankroll.get('current_balance') or 0.0), bankroll_summary=bankroll)} | "
                f"Открытый риск: {self._format_money(float(bankroll.get('open_exposure') or 0.0), bankroll_summary=bankroll)} | "
                f"ROI: {float(bankroll.get('roi_pct') or 0.0):+.2f}%"
            )
        return "\n\n".join(lines)

    def _best_bets_title(self, count: int, publish_window_hours: int) -> str:
        mod10 = count % 10
        mod100 = count % 100
        if mod10 == 1 and mod100 != 11:
            noun = "лучшая ставка"
        elif mod10 in {2, 3, 4} and mod100 not in {12, 13, 14}:
            noun = "лучшие ставки"
        else:
            noun = "лучших ставок"
        return f"{count} {noun} на ближайшие {publish_window_hours} часов"

    async def publish_settlement_summary(self, settlement_summary: dict[str, Any]) -> tuple[int, list[str]]:
        if not getattr(self.settings, "settlement_send_telegram_summary", True):
            return 0, []
        message = self.render_settlement_summary(settlement_summary)
        if not message:
            return 0, []
        return await self._send_message(message)

    def render_daily_report(self, daily_report: dict[str, Any]) -> str | None:
        summary = dict(daily_report.get("summary") or {})
        rows = [dict(item) for item in (daily_report.get("rows") or []) if isinstance(item, dict)]
        if int(summary.get("total_bets") or 0) <= 0:
            return None

        bankroll = {"currency": summary.get("currency") or getattr(self.settings, "bankroll_currency", "units")}
        report_date = str(summary.get("report_date") or daily_report.get("report_date") or "")
        date_text = report_date
        try:
            year, month, day = report_date.split("-")
            date_text = f"{day}.{month}.{year}"
        except Exception:
            pass

        revenue = float(summary.get("revenue") or summary.get("pnl") or 0.0)
        roi_pct = float(summary.get("roi_pct") or 0.0)
        hit_rate_pct = float(summary.get("hit_rate_pct") or 0.0)
        lines = [
            f"📊 Итоги прогнозов за {date_text}",
            (
                f"Ставок: {int(summary.get('total_bets') or 0)} | "
                f"Закрыто: {int(summary.get('settled_bets') or 0)} | "
                f"В ожидании: {int(summary.get('pending_bets') or 0)}"
            ),
            (
                f"Зашло: {int(summary.get('won') or 0)} | "
                f"Не зашло: {int(summary.get('lost') or 0)} | "
                f"Возвраты: {int(summary.get('push') or 0) + int(summary.get('void') or 0)}"
            ),
            (
                f"Ставки: {self._format_money(float(summary.get('settled_stake') or 0.0), bankroll_summary=bankroll)} | "
                f"Выручка: {revenue:+.2f}{self._money_suffix(bankroll_summary=bankroll)} | "
                f"ROI: {roi_pct:+.2f}% | Проходимость: {hit_rate_pct:.1f}%"
            ),
        ]
        if bool(daily_report.get("is_revision")):
            lines.insert(1, "\U0001f501 \u041e\u0431\u043d\u043e\u0432\u043b\u0435\u043d\u043e: \u0437\u0430\u043a\u0440\u044b\u043b\u0438\u0441\u044c \u0441\u0442\u0430\u0432\u043a\u0438, \u043a\u043e\u0442\u043e\u0440\u044b\u0435 \u0440\u0430\u043d\u044c\u0448\u0435 \u0431\u044b\u043b\u0438 \u0432 \u043e\u0436\u0438\u0434\u0430\u043d\u0438\u0438.")
        pending_stake = float(summary.get("pending_stake") or 0.0)
        if pending_stake > 0:
            lines.append(f"Открытый остаток за день: {self._format_money(pending_stake, bankroll_summary=bankroll)}")

        quality_analysis = dict(daily_report.get("quality_analysis") or {})
        failure_tags = dict(quality_analysis.get("top_failure_tags") or {})
        if failure_tags:
            top_tags = ", ".join(f"{name}: {count}" for name, count in list(failure_tags.items())[:4])
            lines.append(f"Разбор ошибок: {top_tags}")

        result_emoji = {
            "won": "✅",
            "lost": "❌",
            "push": "➖",
            "void": "➖",
            "pending": "⏳",
        }
        item_lines: list[str] = []
        for idx, row in enumerate(rows[:8], start=1):
            result = str(row.get("result") or row.get("status") or "pending")
            emoji = result_emoji.get(result, "•")
            point = row.get("point")
            point_suffix = f" ({float(point):g})" if point not in (None, "") else ""
            score = str(row.get("final_score") or "н/д")
            pnl_raw = row.get("pnl")
            pnl_text = "ожидание" if pnl_raw in (None, "") else f"{float(pnl_raw):+.2f}{self._money_suffix(bankroll_summary=bankroll)}"
            family = str(row.get("family") or "")
            selection = str(row.get("selection") or "")
            item_lines.append(
                f"{idx}. {row.get('home_team')} — {row.get('away_team')}\n"
                f"{emoji} {russian_market_name(family)} — {russian_selection(family, selection, point)}{point_suffix} "
                f"@ {float(row.get('odds') or 0.0):.2f} | Счет: {score} | P&L: {pnl_text}"
            )
        if item_lines:
            lines.append("\n".join(item_lines))
        return "\n\n".join(lines)

    async def publish_daily_report(self, daily_report: dict[str, Any]) -> tuple[int, list[str]]:
        if not getattr(self.settings, "daily_report_send_telegram", True):
            return 0, []
        message = self.render_daily_report(daily_report)
        if not message:
            return 0, []
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

    def render_run_report(self, summary: dict[str, Any]) -> str | None:
        if not bool(getattr(self.settings, "run_report_enabled", True)):
            return None

        summary = dict(summary or {})
        source_stats = dict(summary.get("source_stats") or {})
        filtering = dict(summary.get("filtering") or {})
        bankroll = dict(summary.get("bankroll") or {})
        rejections = dict(summary.get("rejections") or {})

        def pick(*keys: str, default: Any = 0) -> Any:
            for key in keys:
                if summary.get(key) is not None:
                    return summary.get(key)
            for key in keys:
                if source_stats.get(key) is not None:
                    return source_stats.get(key)
            return default

        published = int(pick("published_to_telegram", "published", default=0) or 0)
        if bool(getattr(self.settings, "run_report_only_when_no_predictions", True)) and published > 0:
            return None

        matches_seen = int(pick("matches_seen", default=0) or 0)
        matches_with_offers = int(pick("matches_with_offers", default=0) or 0)
        contexts_built = int(pick("contexts_built", default=0) or 0)
        candidates_before_quality = int(pick("candidates_before_quality", default=0) or 0)
        candidates_raw = int(pick("candidates_raw", default=0) or 0)
        candidates_publishable = int(pick("candidates_publishable", default=0) or 0)

        labels = {
            "insufficient_books": "мало подтверждённых котировок",
            "unsupported_total_line": "unsupported total line",
            "missing_context_totals": "не хватает контекста по тоталам",
            "missing_context_h2h": "не хватает контекста по исходам",
            "missing_context_spreads": "не хватает контекста по форам",
            "confidence_below_threshold": "недобор по уверенности модели",
            "ev_below_threshold": "недобор по EV",
            "no_candidate_for_match": "no candidate for match",
        }
        quality_labels = {
            "bad_historical_segment_guard": "historical guard",
            "historical_guard": "historical guard",
            "post_calibration_probability_guard": "post-calibration probability guard",
        }
        guidance_labels = {
            "insufficient_books": "расширить покрытие линий у букмекеров или снизить минимальное число подтверждённых офферов в настройках",
            "missing_context_totals": "увеличить глубину статистики по тоталам (форма, xG, темп) для матчей в текущем окне",
            "missing_context_spreads": "добавить контекст по форам (рейтинги силы, травмы, составы) перед запуском",
            "missing_context_h2h": "пополнить контекст по исходам (форма, мотивация, кадровые новости)",
            "confidence_below_threshold": "проверить калибровку модели или временно ослабить порог confidence",
            "ev_below_threshold": "пересмотреть порог EV либо расширить пул рынков с валидными офферами",
            "quality_historical_guard": "ослабить historical guard или накопить больше исторических данных для сегмента",
            "quality_post_calibration_probability_guard": "перепроверить калибровку вероятностей после quality-фильтров",
        }

        top_reasons: list[tuple[str, int]] = []
        quality_reasons: list[tuple[str, int]] = []
        for key, value in sorted(rejections.items(), key=lambda item: int(item[1] or 0), reverse=True):
            count = int(value or 0)
            if count <= 0:
                continue
            if str(key).startswith("quality_"):
                quality_reasons.append((str(key)[8:], count))
                continue
            top_reasons.append((str(key), count))

        top_limit = max(1, int(getattr(self.settings, "run_report_top_reasons", 4) or 4))
        lines = [
            "🧾 Отчёт по запуску бота",
            f"🕒 Время запуска: {summary.get('current_time_local') or summary.get('current_time_utc') or 'н/д'}",
            f"📅 Окно публикации: {filtering.get('publish_window_hours', getattr(self.settings, 'publish_window_hours', 'н/д'))} ч | Мин. запас до матча: {filtering.get('min_kickoff_lead_minutes', getattr(self.settings, 'min_kickoff_lead_minutes', 'н/д'))} мин",
            f"⚽ Матчей в окне: {matches_seen} | С офферами: {matches_with_offers} | Контекстов: {contexts_built}",
            f"🧠 Кандидаты: до quality {candidates_before_quality} | после quality {candidates_raw} | к публикации {candidates_publishable}",
        ]
        if bankroll:
            lines.append(
                f"💼 Банк: {self._format_money(float(bankroll.get('current_balance') or 0.0), bankroll_summary=bankroll)} | Открытый риск: {self._format_money(float(bankroll.get('open_exposure') or 0.0), bankroll_summary=bankroll)}"
            )
        lines.append("❌ В этот запуск прогнозов не было." if published <= 0 else f"✅ В этот запуск опубликовано прогнозов: {published}")
        if top_reasons:
            lines.append("Почему нет прогноза:")
            for key, count in top_reasons[:top_limit]:
                lines.append(f"• {labels.get(key, key.replace('_', ' '))} — {count}")
        if quality_reasons:
            lines.append("Quality стопоры:")
            for key, count in quality_reasons[:4]:
                lines.append(f"• quality: {quality_labels.get(key, key.replace('_', ' '))} — {count}")
        if published <= 0:
            guidance: list[str] = []
            for key, _ in top_reasons[:top_limit]:
                tip = guidance_labels.get(key)
                if tip:
                    guidance.append(tip)
            for key, _ in quality_reasons[:4]:
                tip = guidance_labels.get(f"quality_{key}")
                if tip:
                    guidance.append(tip)
            if guidance:
                lines.append("Что можно улучшить к следующему запуску:")
                for tip in list(dict.fromkeys(guidance))[:3]:
                    lines.append(f"• {tip}")
        return "\n".join(lines)

    async def publish_run_report(self, summary: dict[str, Any]) -> tuple[int, list[str]]:
        message = self.render_run_report(summary)
        if not message:
            return 0, []
        return await self._send_message(message)

    async def publish(self, bets: list[CandidateBet], bankroll_summary: dict[str, Any] | None = None) -> tuple[int, list[str]]:
        if not bets:
            return 0, []

        message = self.render_message(bets, bankroll_summary=bankroll_summary)
        return await self._send_message(message)
