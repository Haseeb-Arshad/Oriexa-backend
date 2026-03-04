#!/usr/bin/env bash
# Oriexa API — one-shot droplet setup / update script
# Run from /opt/oriexa/repo:  bash scripts/setup_droplet.sh
set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

echo "==> Pulling latest code..."
git pull origin main

echo "==> Installing dependencies..."
uv pip install -e .

echo "==> Running migrations..."
.venv/bin/alembic upgrade head

echo ""
echo "All done. Start the server with:"
echo "  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000"
