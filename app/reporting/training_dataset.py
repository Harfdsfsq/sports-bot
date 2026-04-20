from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any


class TrainingDatasetExporter:
    def __init__(self, output_path: str):
        self.output_path = Path(output_path)

    def export(self, *, state_path: str) -> dict[str, Any]:
        state = json.loads(Path(state_path).read_text(encoding="utf-8"))
        rows = []
        for tracking_mode, items in (("published", state.get("bets") or []), ("shadow", state.get("shadow_bets") or [])):
            for item in items:
                rows.append(self._flatten(item, tracking_mode=tracking_mode))
        rows.sort(key=lambda row: (row.get("published_at") or "", row.get("prediction_id") or ""))
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        fieldnames = self._fieldnames(rows)
        with self.output_path.open("w", encoding="utf-8", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        jsonl_path = self.output_path.with_suffix(".jsonl")
        with jsonl_path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
        settled = sum(1 for row in rows if bool(row.get("is_settled")))
        return {"state_path": str(state_path), "csv_path": str(self.output_path), "jsonl_path": str(jsonl_path), "rows": len(rows), "settled_rows": settled, "pending_rows": len(rows) - settled}

    def _flatten(self, item: dict[str, Any], *, tracking_mode: str) -> dict[str, Any]:
        settlement = dict(item.get("settlement") or {})
        status = str(item.get("status") or tracking_mode)
        settled_statuses = {"won", "lost", "push", "void", "half_won", "half_lost"}
        source_summary = dict(item.get("source_summary") or {})
        return {
            "tracking_mode": tracking_mode,
            "prediction_id": item.get("prediction_id"),
            "fingerprint": item.get("fingerprint"),
            "published_at": item.get("published_at"),
            "match_key": item.get("match_key"),
            "league_name": item.get("league_name"),
            "home_team": item.get("home_team"),
            "away_team": item.get("away_team"),
            "family": item.get("family"),
            "selection": item.get("selection"),
            "selection_key": item.get("selection_key"),
            "team_side": item.get("team_side"),
            "point": self._float(item.get("point")),
            "odds": self._float(item.get("odds")),
            "books_count": int(item.get("books_count") or 0),
            "sources_count": int(item.get("sources_count") or 0),
            "market_probability": self._float(item.get("market_probability")),
            "consensus_probability": self._float(item.get("consensus_probability")),
            "model_probability": self._float(item.get("model_probability")),
            "adjusted_probability": self._float(item.get("adjusted_probability")),
            "edge_pct": self._float(item.get("edge_pct")),
            "ev_pct": self._float(item.get("ev_pct")),
            "confidence": self._float(item.get("confidence")),
            "publication_score": self._float(item.get("publication_score")),
            "expected_home": self._float(item.get("expected_home")),
            "expected_away": self._float(item.get("expected_away")),
            "stake_amount": self._float(item.get("stake_amount")),
            "stake_pct": self._float(item.get("stake_pct")),
            "bankroll_snapshot": self._float(item.get("bankroll_snapshot")),
            "bookmaker": item.get("bookmaker"),
            "selected_source": source_summary.get("selected_source"),
            "selected_bookmaker": source_summary.get("selected_bookmaker"),
            "quality_status": source_summary.get("quality_status"),
            "quality_score": self._float(source_summary.get("quality_score")),
            "status": status,
            "is_settled": status in settled_statuses,
            "result": settlement.get("result") or status,
            "pnl": self._float(settlement.get("pnl")),
            "final_home_goals": self._float(settlement.get("final_home_goals")),
            "final_away_goals": self._float(settlement.get("final_away_goals")),
            "settled_at": settlement.get("settled_at"),
        }

    @staticmethod
    def _fieldnames(rows: list[dict[str, Any]]) -> list[str]:
        seen: list[str] = []
        for row in rows:
            for key in row.keys():
                if key not in seen:
                    seen.append(key)
        return seen

    @staticmethod
    def _float(value: Any) -> float | None:
        if value in (None, ""):
            return None
        try:
            return float(value)
        except Exception:
            return None
