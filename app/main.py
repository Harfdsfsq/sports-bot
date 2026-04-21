from __future__ import annotations

from fastapi import FastAPI

from app import runtime_bot_fix
from app.config import get_settings
from app.services.runner import PredictionRunner

runtime_bot_fix.apply_runtime_fixes()
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
async def run_now() -> dict:
    runner = PredictionRunner(settings)
    summary = await runner.run_once()
    return {"ok": True, "summary": summary}
