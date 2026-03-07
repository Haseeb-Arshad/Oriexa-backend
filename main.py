#!/usr/bin/env python3
"""Compatibility entrypoint.

This file intentionally proxies to `app.main:app` so every startup path
uses the orchestrator backend (DB-backed progress, roadmap, and delivery gates).
"""

from __future__ import annotations

import uvicorn

from app.main import app


if __name__ == "__main__":
    uvicorn.run("app.main:app", host="0.0.0.0", port=8000, reload=False)
