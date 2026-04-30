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
        text = str(message or "").strip()
        if not text:
            return 0, []
        parts = self._split_message(text)
        if self.settings.publish_dry_run or not self.settings.telegram_token or not self.settings.telegram_chat_id:
            return 0, parts

        sent = 0
        async with httpx.AsyncClient(timeout=20.0) as client:
            for part in parts:
                response = await client.post(
                    f"https://api.telegram.org/bot{self.settings.telegram_token}/sendMessage",
                    json={
                        "chat_id": self.settings.telegram_chat_id,
                        "text": part,
                        "disable_web_page_preview": True,
                    },
                )
                response.raise_for_status()
                sent += 1
        return sent, parts

    def _split_message(self, message: str, limit: int = 3900) -> list[str]:
        text = str(message or "").strip()
        if len(text) <= limit:
            return [text]

        chunks: list[str] = []
        current = ""
        for block in text.split("\n\n"):
            candidate = block.strip()
            if not candidate:
                continue
            if not current:
                current = candidate
                continue
            merged = f"{current}\n\n{candidate}"
            if len(merged) <= limit:
                current = merged
                continue
            chunks.append(current)
            current = candidate

        if current:
            chunks.append(current)

        final_chunks: list[str] = []
        for chunk in chunks:
            if len(chunk) <= limit:
                final_chunks.append(chunk)
                continue
            for i in range(0, len(chunk), limit):
                final_chunks.append(chunk[i:i + limit])
        return final_chunks

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

    def _consensus_probability(self, bet: CandidateBet) -> float:
        consensus = float(getattr(bet, "consensus_probability", 0.0) or 0.0)
        market = float(getattr(bet, "market_probability", 0.0) or 0.0)
        return consensus if 0.0 < consensus < 1.0 else market

    def _raw_model_probability(self, bet: CandidateBet) -> float:
        raw = float(getattr(bet, "model_probability", 0.0) or 0.0)
        adjusted = float(getattr(bet, "adjusted_probability", 0.0) or 0.0)
        return raw if 0.0 < raw < 1.0 else adjusted

    def _show_probability_breakdown(self, bet: CandidateBet) -> bool:
        raw = self._raw_model_probability(bet)
        adjusted = float(getattr(bet, "adjusted_probability", 0.0) or 0.0)
        family = str(getattr(bet, "family", "") or "").strip().lower()
        if family in {"spreads", "dnb"}:
            return True
        return abs(raw - adjusted) >= 0.04

    def _market_name_display(self, family: str) -> str:
        family_key = str(family or "").strip()
        if family_key in {"spreads", "dnb"}:
            return "Фора"
        return russian_market_name(family_key)

    def _format_signed_point(self, point: float | None, default: float = 0.0) -> str:
        value = default if point in (None, "") else float(point)
        if abs(value) < 1e-9:
            return "0"
        text = f"{value:g}"
        return text if text.startswith("-") else f"+{text}"

    def _compact_selection_display(
        self,
        family: str,
        selection: str | None,
        point: float | None = None,
        team_side: str | None = None,
        home_team: str | None = None,
        away_team: str | None = None,
        selection_key: str | None = None,
    ) -> str:
        family_key = str(family or "").strip()
        kind = self._selection_kind(family_key, selection, str(selection or ""), selection_key)

        if family_key == "h2h":
            if kind == "home":
                return "П1"
            if kind == "away":
                return "П2"
            if kind == "draw":
                return "Ничья"
            return str(selection or "").strip()

        if family_key in {"spreads", "dnb"}:
            side = str(team_side or "").strip().lower()
            if not side:
                side = kind if kind in {"home", "away"} else ""
            side_code = "1" if side == "home" else "2" if side == "away" else ""
            if side_code:
                return f"Ф{side_code}({self._format_signed_point(point, default=0.0)})"
            return str(selection or "").strip()

        if family_key == "totals":
            if kind == "over":
                return f"ТБ({float(point):g})" if point not in (None, "") else "ТБ"
            if kind == "under":
                return f"ТМ({float(point):g})" if point not in (None, "") else "ТМ"
            return str(selection or "").strip()

        if family_key == "teamTotals":
            side = str(team_side or "").strip().lower()
            if not side:
                side = "home" if self._team_side_label(team_side, home_team, away_team) == str(home_team or "").strip() else "away"
            side_code = "1" if side == "home" else "2" if side == "away" else ""
            if kind == "over" and side_code:
                return f"ИТБ{side_code}({float(point):g})" if point not in (None, "") else f"ИТБ{side_code}"
            if kind == "under" and side_code:
                return f"ИТМ{side_code}({float(point):g})" if point not in (None, "") else f"ИТМ{side_code}"
            return str(selection or "").strip()

        if family_key == "doubleChance":
            normalized = self._normalize_selection(selection).replace(" ", "")
            if normalized in {"1x", "x1", "homeordraw"}:
                return "1X"
            if normalized in {"x2", "2x", "awayordraw"}:
                return "X2"
            if normalized in {"12", "nodraw"}:
                return "12"
            return str(selection or "").strip()

        if family_key == "btts":
            if kind == "yes":
                return "Да"
            if kind == "no":
                return "Нет"
        return str(selection or "").strip()

    def _row_selection_display(self, row: dict[str, Any]) -> str:
        return self._compact_selection_display(
            str(row.get("family") or ""),
            str(row.get("selection") or ""),
            row.get("point"),
            row.get("team_side"),
            row.get("home_team"),
            row.get("away_team"),
            str(row.get("selection_key") or ""),
        )

    def _shrink_delta_pp(self, bet: CandidateBet) -> float:
        return abs(self._raw_model_probability(bet) - float(getattr(bet, "adjusted_probability", 0.0) or 0.0)) * 100.0

    def _bookmakers_text(self, bet: CandidateBet) -> str:
        summary = dict(getattr(bet, "source_summary", {}) or {})
        raw = (
            summary.get("selected_bookmakers")
            or summary.get("consensus_bookmakers")
            or summary.get("books")
            or summary.get("bookmakers")
            or []
        )
        names: list[str] = []
        seen: set[str] = set()
        for item in raw:
            text = str(item).strip()
            key = text.lower()
            if not text or key in seen:
                continue
            seen.add(key)
            names.append(text)
        if names:
            return ", ".join(names[:4])
        fallback = str(summary.get("selected_bookmaker") or getattr(bet, "bookmaker", "") or "").strip()
        return fallback

    def _fair_consensus_odds(self, bet: CandidateBet) -> float | None:
        consensus_prob = self._consensus_probability(bet)
        if consensus_prob <= 0.0 or consensus_prob >= 1.0:
            return None
        fair_odds = 1.0 / consensus_prob
        if fair_odds <= 1.0:
            return None
        return fair_odds

    def _price_vs_consensus_pct(self, bet: CandidateBet) -> float | None:
        fair_odds = self._fair_consensus_odds(bet)
        if fair_odds is None or float(bet.odds) <= 1.0:
            return None
        return (float(bet.odds) - fair_odds) * 100.0 / fair_odds

    def _normalize_selection(self, value: str | None) -> str:
        text = (value or "").strip().lower()
        text = text.replace("ё", "е")
        text = re.sub(r"[^a-zа-я0-9+\-. ]+", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
        return text

    def _team_side_label(self, team_side: str | None, home_team: str | None = None, away_team: str | None = None) -> str:
        side = str(team_side or "").strip().lower()
        if side == "home":
            return str(home_team or "Хозяева").strip()
        if side == "away":
            return str(away_team or "Гости").strip()
        return ""

    def _selection_kind(
        self,
        family: str,
        selection: str | None,
        selection_text: str | None = None,
        selection_key: str | None = None,
    ) -> str:
        raw_key = str(selection_key or "").strip().lower()
        if family in {"totals", "teamTotals"} and raw_key in {"over", "under"}:
            return raw_key
        if family == "btts" and raw_key in {"yes", "no"}:
            return raw_key
        if family in {"h2h", "spreads", "dnb"} and raw_key in {"home", "away", "draw"}:
            return raw_key

        raw = " ".join(
            part for part in [self._normalize_selection(selection), self._normalize_selection(selection_text)] if part
        )

        if family in {"totals", "teamTotals"}:
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

    def _league_bucket(self, league_name: str | None) -> str:
        if self.settings.is_preferred_league(league_name):
            return "preferred"
        if self.settings.is_secondary_league(league_name):
            return "secondary"
        if self.settings.is_low_tier_league(league_name):
            return "low"
        return "other"

    def _trust_profile(self, bet: CandidateBet) -> dict[str, Any]:
        summary = dict(getattr(bet, "source_summary", {}) or {})
        quality_score = float(summary.get("quality_score") or 0.0)
        books_count = int(getattr(bet, "books_count", 0) or 0)
        sources_count = int(getattr(bet, "sources_count", 0) or 0)
        confidence = float(getattr(bet, "confidence", 0.0) or 0.0)
        league_bucket = self._league_bucket(getattr(bet, "league_name", None))
        shrink_pp = self._shrink_delta_pp(bet)

        score = quality_score * 0.90
        score += min(10.0, max(0, books_count - 1) * 5.0)
        score += min(6.0, max(0, sources_count - 1) * 3.0)
        score += max(0.0, min(8.0, (confidence - 60.0) * 0.30))

        risk_flags: list[str] = []
        if books_count <= 1:
            score -= 6.0
            risk_flags.append("single-book")
        if sources_count <= 1:
            score -= 10.0
            risk_flags.append("single-source")
        if league_bucket in {"other", "low"}:
            score -= 4.0
            risk_flags.append("non-core")
        if shrink_pp >= 12.0:
            score -= min(8.0, (shrink_pp - 12.0) * 0.6 + 1.5)
            risk_flags.append("heavy-shrink")

        score = max(0.0, min(100.0, round(score, 1)))
        if sources_count <= 1:
            score = min(score, 82.0)
        if sources_count <= 1 and league_bucket in {"other", "low"}:
            score = min(score, 74.0)
        if books_count <= 1 and sources_count <= 1:
            score = min(score, 74.0)
        if shrink_pp >= 16.0:
            score = min(score, 78.0)

        grade = "A" if score >= 82 else "B" if score >= 64 else "C"
        return {
            "grade": grade,
            "score": score,
            "quality_score": round(quality_score, 1),
            "books_count": books_count,
            "sources_count": sources_count,
            "risk_flags": risk_flags,
        }

    def _trust_profile_text(self, bet: CandidateBet) -> str:
        if not bool(getattr(self.settings, "telegram_writeup_show_trust_profile", True)):
            return ""
        trust = self._trust_profile(bet)
        parts = [
            f"🛡 Профиль сигнала: {trust['grade']} {trust['score']:.1f}/100",
            f"quality {trust['quality_score']:.1f}",
            f"линии {trust['books_count']}",
            f"источники {trust['sources_count']}",
        ]
        text = " | ".join(parts)
        if trust["risk_flags"]:
            text += f" | риск: {', '.join(trust['risk_flags'][:3])}"
        return text

    def _quality_notes(self, bet: CandidateBet) -> str:
        summary = dict(getattr(bet, "source_summary", {}) or {})
        status = str(summary.get("quality_status") or "").strip().lower()
        reasons = [str(item).strip().lower() for item in (summary.get("quality_reasons") or [])]
        notes: list[str] = []

        if status == "passed_quality_emergency" or "quality_emergency_publish" in reasons:
            notes.append("⚠️ Режим качества: аварийная публикация, сигнал пограничный.")
        elif status == "passed_quality_historical_relief" or "quality_historical_guard_relief" in reasons:
            notes.append("⚠️ Режим качества: публикация с послаблением по историческому фильтру.")
        elif status == "passed_quality_last_resort" or "quality_last_resort_publish" in reasons:
            notes.append("⚠️ Режим качества: запасной проход после quality-стопоров.")

        if int(getattr(bet, "books_count", 0) or 0) <= 1:
            notes.append("⚠️ Рыночное подтверждение слабое: линия только у одного букмекера.")

        if self._league_bucket(getattr(bet, "league_name", None)) in {"other", "low"}:
            notes.append("⚠️ Лига вне core-пула, для таких матчей риск ошибки выше.")

        return "\n".join(notes[:2])

    def render_message(
        self,
        bets: list[CandidateBet],
        bankroll_summary: dict[str, Any] | None = None,
    ) -> str:
        count = len(bets)
        single_book_count = sum(1 for bet in bets if int(getattr(bet, "books_count", 0) or 0) <= 1)
        publish_window_hours = max(1, int(getattr(self.settings, "publish_window_hours", 48) or 48))
        books_note = (
            "есть подтверждение по рыночным котировкам; приоритет — варианты с 2+ букмекерами, "
            "а одиночные линии допускаются только при сильном сигнале модели и хорошем контексте."
            if single_book_count
            else "есть подтверждение как минимум у двух букмекеров."
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

        header = (
            f"🔥 {count} лучшая ставка на ближайшие {publish_window_hours} часов\n\n"
            if count == 1
            else f"🔥 {count} лучшие ставки на ближайшие {publish_window_hours} часов\n\n"
        )
        header += bank_line
        notes = (
            "Показываем только одиночные ставки. "
            "На один матч — не больше одной рекомендации. "
            f"В список попадают варианты, где модель видит перевес над линией и {books_note}"
        )
        blocks: list[str] = [header + notes]

        for idx, bet in enumerate(bets, start=1):
            selection_text = self._compact_selection_display(
                bet.family,
                bet.selection,
                bet.point,
                getattr(bet, "team_side", None),
                bet.home_team,
                bet.away_team,
                getattr(bet, "selection_key", None),
            )
            start_text = (
                f"{bet.commence_time.astimezone(self.settings.tzinfo).strftime('%d.%m.%Y %H:%M')} "
                f"{self._timezone_label(bet.commence_time)}"
            )
            quality_text = self._quality_notes(bet)
            trust_text = self._trust_profile_text(bet)
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
            consensus_probability = self._consensus_probability(bet)
            raw_model_probability = self._raw_model_probability(bet)
            explanation = self._build_explanation(bet, selection_text)
            consensus_label = "по линии (консенсус)"
            if bet.family in {"spreads", "dnb"}:
                consensus_label = "по линии (с учётом возврата)"
            probability_lines: list[str] = []
            if self._show_probability_breakdown(bet):
                probability_lines.append(
                    f"📊 Сырая модель: {raw_model_probability * 100:.1f}% | Скорректированная оценка: {bet.adjusted_probability * 100:.1f}%"
                )
                probability_lines.append(f"📉 {consensus_label}: {consensus_probability * 100:.1f}%")
            else:
                probability_label = "Вероятность по модели"
                if bet.family in {"spreads", "dnb"}:
                    probability_label = "Оценка по модели"
                probability_lines.append(
                    f"📊 {probability_label}: {bet.adjusted_probability * 100:.1f}% | {consensus_label}: {consensus_probability * 100:.1f}%"
                )
            probability_block = "\n".join(probability_lines)
            blocks.append(
                f"{idx}. {bet.home_team} — {bet.away_team}\n"
                f"🎯 Ставка: {self._market_name_display(bet.family)} — {selection_text}\n"
                f"💸 Коэффициент: {bet.odds:.2f}\n"
                f"{probability_block}\n"
                f"✅ Уверенность: {bet.confidence:.1f}% | Букмекеров: {bet.books_count}\n"
                f"{trust_text + chr(10) if trust_text else ''}"
                f"{quality_text + chr(10) if quality_text else ''}"
                f"🏆 Турнир: {bet.league_name}\n"
                f"🕒 Начало: {start_text}"
                f"{stake_text}"
                f"{xg_text}"
                f"{used_text}\n"
                f"📝 Разбор:\n{explanation}"
            )

        return "\n\n".join(blocks)

    def _current_edge_analysis_text(self, bet: CandidateBet, selection_text: str) -> str:
        kind = self._selection_kind(
            bet.family,
            bet.selection,
            selection_text,
            getattr(bet, "selection_key", None),
        )
        adjusted_model_pct = float(getattr(bet, "adjusted_probability", 0.0) or 0.0) * 100.0
        consensus_pct = self._consensus_probability(bet) * 100.0
        edge_pp = adjusted_model_pct - consensus_pct

        if bet.family == "totals":
            line_label = selection_text
            return (
                f"Модель даёт {adjusted_model_pct:.1f}% против {consensus_pct:.1f}% по линии, "
                f"что даёт запас {edge_pp:+.1f} п.п. и объясняет интерес к ставке на {line_label}."
            )
        if bet.family == "h2h":
            target_team = bet.home_team if kind == "home" else bet.away_team if kind == "away" else "этот исход"
            return (
                f"Модель даёт {adjusted_model_pct:.1f}% против {consensus_pct:.1f}% по линии, "
                f"что даёт запас {edge_pp:+.1f} п.п. в пользу {target_team}."
            )
        if bet.family in {"spreads", "dnb"}:
            return (
                f"Модель даёт {adjusted_model_pct:.1f}% против {consensus_pct:.1f}% по линии, "
                f"что даёт запас {edge_pp:+.1f} п.п. и объясняет интерес к этой форе."
            )
        return (
            f"Модель даёт {adjusted_model_pct:.1f}% против {consensus_pct:.1f}% по линии, "
            f"что даёт запас {edge_pp:+.1f} п.п. и объясняет интерес к ставке."
        )

    def _analysis_breakdown(self, bet: CandidateBet, selection_text: str) -> str | None:
        if not bool(getattr(self.settings, "detailed_telegram_writeup", True)):
            return None
        analysis = dict(getattr(bet, "analysis", {}) or {})
        sections = analysis.get("sections") or {}
        if not isinstance(sections, dict):
            sections = {}
        order = [
            ("xg", "xG"),
            ("profile", "Профиль атаки/обороны"),
            ("splits", "Дом/выезд"),
            ("recent", "Свежая выборка"),
            ("form", "Форма"),
            ("table", "Таблица"),
            ("injuries", "Кадры и новости"),
            ("market", "Рынок"),
        ]
        lines: list[str] = []
        seen: set[str] = set()

        edge_text = self._current_edge_analysis_text(bet, selection_text)
        if edge_text:
            normalized_edge = " ".join(edge_text.split())
            seen.add(normalized_edge)
            lines.append(f"• Линия и value: {edge_text}")

        for key, label in order:
            text = str(sections.get(key) or "").strip()
            normalized = " ".join(text.split())
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            lines.append(f"• {label}: {text}")

        summary_points = [str(item).strip() for item in (analysis.get("summary_points") or []) if str(item).strip()]
        for item in summary_points:
            normalized = " ".join(item.split())
            lower = normalized.lower()
            if (
                ("линия" in lower or "рын" in lower)
                and "модел" in lower
                and "%" in lower
            ):
                continue
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            lines.append(f"• {normalized}")

        return "\n".join(lines) if lines else None

    def _build_explanation(self, bet: CandidateBet, selection_text: str) -> str:
        detailed = self._analysis_breakdown(bet, selection_text)
        if detailed:
            return detailed

        parts: list[str] = []
        kind = self._selection_kind(
            bet.family,
            bet.selection,
            selection_text,
            getattr(bet, "selection_key", None),
        )

        raw_model_pct = self._raw_model_probability(bet) * 100.0
        adjusted_model_pct = float(bet.adjusted_probability) * 100.0
        consensus_pct = self._consensus_probability(bet) * 100.0
        edge_pp = adjusted_model_pct - consensus_pct

        if bet.family == "totals":
            line_label = selection_text
            parts.append(
                f"На {line_label} линия сейчас даёт около {consensus_pct:.1f}%, "
                f"а модель оценивает вероятность в {adjusted_model_pct:.1f}%. "
                f"Запас {edge_pp:+.1f} п.п. делает этот вариант интереснее рынка."
            )
        elif bet.family == "h2h":
            target_team = bet.home_team if kind == "home" else bet.away_team if kind == "away" else "этот исход"
            parts.append(
                f"По линии этот вариант оценивается примерно в {consensus_pct:.1f}%, "
                f"а модель поднимает вероятность до {adjusted_model_pct:.1f}%. "
                f"Перевес {edge_pp:+.1f} п.п. даёт преимущество в пользу {target_team}."
            )
        elif bet.family in {"spreads", "dnb"}:
            parts.append(
                f"В пересчёте на цену с учётом возврата линия даёт около {consensus_pct:.1f}%, а скорректированная оценка модели — {adjusted_model_pct:.1f}%. "
                f"Разница {edge_pp:+.1f} п.п. объясняет интерес к этой форе."
            )
        else:
            parts.append(
                f"Линия сейчас закладывает около {consensus_pct:.1f}%, а модель видит {adjusted_model_pct:.1f}%. "
                f"Разница {edge_pp:+.1f} п.п. говорит в пользу этой ставки."
            )

        adjustment_delta = raw_model_pct - adjusted_model_pct
        if self._show_probability_breakdown(bet):
            direction = "снижена" if adjustment_delta >= 0 else "поднята"
            parts.append(
                f"Сырая модельная оценка была {raw_model_pct:.1f}%, но для публикации она {direction} до {adjusted_model_pct:.1f}% "
                f"с учётом качества контекста, подтверждения линии и защитного shrink к рынку."
            )

        if bet.expected_home is not None and bet.expected_away is not None:
            expected_home = float(bet.expected_home)
            expected_away = float(bet.expected_away)
            total_xg = expected_home + expected_away
            stronger = bet.home_team if expected_home >= expected_away else bet.away_team
            if bet.family == "totals" and kind == "under" and bet.point is not None:
                parts.append(
                    f"По ожидаемым голам матч тянет к {total_xg:.2f} ({expected_home:.2f} : {expected_away:.2f}). "
                    f"Для линии {bet.point:g} это профиль в пользу более низового сценария. "
                    f"Основной вклад в темп модель ждёт от {stronger}."
                )
            elif bet.family == "totals" and kind == "over" and bet.point is not None:
                parts.append(
                    f"По ожидаемым голам матч тянет к {total_xg:.2f} ({expected_home:.2f} : {expected_away:.2f}), "
                    f"что поддерживает более результативный сценарий относительно линии {bet.point:g}."
                )
            else:
                parts.append(
                    f"Ожидаемые голы по модели — {expected_home:.2f} : {expected_away:.2f}, "
                    f"что не противоречит выбранному сценарию."
                )

        bookmakers = self._bookmakers_text(bet)
        premium_pct = self._price_vs_consensus_pct(bet)
        books_count = int(getattr(bet, "books_count", 0) or 0)
        market_line = (
            ("Рынок идею подтверждает: " if books_count >= 2 else "Рыночное подтверждение слабее: ")
            + f"{self._books_confirmation_phrase(books_count)}"
        )
        if bookmakers:
            market_line += f" ({bookmakers})"
        market_line += "."

        if premium_pct is not None:
            if premium_pct >= 0.5:
                market_line += f" Лучшая доступная цена сейчас примерно на {premium_pct:.1f}% выше fair-цены консенсуса."
            elif premium_pct <= -0.5:
                market_line += f" Цена уже просела относительно fair-цены консенсуса примерно на {abs(premium_pct):.1f}%, поэтому value хуже, чем в пике, но перевес модели пока сохраняется."
            else:
                market_line += " Текущая цена близка к fair-цене консенсуса."
        parts.append(market_line)

        cleaned: list[str] = []
        seen: set[str] = set()
        for part in parts:
            key = part.strip()
            if not key or key in seen:
                continue
            seen.add(key)
            cleaned.append(key)
        return "\n\n".join(cleaned[:3])

    def _books_confirmation_phrase(self, books_count: int) -> str:
        abs_count = abs(int(books_count))
        last_two = abs_count % 100
        last = abs_count % 10
        if 11 <= last_two <= 14:
            noun = "букмекеров"
        elif last == 1:
            noun = "букмекер"
        elif last in {2, 3, 4}:
            noun = "букмекера"
        else:
            noun = "букмекеров"
        verb = "держит" if abs_count == 1 else "держат"
        return f"линию {verb} уже {books_count} {noun}"

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
            selection_text = self._row_selection_display(dict(item))
            lines.append(
                f"{idx}. {item.get('home_team')} — {item.get('away_team')}\n"
                f"{emoji} Итог: {self._format_outcome(outcome)} | Счёт: {score or 'н/д'}\n"
                f"Ставка: {self._market_name_display(str(item.get('family') or ''))} — {selection_text} "
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

    async def publish_settlement_summary(self, settlement_summary: dict[str, Any]) -> tuple[int, list[str]]:
        if not getattr(self.settings, "settlement_send_telegram_summary", True):
            return 0, []
        message = self.render_settlement_summary(settlement_summary)
        if not message:
            return 0, []
        return await self._send_message(message)

    @staticmethod
    def _daily_report_team_pair(row: dict[str, Any]) -> tuple[str, str]:
        home = str(row.get("home_team") or row.get("home") or "").strip()
        away = str(row.get("away_team") or row.get("away") or "").strip()
        if home and away:
            return home, away
        match_name = str(row.get("match_name") or row.get("event_name") or row.get("match") or "").strip()
        text = " ".join(match_name.split())
        low = text.lower()
        for sep in (f" {chr(8212)} ", f" {chr(8211)} ", " vs ", " v ", " - "):
            marker = sep.lower()
            if marker not in low:
                continue
            index = low.find(marker)
            parsed_home = text[:index].strip()
            parsed_away = text[index + len(sep):].strip()
            if parsed_home and parsed_away:
                return home or parsed_home, away or parsed_away
        return home, away

    @staticmethod
    def _daily_report_odds(row: dict[str, Any]) -> float:
        for key in ("odds", "selected_odds", "price", "decimal_odds", "odd", "value"):
            value = row.get(key)
            if value in (None, ""):
                continue
            try:
                return float(value)
            except Exception:
                continue
        return 0.0

    def render_daily_report(self, daily_report: dict[str, Any]) -> str | None:
        summary = dict(daily_report.get("summary") or {})
        rows: list[dict[str, Any]] = []
        skipped_corrupted = 0
        for item in (daily_report.get("rows") or []):
            if not isinstance(item, dict):
                continue
            row = dict(item)
            home, away = self._daily_report_team_pair(row)
            odds = self._daily_report_odds(row)
            if not home or not away or odds <= 1.0:
                skipped_corrupted += 1
                continue
            row["home_team"] = home
            row["away_team"] = away
            row["odds"] = odds
            rows.append(row)
        skipped_corrupted += len([item for item in (daily_report.get("corrupted_rows") or []) if isinstance(item, dict)])
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
            lines.insert(1, "🔁 Обновлено: закрылись ставки, которые раньше были в ожидании.")
        pending_stake = float(summary.get("pending_stake") or 0.0)
        if pending_stake > 0:
            lines.append(f"Открытый остаток за день: {self._format_money(pending_stake, bankroll_summary=bankroll)}")

        if skipped_corrupted:
            lines.append(f"Corrupted entries skipped: {skipped_corrupted}")

        quality_analysis = dict(daily_report.get("quality_analysis") or {})
        failure_tags = dict(quality_analysis.get("top_failure_tags") or {})
        if failure_tags:
            top_tags = ", ".join(f"{name}: {count}" for name, count in list(failure_tags.items())[:4])
            lines.append(f"Разбор ошибок: {top_tags}")

        next_day_adjustments = dict(daily_report.get("next_day_adjustments") or {})
        actions = [dict(item) for item in (next_day_adjustments.get("actions") or []) if isinstance(item, dict)]
        if actions:
            action_parts: list[str] = []
            for item in actions[:4]:
                scope = str(item.get("scope") or "")
                key = str(item.get("key") or "")
                delta = float(item.get("score_delta") or 0.0)
                if not key:
                    continue
                prefix = "Рынок" if scope == "family" else "Лига"
                action_parts.append(f"{prefix} {key}: {delta:+.2f}")
            if action_parts:
                lines.append(f"Коррекция на завтра: {', '.join(action_parts)}")

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
            score = str(row.get("final_score") or "н/д")
            pnl_raw = row.get("pnl")
            pnl_text = "ожидание" if pnl_raw in (None, "") else f"{float(pnl_raw):+.2f}{self._money_suffix(bankroll_summary=bankroll)}"
            family = str(row.get("family") or "")
            item_lines.append(
                f"{idx}. {row.get('home_team')} — {row.get('away_team')}\n"
                f"{emoji} {self._market_name_display(family)} — {self._row_selection_display(row)} "
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
        return await self._send_message(message)

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
            lines.append("Почему нет прогноза:" if published <= 0 else "Что ещё отсеялось:")
            for key, count in top_reasons[:top_limit]:
                lines.append(f"• {labels.get(key, key.replace('_', ' '))} — {count}")
        if quality_reasons:
            lines.append("Quality стопоры:" if published <= 0 else "Quality стопоры по остальным кандидатам:")
            for key, count in quality_reasons[:4]:
                lines.append(f"• quality: {quality_labels.get(key, key.replace('_', ' '))} — {count}")
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
