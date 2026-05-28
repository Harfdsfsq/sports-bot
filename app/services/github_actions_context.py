from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_TRUE_VALUES = {"1", "true", "yes", "on"}


def env_bool(name: str, default: bool = True) -> bool:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    return str(raw).strip().lower() in _TRUE_VALUES


def github_run_context() -> dict[str, Any]:
    """Return a small, Telegram-safe GitHub Actions run reference.

    The values come from standard GitHub Actions environment variables. Outside
    Actions the function returns enabled=false, so local runs and tests stay quiet.
    """
    run_id = str(os.getenv("GITHUB_RUN_ID") or "").strip()
    repo = str(os.getenv("GITHUB_REPOSITORY") or "").strip()
    server = str(os.getenv("GITHUB_SERVER_URL") or "https://github.com").strip().rstrip("/")
    run_attempt = str(os.getenv("GITHUB_RUN_ATTEMPT") or "").strip()
    workflow = str(os.getenv("GITHUB_WORKFLOW") or "").strip()
    job = str(os.getenv("GITHUB_JOB") or "").strip()
    ref = str(os.getenv("GITHUB_REF_NAME") or os.getenv("GITHUB_REF") or "").strip()
    sha = str(os.getenv("GITHUB_SHA") or "").strip()

    url = str(os.getenv("GITHUB_RUN_URL") or "").strip()
    if not url and run_id and repo:
        url = f"{server}/{repo}/actions/runs/{run_id}"

    return {
        "enabled": bool(run_id and repo),
        "run_id": run_id,
        "run_url": url,
        "artifact_name": f"run-bot-{run_id}" if run_id else "",
        "repository": repo,
        "workflow": workflow,
        "job": job,
        "run_attempt": run_attempt,
        "ref": ref,
        "sha": sha,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def github_run_reference_text() -> str:
    if not env_bool("HARIZON_TELEGRAM_RUN_REFERENCE_ENABLED", True):
        return ""
    ctx = github_run_context()
    if not ctx.get("enabled"):
        return ""

    lines = [
        "🔗 GitHub Actions",
        f"• Run ID: {ctx.get('run_id')}",
        f"• Run URL: {ctx.get('run_url')}",
        f"• Artifact: {ctx.get('artifact_name')}",
    ]
    workflow = str(ctx.get("workflow") or "").strip()
    attempt = str(ctx.get("run_attempt") or "").strip()
    if workflow or attempt:
        suffix = []
        if workflow:
            suffix.append(f"workflow {workflow}")
        if attempt:
            suffix.append(f"attempt {attempt}")
        lines.append("• " + ", ".join(suffix))
    return "\n".join(lines)


def append_github_run_reference(message: Any) -> str:
    text = str(message or "")
    ref = github_run_reference_text()
    if not ref:
        return text
    # Avoid duplicate blocks when a wrapper is stacked with a patched renderer.
    if "Run ID:" in text and "Run URL:" in text:
        return text
    if "GitHub Actions" in text and "actions/runs/" in text:
        return text
    return text.rstrip() + "\n\n" + ref


def write_github_run_context(path: str | Path = ".data/exports/latest-github-actions-run-context.json") -> dict[str, Any]:
    ctx = github_run_context()
    try:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(ctx, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    except Exception:
        pass
    return ctx
