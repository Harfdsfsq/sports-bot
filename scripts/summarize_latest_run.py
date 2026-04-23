#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path

paths = [
    Path(".data/exports/main-clean-publish-report.json"),
    Path(".data/exports/odds-integrity-report.json"),
    Path(".data/exports/latest-run-summary.json"),
]
payload = {}
for path in paths:
    if path.exists():
        try:
            payload[path.name] = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            payload[path.name] = {"error": f"{type(exc).__name__}: {exc}"}
print(json.dumps(payload, ensure_ascii=False, indent=2))
