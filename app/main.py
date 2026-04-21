from __future__ import annotations

import os
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
    if expected and x_admin_token != expected:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="unauthorized",
        )
    runner = PredictionRunner(settings)
    summary = await runner.run_once()
    return {"ok": True, "summary": summary}
