from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app
from app.providers.sportlogic_provider import SportLogicProvider
from app.services.runtime_inventory_hooks import _run_script


def test_run_endpoint_requires_configured_admin_token(monkeypatch):
    monkeypatch.delenv("ADMIN_RUN_TOKEN", raising=False)
    client = TestClient(app)

    response = client.post("/run")

    assert response.status_code == 503
    assert response.json()["detail"] == "admin token is not configured"


def test_runtime_hook_async_main_runs_inside_existing_event_loop(tmp_path, monkeypatch):
    marker = tmp_path / "hook-ran.txt"
    script = tmp_path / "async_hook.py"
    script.write_text(
        "from pathlib import Path\n"
        "import os\n"
        "import asyncio\n"
        "async def main_async():\n"
        "    Path(os.environ['HOOK_MARKER']).write_text('ok', encoding='utf-8')\n"
        "    return 0\n"
        "def main():\n"
        "    return asyncio.run(main_async())\n"
        "if __name__ == '__main__':\n"
        "    raise SystemExit(main())\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HOOK_MARKER", str(marker))

    async def run_inside_loop() -> dict:
        return _run_script(str(script))

    result = asyncio.run(run_inside_loop())

    assert result["status"] == "ok"
    assert result["return_code"] == 0
    assert marker.read_text(encoding="utf-8") == "ok"


def test_sportlogic_next_cursor_supports_common_envelopes():
    assert SportLogicProvider._next_cursor({"next_cursor": "abc"}) == "abc"
    assert SportLogicProvider._next_cursor({"meta": {"nextCursor": "def"}}) == "def"
    assert SportLogicProvider._next_cursor({"pagination": {"cursor": "ghi"}}) == "ghi"
    assert SportLogicProvider._next_cursor({"data": []}) is None
