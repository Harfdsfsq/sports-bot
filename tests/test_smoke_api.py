from fastapi.testclient import TestClient

import app.main as main_module
from app.main import app


def test_health_endpoint():
    client = TestClient(app)
    response = client.get("/health")
    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"


def test_run_requires_admin_token(monkeypatch):
    async def fake_run_once(self):
        return {"ok": True}

    monkeypatch.setattr(main_module.settings, "admin_run_token", "secret-token")
    monkeypatch.setattr(main_module.PredictionRunner, "run_once", fake_run_once)

    client = TestClient(app)

    unauthorized = client.post("/run")
    assert unauthorized.status_code == 401

    authorized = client.post("/run", headers={"X-Admin-Token": "secret-token"})
    assert authorized.status_code == 200
    assert authorized.json()["ok"] is True
