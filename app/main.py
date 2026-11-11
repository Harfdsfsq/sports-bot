from __future__ import annotations

import os
from secrets import compare_digest
from typing import Annotated

from fastapi import FastAPI, Header, HTTPException, status

from app.config import get_settings
from app.services.runner import PredictionRunner

settings = get_settings()
app = FastAPI(title=settings.app_name)


@app.get("/health")
def health() -> dict[str, str | bool]:
    return {
        "status": "ok",
        "app": settings.app_name,
        "env": settings.app_env,
        "dry_run": settings.publish_dry_run,
    }


@app.post("/run")
async def run_now(
    x_admin_token: Annotated[str | None, Header()] = None,
) -> dict:
    expected = os.getenv("ADMIN_RUN_TOKEN")
    if not expected:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="admin token is not configured",
        )
    if x_admin_token is None or not compare_digest(x_admin_token.encode(), expected.encode()):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
        )
    runner: PredictionRunner
    if os.getenv("PUBLICATION_PROFILE", "").lower() == "daily_quality":
        from app.services.daily_quality_runner import DailyQualityRunner

        runner = DailyQualityRunner(settings)
    else:
        runner = PredictionRunner(settings)
    summary = await runner.run_once()
    return {"ok": True, "summary": summary}
