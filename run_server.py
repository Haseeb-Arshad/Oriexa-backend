"""Launcher — changes to the correct directory before starting uvicorn."""
import os
import sys

# Change working directory so pydantic-settings finds .env
os.chdir(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.getcwd())

import uvicorn  # noqa: E402

if __name__ == "__main__":
    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=False)
