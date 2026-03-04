#!/usr/bin/env bash
# Oriexa — one-command droplet setup / update
# Run from /opt/oriexa/repo:  bash scripts/setup_droplet.sh
set -e

REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
cd "$REPO_DIR"

echo "==> [1/6] Pulling latest code..."
git pull origin main

echo "==> [2/6] Installing Python dependencies..."
uv pip install -e .

echo "==> [3/6] Running DB migrations..."
.venv/bin/alembic upgrade head

echo "==> [4/6] Creating agent workspace directory..."
WORKSPACE="${AGENT_WORKSPACE_DIR:-/opt/oriexa/agent_works}"
mkdir -p "$WORKSPACE"
echo "      workspace: $WORKSPACE"

echo "==> [5/6] Installing systemd services..."
for svc in oriexa-api oriexa-swarm oriexa-worker oriexa-reviewer; do
    if [ -f "scripts/${svc}.service" ]; then
        cp "scripts/${svc}.service" "/etc/systemd/system/${svc}.service"
        echo "      installed ${svc}.service"
    fi
done
systemctl daemon-reload

echo "==> [6/6] Enabling and (re)starting all services..."
for svc in oriexa-api oriexa-swarm oriexa-worker oriexa-reviewer; do
    systemctl enable "$svc" 2>/dev/null || true
    systemctl restart "$svc"
    echo "      $svc: $(systemctl is-active $svc)"
done

echo ""
echo "======================================================"
echo " Oriexa is running. Useful commands:"
echo ""
echo "  # Watch all agent logs live:"
echo "  journalctl -u 'oriexa-*' -f"
echo ""
echo "  # Check status:"
echo "  systemctl status oriexa-api oriexa-swarm oriexa-worker oriexa-reviewer"
echo ""
echo "  # Restart a single service:"
echo "  systemctl restart oriexa-worker"
echo "======================================================"
