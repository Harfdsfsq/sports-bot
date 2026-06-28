from __future__ import annotations

"""Backfill published HARIZON picks from historical GitHub Actions artifacts.

The manual performance report must not depend only on the current checkout's
.data/bets files.  Older run-bot executions often have their Telegram text,
controlled-fallback report, latest picks or state snapshots only inside Actions
artifacts.  This script downloads the still-available artifacts, extracts
published picks, merges them into the durable semantic ledger, and mirrors them
back into .data/state.json before settlement/reporting runs.
"""

import argparse
import json
import os
import tempfile
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib import request

ROOT = Path(".").resolve()
EXPORT = ROOT / ".data" / "exports"
IMPORT = ROOT / ".data" / "imports"
BET_DIR = ROOT / ".data" / "bets"
REPORT = EXPORT / "latest-actions-artifact-publication-backfill.json"

TEXT_HINTS = ("🔥", "🎯 Ставка", "💸 Коэффициент", "HARIZON")
PUBLISHED_JSON_HINTS = (
    "latest-picks", "latest-pending-bets", "published", "published-picks", "published-bets",
    "controlled-fallback-report", "fallback-sent-index", "state.json", "latest-harizon-telegram-run-report",
)


def _json(path: Path, default: Any) -> Any:
    try:
        if path.exists() and path.stat().st_size > 0:
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _gh_json(url: str, token: str) -> Any:
    req = request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "harizon-publication-backfill",
    })
    with request.urlopen(req, timeout=45) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _download(url: str, token: str, path: Path) -> bool:
    req = request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "harizon-publication-backfill",
    })
    try:
        with request.urlopen(req, timeout=90) as resp:
            path.write_bytes(resp.read())
        return True
    except Exception:
        return False


def _iter_runs(repo: str, token: str, max_runs: int, lookback_days: int) -> list[dict[str, Any]]:
    cutoff = datetime.now(UTC) - timedelta(days=max(1, lookback_days))
    runs: list[dict[str, Any]] = []
    page = 1
    while len(runs) < max_runs and page <= 10:
        url = f"https://api.github.com/repos/{repo}/actions/runs?per_page=100&page={page}&status=completed"
        payload = _gh_json(url, token)
        page_runs = payload.get("workflow_runs") if isinstance(payload, dict) else []
        if not isinstance(page_runs, list) or not page_runs:
            break
        for run in page_runs:
            created = _parse_dt(run.get("created_at"))
            if created is not None and created < cutoff:
                continue
            name = str(run.get("name") or run.get("display_title") or "").lower()
            path = str(run.get("path") or "").lower()
            if "run-bot" not in name and "run-bot" not in path and "daily report" not in name and "daily-report" not in path:
                continue
            runs.append(run)
            if len(runs) >= max_runs:
                break
        page += 1
    return runs[:max_runs]


def _parse_dt(value: Any) -> datetime | None:
    try:
        text = str(value or "").strip()
        if not text:
            return None
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(UTC)
    except Exception:
        return None


def _iter_artifacts(repo: str, token: str, run_id: int) -> list[dict[str, Any]]:
    try:
        payload = _gh_json(f"https://api.github.com/repos/{repo}/actions/runs/{run_id}/artifacts?per_page=100", token)
        artifacts = payload.get("artifacts") if isinstance(payload, dict) else []
        return [x for x in artifacts if isinstance(x, dict)] if isinstance(artifacts, list) else []
    except Exception:
        return []


def _walk_json_texts(value: Any) -> list[str]:
    texts: list[str] = []
    if isinstance(value, str):
        if any(h in value for h in TEXT_HINTS):
            texts.append(value)
    elif isinstance(value, list):
        for item in value:
            texts.extend(_walk_json_texts(item))
    elif isinstance(value, dict):
        for item in value.values():
            texts.extend(_walk_json_texts(item))
    return texts


def _extract_container_rows(payload: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if isinstance(payload, list):
        return [x for x in payload if isinstance(x, dict)]
    if not isinstance(payload, dict):
        return rows
    for key in (
        "bets", "rows", "picks", "pending", "published", "published_candidates", "published_picks",
        "selected", "selected_rows", "selected_picks", "top_picks", "telegram_picks", "sent_picks",
        "published_rows", "selected_all", "published_bets", "items",
    ):
        value = payload.get(key)
        if isinstance(value, list):
            rows.extend([x for x in value if isinstance(x, dict)])
    return rows


def _published_context(path_name: str, payload: Any) -> bool:
    lower = path_name.lower()
    if "rejected" in lower or "shadow" in lower or "reserve" in lower or "debug" in lower:
        return False
    if any(h in lower for h in ("published", "pending-bets", "latest-picks", "fallback-sent-index", "state.json")):
        return True
    if "controlled-fallback-report" in lower and isinstance(payload, dict):
        return bool(payload.get("published") or payload.get("published_count"))
    return False


def _mark_backfill_row(row: dict[str, Any], source: str, created_at: str | None) -> dict[str, Any]:
    out = dict(row)
    out["telegram_sent"] = True
    out["published"] = True
    out.setdefault("status", "pending")
    out.setdefault("publication_lifecycle_status", "telegram_sent")
    out.setdefault("publication_lifecycle_stage", "telegram_sent")
    out.setdefault("source", "actions_artifact_backfill")
    out.setdefault("ledger_source_file", source)
    if created_at:
        out.setdefault("published_at_utc", created_at)
    return out


def _scan_extracted(root: Path, run: dict[str, Any], artifact: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str], dict[str, int]]:
    from scripts import sync_publication_ledger as ledger

    rows: list[dict[str, Any]] = []
    texts: list[str] = []
    stats = {"files_seen": 0, "text_files": 0, "json_files": 0, "json_rows": 0, "text_rows": 0}
    created_at = str(run.get("created_at") or artifact.get("created_at") or "") or None
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        stats["files_seen"] += 1
        rel = str(path.relative_to(root))
        low = rel.lower()
        if path.stat().st_size > 8_000_000:
            continue
        if low.endswith(".txt") or low.endswith(".log"):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except Exception:
                continue
            if any(h in text for h in TEXT_HINTS):
                stats["text_files"] += 1
                texts.append(text)
                parsed = ledger.parse_telegram_text(text, f"artifact:{run.get('id')}:{artifact.get('name')}:{rel}")
                rows.extend(parsed)
                stats["text_rows"] += len(parsed)
        elif low.endswith(".json"):
            if not any(h in low for h in PUBLISHED_JSON_HINTS):
                continue
            payload = _json(path, None)
            if payload is None:
                continue
            stats["json_files"] += 1
            for text in _walk_json_texts(payload):
                texts.append(text)
                parsed = ledger.parse_telegram_text(text, f"artifact-json-text:{run.get('id')}:{artifact.get('name')}:{rel}")
                rows.extend(parsed)
                stats["text_rows"] += len(parsed)
            if _published_context(rel, payload):
                for item in _extract_container_rows(payload):
                    # Avoid raw reserve/evaluated rows unless the file/context is explicitly published/pending/state.
                    if item.get("ok") is False and not item.get("published"):
                        continue
                    row = _mark_backfill_row(item, f"artifact-json:{run.get('id')}:{artifact.get('name')}:{rel}", created_at)
                    rows.append(row)
                    stats["json_rows"] += 1
    return rows, texts, stats


def _merge_into_ledger(rows: list[dict[str, Any]]) -> dict[str, Any]:
    from scripts import sync_publication_ledger as ledger

    existing = ledger.iter_jsonl(ledger.PUBLISHED_JSONL) + ledger.iter_jsonl(ledger.SETTLED_JSONL)
    merged, stats = ledger.merge_by_key(existing, rows)
    pending, pending_stats = ledger.merge_by_key([], [r for r in merged if ledger.is_pending(r)])
    settled, settled_stats = ledger.merge_by_key([], [r for r in merged if not ledger.is_pending(r)])
    ledger.write_jsonl(ledger.PUBLISHED_JSONL, merged)
    ledger.write_json(ledger.PENDING_JSON, pending)
    ledger.write_jsonl(ledger.SETTLED_JSONL, settled)
    ledger.write_json(ledger.EXPORT_DIR / "latest-pending-bets.json", pending)
    ledger.write_json(ledger.EXPORT_DIR / "latest-picks.json", merged)
    ledger.write_json(ledger.EXPORT_DIR / "latest-settled-bets.json", settled)
    state_stats = ledger.mirror_to_state(merged)
    return {
        "rows_input": len(rows),
        "published_ledger_rows": len(merged),
        "pending_rows": len(pending),
        "settled_rows": len(settled),
        "duplicates_removed": stats.get("duplicates_removed", 0),
        "pending_duplicates_removed": pending_stats.get("duplicates_removed", 0),
        "settled_duplicates_removed": settled_stats.get("duplicates_removed", 0),
        "state_mirror": state_stats,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPOSITORY") or "Harfdsfsq/sports-bot")
    parser.add_argument("--max-runs", type=int, default=int(os.getenv("ACTIONS_BACKFILL_MAX_RUNS", "120") or 120))
    parser.add_argument("--lookback-days", type=int, default=int(os.getenv("ACTIONS_BACKFILL_LOOKBACK_DAYS", "30") or 30))
    parser.add_argument("--artifact-filter", default=os.getenv("ACTIONS_BACKFILL_ARTIFACT_FILTER") or "run-bot,daily-report")
    args = parser.parse_args()

    token = os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN")
    EXPORT.mkdir(parents=True, exist_ok=True)
    IMPORT.mkdir(parents=True, exist_ok=True)
    BET_DIR.mkdir(parents=True, exist_ok=True)
    if not token:
        payload = {"status": "skipped", "reason": "missing_GITHUB_TOKEN", "created_at_utc": datetime.now(UTC).isoformat()}
        _write_json(REPORT, payload)
        print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
        return 0

    filters = [x.strip().lower() for x in str(args.artifact_filter).split(",") if x.strip()]
    runs = _iter_runs(args.repo, token, args.max_runs, args.lookback_days)
    all_rows: list[dict[str, Any]] = []
    total_stats = {"runs_seen": len(runs), "artifacts_seen": 0, "artifacts_downloaded": 0, "files_seen": 0, "text_files": 0, "json_files": 0, "json_rows": 0, "text_rows": 0}
    artifact_samples: list[dict[str, Any]] = []
    with tempfile.TemporaryDirectory(prefix="harizon-actions-backfill-") as td:
        tmp = Path(td)
        for run in runs:
            for artifact in _iter_artifacts(args.repo, token, int(run.get("id"))):
                total_stats["artifacts_seen"] += 1
                name = str(artifact.get("name") or "")
                if filters and not any(f in name.lower() for f in filters):
                    continue
                archive_url = str(artifact.get("archive_download_url") or "")
                if not archive_url:
                    continue
                zip_path = tmp / f"artifact-{run.get('id')}-{artifact.get('id')}.zip"
                if not _download(archive_url, token, zip_path):
                    continue
                total_stats["artifacts_downloaded"] += 1
                extract_dir = tmp / f"extract-{run.get('id')}-{artifact.get('id')}"
                extract_dir.mkdir(parents=True, exist_ok=True)
                try:
                    with zipfile.ZipFile(zip_path) as zf:
                        zf.extractall(extract_dir)
                except Exception:
                    continue
                rows, texts, stats = _scan_extracted(extract_dir, run, artifact)
                all_rows.extend(rows)
                for key, value in stats.items():
                    total_stats[key] = total_stats.get(key, 0) + int(value)
                if rows and len(artifact_samples) < 12:
                    artifact_samples.append({"run_id": run.get("id"), "artifact_id": artifact.get("id"), "artifact_name": name, "rows": len(rows), "created_at": run.get("created_at")})
                if texts:
                    text_path = IMPORT / f"actions-artifact-{run.get('id')}-{artifact.get('id')}.txt"
                    text_path.write_text("\n\n".join(texts), encoding="utf-8")
    merge_stats = _merge_into_ledger(all_rows) if all_rows else {"rows_input": 0, "published_ledger_rows": 0, "pending_rows": 0, "settled_rows": 0, "duplicates_removed": 0}
    payload = {
        "status": "ok",
        "created_at_utc": datetime.now(UTC).isoformat(),
        "repo": args.repo,
        "lookback_days": args.lookback_days,
        "max_runs": args.max_runs,
        "artifact_filter": filters,
        "stats": total_stats,
        "merge": merge_stats,
        "artifact_samples": artifact_samples,
    }
    _write_json(REPORT, payload)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
