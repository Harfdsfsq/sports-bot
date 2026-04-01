from fastapi import FastAPI

from app.config import get_settings
from app.services.runner import PredictionRunner

settings = get_settings()
app = FastAPI(title=settings.app_name)


@app.get('/health')
def health() -> dict:
    return {'status': 'ok', 'app': settings.app_name, 'env': settings.app_env}


@app.post('/run')
async def run_now() -> dict:
    runner = PredictionRunner(settings)
    summary = await runner.run_once()
    return {'ok': True, 'summary': summary}
