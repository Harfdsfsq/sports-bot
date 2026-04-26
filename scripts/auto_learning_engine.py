from __future__ import annotations

import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

UTC = timezone.utc

POLICY_PATH = Path("config/auto_learning_policy.json")
DEFAULT_STATE_PATH = Path(".data/learning-state.json")
DEFAULT_RUNTIME_ENV_PATH = Path(".data/auto_learning_runtime_overrides.env")
DEFAULT_CALIBRATION_PATH = Path(".data/calibration-profile.json")
OUT_JSON = Path(".data/exports/latest-auto-learning-report.json")
OUT_TXT = Path(".data/exports/latest-auto-learning-report.txt")


def load_json(path: str | Path, default: Any) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: str | Path, payload: Any) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def write_text(path: str | Path, text: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except Exception:
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except Exception:
        return default


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def policy() -> dict[str, Any]:
    raw = load_json(POLICY_PATH, {})
    if not isinstance(raw, dict):
        raw = {}
    return raw


def nested_get(row: dict[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        cur: Any = row
        ok = True
        for part in key.split("."):
            if not isinstance(cur, dict) or part not in cur:
                ok = False
                break
            cur = cur[part]
        if ok and cur not in (None, ""):
            return cur
    return default


def normalize_prob(value: Any) -> float | None:
    raw = safe_float(value, -1.0)
    if raw < 0:
        return None
    if raw > 1.0:
        raw /= 100.0
    if raw < 0 or raw > 1:
        return None
    return raw


def outcome_value(row: dict[str, Any]) -> tuple[str, float | None]:
    status = str(nested_get(row, "settlement.outcome", "outcome", "status", default="")).strip().lower()
    mapping = {
        "won": 1.0,
        "win": 1.0,
        "half_won": 0.5,
        "lost": 0.0,
        "loss": 0.0,
        "half_lost": 0.0,
        "push": None,
        "void": None,
        "cancelled": None,
        "canceled": None,
        "pending": None,
        "generated": None,
        "open": None,
    }
    if status in mapping:
        return status, mapping[status]
    return status or "unknown", None


def bet_id(row: dict[str, Any]) -> str:
    for key in ("fingerprint", "prediction_id", "id", "bet_id"):
        value = row.get(key)
        if value:
            return str(value)
    home = str(row.get("home_team") or row.get("home") or "")
    away = str(row.get("away_team") or row.get("away") or "")
    sel = str(row.get("selection") or row.get("market") or "")
    time = str(row.get("commence_time") or row.get("start_time") or "")
    return f"{home}|{away}|{sel}|{time}|{row.get('odds')}"


def state_bets() -> list[dict[str, Any]]:
    state = load_json(".data/state.json", {})
    if not isinstance(state, dict):
        return []
    rows: list[dict[str, Any]] = []
    for key in ("bets", "published_candidates"):
        raw = state.get(key) or []
        if isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict):
                    rows.append(dict(item))
    dedup: dict[str, dict[str, Any]] = {}
    for row in rows:
        dedup[bet_id(row)] = row
    return list(dedup.values())


def training_rows() -> list[dict[str, Any]]:
    payload = load_json(".data/exports/latest-training-dataset.json", None)
    if payload is None:
        payload = load_json("artifacts/run-bot/latest-training-dataset.json", None)

    rows: list[dict[str, Any]] = []
    if isinstance(payload, list):
        rows = [x for x in payload if isinstance(x, dict)]
    elif isinstance(payload, dict):
        for key in ("rows", "data", "items", "examples", "training_rows", "records"):
            raw = payload.get(key)
            if isinstance(raw, list):
                rows.extend([x for x in raw if isinstance(x, dict)])
        if not rows:
            for value in payload.values():
                if isinstance(value, list):
                    rows.extend([x for x in value if isinstance(x, dict)])
    return rows


def settled_rows() -> list[dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for source in (state_bets(), training_rows()):
        for row in source:
            status, actual = outcome_value(row)
            if actual is None and status not in {"push", "void", "cancelled", "canceled"}:
                continue
            rows[bet_id(row)] = row
    return list(rows.values())


def bucket_value(row: dict[str, Any], name: str) -> str:
    if name == "family":
        return str(row.get("family") or row.get("market_family") or row.get("market") or "unknown").lower()
    if name == "tier":
        return str(row.get("tier") or row.get("fallback_tier") or row.get("quality_tier") or "unknown").lower()
    if name == "quality_source":
        return str(row.get("quality_score_source") or row.get("score_source") or "unknown").lower()
    if name == "odds_bucket":
        odds = safe_float(row.get("odds"), 0.0)
        if odds <= 0:
            return "unknown"
        if odds < 1.7:
            return "<1.70"
        if odds < 2.0:
            return "1.70-1.99"
        if odds < 2.35:
            return "2.00-2.34"
        if odds < 2.75:
            return "2.35-2.74"
        return ">=2.75"
    if name == "confidence_bucket":
        conf = safe_float(row.get("confidence"), safe_float(row.get("adjusted_probability"), 0.0))
        if conf <= 1.0:
            conf *= 100.0
        if conf <= 0:
            return "unknown"
        if conf < 60:
            return "<60"
        if conf < 68:
            return "60-67.9"
        if conf < 73.5:
            return "68-73.4"
        if conf < 78:
            return "73.5-77.9"
        return ">=78"
    if name == "books_bucket":
        books = safe_int(row.get("books_count") or row.get("books"), 0)
        if books <= 0:
            return "unknown"
        if books == 1:
            return "1"
        if books == 2:
            return "2"
        return "3+"
    if name == "sources_bucket":
        sources = safe_int(row.get("sources_count") or row.get("sources"), 0)
        if sources <= 0:
            return "unknown"
        if sources == 1:
            return "1"
        if sources == 2:
            return "2"
        return "3+"
    if name == "league":
        league = str(row.get("league") or row.get("competition") or row.get("tournament") or "unknown")
        return league[:80] or "unknown"
    return "unknown"


class Stat:
    __slots__ = ("n", "won", "lost", "push", "stake", "pnl", "pred_sum", "actual_sum")

    def __init__(self) -> None:
        self.n = 0
        self.won = 0
        self.lost = 0
        self.push = 0
        self.stake = 0.0
        self.pnl = 0.0
        self.pred_sum = 0.0
        self.actual_sum = 0.0

    def add(self, row: dict[str, Any]) -> None:
        status, actual = outcome_value(row)
        stake = safe_float(row.get("stake_amount") or row.get("stake"), 0.0)
        pnl = safe_float(nested_get(row, "settlement.pnl", "pnl", default=0.0), 0.0)
        pred = normalize_prob(row.get("adjusted_probability"))
        if pred is None:
            pred = normalize_prob(row.get("probability"))
        if pred is None:
            pred = normalize_prob(row.get("confidence"))
        if pred is None:
            pred = 0.5

        if actual is None:
            self.push += 1
            return

        self.n += 1
        if actual >= 0.5:
            self.won += 1
        else:
            self.lost += 1
        self.stake += stake
        self.pnl += pnl
        self.pred_sum += pred
        self.actual_sum += actual

    def to_dict(self) -> dict[str, Any]:
        roi = (self.pnl / self.stake) if self.stake else 0.0
        hit = (self.actual_sum / self.n) if self.n else 0.0
        pred = (self.pred_sum / self.n) if self.n else 0.0
        return {
            "n": self.n,
            "won": self.won,
            "lost": self.lost,
            "push": self.push,
            "stake": round(self.stake, 2),
            "pnl": round(self.pnl, 2),
            "roi": round(roi, 4),
            "hit_rate": round(hit, 4),
            "avg_predicted_probability": round(pred, 4),
            "calibration_bias_pp": round((hit - pred) * 100.0, 2),
        }


def build_stats(rows: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, dict[str, Any]]]:
    overall = Stat()
    bucket_stats: dict[str, dict[str, Stat]] = defaultdict(lambda: defaultdict(Stat))
    bucket_names = [
        "family",
        "tier",
        "quality_source",
        "odds_bucket",
        "confidence_bucket",
        "books_bucket",
        "sources_bucket",
        "league",
    ]

    for row in rows:
        overall.add(row)
        for bucket in bucket_names:
            bucket_stats[bucket][bucket_value(row, bucket)].add(row)

    serial_buckets: dict[str, dict[str, Any]] = {}
    for bucket, values in bucket_stats.items():
        serial_buckets[bucket] = {name: stat.to_dict() for name, stat in values.items() if stat.n or stat.push}
    return overall.to_dict(), serial_buckets


def fallback_near_miss_summary() -> dict[str, Any]:
    report = load_json("artifacts/controlled-fallback-report.json", {})
    if not isinstance(report, dict):
        report = load_json(".data/exports/latest-controlled-fallback-report.json", {})
    if not isinstance(report, dict):
        return {"reason_counts": {}, "positive_near_misses": 0}

    reason_counts = Counter()
    positive = 0
    evaluated = report.get("evaluated")
    if isinstance(evaluated, list):
        for item in evaluated:
            if not isinstance(item, dict):
                continue
            candidate = item.get("candidate") if isinstance(item.get("candidate"), dict) else item
            metrics = item.get("metrics") if isinstance(item.get("metrics"), dict) else {}
            ev = safe_float(metrics.get("canonical_ev_pct", candidate.get("canonical_ev_pct", candidate.get("ev_pct", 0))))
            edge = safe_float(metrics.get("canonical_edge_pp", candidate.get("canonical_edge_pp", candidate.get("edge_pp", 0))))
            if ev > 0 or edge > 0:
                positive += 1
            reasons = item.get("reject_reasons") or item.get("reasons") or []
            if isinstance(reasons, str):
                reasons = [reasons]
            reason_counts.update(str(x) for x in reasons if str(x).strip())

    for key in ("reject_reasons", "reason_counts", "rejection_reasons"):
        raw = report.get(key)
        if isinstance(raw, dict):
            for reason, count in raw.items():
                reason_counts[str(reason)] += safe_int(count)
    return {
        "reason_counts": dict(reason_counts.most_common(15)),
        "positive_near_misses": positive,
        "status": report.get("status"),
        "published": bool(report.get("published") or report.get("selected_count")),
    }


def recommendations(overall: dict[str, Any], buckets: dict[str, dict[str, Any]], pol: dict[str, Any]) -> list[dict[str, Any]]:
    min_total = safe_int(pol.get("min_settled_total"), 30)
    min_bucket = safe_int(pol.get("min_bucket_samples"), 12)
    bad_rules = pol.get("bad_bucket_rules") if isinstance(pol.get("bad_bucket_rules"), dict) else {}
    good_rules = pol.get("good_bucket_rules") if isinstance(pol.get("good_bucket_rules"), dict) else {}
    out: list[dict[str, Any]] = []

    if safe_int(overall.get("n")) < min_total:
        out.append({
            "type": "observe_only",
            "severity": "info",
            "message": f"Недостаточно закрытых ставок для изменения guard’ов: {overall.get('n', 0)}/{min_total}.",
        })
        return out

    for bucket_name, values in buckets.items():
        for value, stat in values.items():
            n = safe_int(stat.get("n"))
            if n < min_bucket:
                continue
            roi = safe_float(stat.get("roi"))
            bias = safe_float(stat.get("calibration_bias_pp"))
            lost = safe_int(stat.get("lost"))
            won = safe_int(stat.get("won"))

            if roi <= safe_float(bad_rules.get("min_roi"), -0.08) or bias <= safe_float(bad_rules.get("min_calibration_bias_pp"), -8.0):
                out.append({
                    "type": "tighten",
                    "severity": "warning",
                    "bucket": bucket_name,
                    "value": value,
                    "n": n,
                    "roi": roi,
                    "calibration_bias_pp": bias,
                    "message": f"{bucket_name}={value}: слабая фактическая доходность/калибровка; guard нужно ужесточать.",
                })
            elif roi >= safe_float(good_rules.get("min_roi"), 0.08) and bias >= safe_float(good_rules.get("min_calibration_bias_pp"), 5.0):
                out.append({
                    "type": "watch_good",
                    "severity": "info",
                    "bucket": bucket_name,
                    "value": value,
                    "n": n,
                    "roi": roi,
                    "calibration_bias_pp": bias,
                    "message": f"{bucket_name}={value}: хорошая зона, но авто-ослабление выключено до ручного подтверждения.",
                })
    if not out:
        out.append({
            "type": "stable",
            "severity": "info",
            "message": "Статистика достаточная, но нет bucket’ов, требующих изменения guard’ов.",
        })
    return out


def runtime_overrides(overall: dict[str, Any], recs: list[dict[str, Any]], pol: dict[str, Any]) -> dict[str, str]:
    defaults = pol.get("runtime_override_defaults") if isinstance(pol.get("runtime_override_defaults"), dict) else {}
    base_conf = safe_float(defaults.get("CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE"), 73.5)
    base_ev = safe_float(defaults.get("CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT"), 10.0)
    base_edge = safe_float(defaults.get("CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP"), 5.0)

    n = safe_int(overall.get("n"))
    min_total = safe_int(pol.get("min_settled_total"), 30)
    tighten_count = sum(1 for r in recs if r.get("type") == "tighten")

    conf = base_conf
    ev = base_ev
    edge = base_edge
    mode = "observe_only"

    if n >= min_total and tighten_count:
        mode = "tighten_proxy_single_source"
        conf += min(safe_float(pol.get("max_runtime_confidence_raise_pp"), 2.0), 0.5 * tighten_count)
        ev += min(safe_float(pol.get("max_runtime_ev_raise_pct"), 2.0), 0.5 * tighten_count)
        edge += min(safe_float(pol.get("max_runtime_edge_raise_pp"), 1.0), 0.25 * tighten_count)

    # No auto-relief by default. It is too dangerous for betting quality.
    return {
        "AUTO_LEARNING_ACTIVE": "true",
        "AUTO_LEARNING_SAMPLE_READY": "true" if n >= min_total else "false",
        "AUTO_LEARNING_MODE": mode,
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE": f"{conf:.2f}",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT": f"{ev:.2f}",
        "CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP": f"{edge:.2f}",
    }


def write_runtime_env(path: Path, overrides: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [f"{key}={value}" for key, value in sorted(overrides.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def update_calibration_profile(path: Path, payload: dict[str, Any]) -> None:
    profile = load_json(path, {})
    if not isinstance(profile, dict):
        profile = {}
    profile["auto_learning"] = {
        "version": "safe-feedback-v1",
        "updated_at": payload["created_at"],
        "mode": payload["runtime_overrides"].get("AUTO_LEARNING_MODE"),
        "sample_ready": payload["runtime_overrides"].get("AUTO_LEARNING_SAMPLE_READY") == "true",
        "overall": payload["overall"],
        "recommendations": payload["recommendations"][:20],
        "runtime_overrides": payload["runtime_overrides"],
        "note": "Auto-learning is conservative: it can tighten proxy/single-source guardrails after enough negative evidence; it does not loosen thresholds automatically.",
    }
    write_json(path, profile)


def render_report(payload: dict[str, Any]) -> str:
    overall = payload.get("overall") or {}
    near = payload.get("near_misses") or {}
    recs = payload.get("recommendations") or []
    overrides = payload.get("runtime_overrides") or {}

    lines = [
        "🧠 Отчёт автообучения",
        "",
        "📚 Закрытые ставки",
        f"• Учтено: {overall.get('n', 0)} | W {overall.get('won', 0)} / L {overall.get('lost', 0)} / Push {overall.get('push', 0)}",
        f"• ROI: {safe_float(overall.get('roi')) * 100:+.1f}% | PnL {safe_float(overall.get('pnl')):+.2f} | ставка {safe_float(overall.get('stake')):.2f}",
        f"• Калибровка: факт {safe_float(overall.get('hit_rate')) * 100:.1f}% vs модель {safe_float(overall.get('avg_predicted_probability')) * 100:.1f}% | bias {safe_float(overall.get('calibration_bias_pp')):+.1f} п.п.",
        "",
        "⚠️ Near-miss / отказы",
        f"• Положительных near-miss: {near.get('positive_near_misses', 0)}",
    ]

    reasons = near.get("reason_counts") if isinstance(near.get("reason_counts"), dict) else {}
    for reason, count in list(reasons.items())[:6]:
        lines.append(f"• {reason} — {count}")

    lines += ["", "🛡️ Решение"]
    for rec in recs[:8]:
        lines.append(f"• {rec.get('message')}")

    lines += [
        "",
        "⚙️ Runtime overrides на следующий run",
        f"• sample_ready={overrides.get('AUTO_LEARNING_SAMPLE_READY')}",
        f"• mode={overrides.get('AUTO_LEARNING_MODE')}",
        f"• proxy confidence ≥ {overrides.get('CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_CONFIDENCE')}",
        f"• proxy EV ≥ {overrides.get('CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EV_PCT')}%",
        f"• proxy edge ≥ {overrides.get('CONTROLLED_FALLBACK_PROXY_SINGLE_SOURCE_MIN_EDGE_PP')} п.п.",
    ]
    return "\n".join(lines)


def main() -> int:
    try:
        pol = policy()
        if pol.get("enabled") is False or env_bool("AUTO_LEARNING_ENABLED", True) is False:
            payload = {
                "created_at": datetime.now(UTC).isoformat(),
                "enabled": False,
                "reason": "disabled",
            }
            write_json(OUT_JSON, payload)
            write_text(OUT_TXT, "🧠 Автообучение выключено.")
            return 0

        out_cfg = pol.get("output") if isinstance(pol.get("output"), dict) else {}
        state_path = Path(out_cfg.get("learning_state_path") or DEFAULT_STATE_PATH)
        runtime_env_path = Path(out_cfg.get("runtime_overrides_path") or DEFAULT_RUNTIME_ENV_PATH)
        calibration_path = Path(out_cfg.get("calibration_profile_path") or DEFAULT_CALIBRATION_PATH)

        rows = settled_rows()
        overall, buckets = build_stats(rows)
        near = fallback_near_miss_summary()
        recs = recommendations(overall, buckets, pol)
        overrides = runtime_overrides(overall, recs, pol)

        payload = {
            "created_at": datetime.now(UTC).isoformat(),
            "enabled": True,
            "policy_path": str(POLICY_PATH),
            "settled_rows": len(rows),
            "overall": overall,
            "buckets": buckets,
            "near_misses": near,
            "recommendations": recs,
            "runtime_overrides": overrides,
        }

        write_runtime_env(runtime_env_path, overrides)
        update_calibration_profile(calibration_path, payload)
        write_json(state_path, {
            "updated_at": payload["created_at"],
            "overall": overall,
            "runtime_overrides": overrides,
            "recommendations": recs[:50],
            "last_near_misses": near,
        })
        write_json(OUT_JSON, payload)
        text = render_report(payload)
        write_text(OUT_TXT, text)
        print(text)
        return 0
    except Exception as exc:
        # Never fail the forecast workflow because of learning.
        payload = {
            "created_at": datetime.now(UTC).isoformat(),
            "enabled": True,
            "error": repr(exc),
            "note": "Auto-learning failed safely and did not affect this run.",
        }
        write_json(OUT_JSON, payload)
        write_text(OUT_TXT, "🧠 Автообучение: безопасный отказ, run не прерван.\n" + repr(exc))
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
