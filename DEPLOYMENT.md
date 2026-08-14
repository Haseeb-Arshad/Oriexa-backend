# Oriexa API — DigitalOcean Droplet Deployment Guide

## One-time setup (first deploy)

### 1. Clone the repo

```bash
mkdir -p /opt/oriexa
cd /opt/oriexa
git clone git@github.com:Haseeb-Arshad/Oriexa-backend.git repo
cd repo
```

### 2. Install uv

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
source $HOME/.cargo/env
```

### 3. Create venv and install dependencies

```bash
uv venv .venv
source .venv/bin/activate
uv pip install -e .
```

### 4. Create .env

```bash
cp .env.example .env
nano .env
```

Fill in all values — the critical ones:

```
DATABASE_URL=postgresql+asyncpg://postgres:<password>@<supabase-host>:5432/postgres
CORS_ORIGINS=https://oriexa-sigma.vercel.app,http://localhost:3000
ENVIRONMENT=production
NEXT_APP_URL=https://oriexa-sigma.vercel.app
ORIEXA_API_BASE_URL=https://oriexa-sigma.vercel.app/api/v1
ORIEXA_API_KEY=<set-locally>
WORKSPACE_ROOT=/opt/oriexa/workspaces
AGENT_WORKSPACE_DIR=/opt/oriexa/agent_works
```

### 5. Create required directories

```bash
mkdir -p /opt/oriexa/workspaces /opt/oriexa/agent_works
```

### 6. Enable IPv6 (required — Supabase direct connection is IPv6 only)

**In DigitalOcean control panel:**
- Droplets → your droplet → Settings → Networking → IPv6 → Enable
- Note your **IPv6 address** and **IPv6 gateway** shown on that page
- Power the droplet back on

**On the droplet — configure netplan:**

```bash
sudo cp /etc/netplan/50-cloud-init.yaml /etc/netplan/50-cloud-init.yaml.bak
sudo python3 scripts/enable_ipv6_netplan.py
sudo netplan apply
```

> Edit `scripts/enable_ipv6_netplan.py` first if your IPv6 address/gateway differ from what's hardcoded.

**Lock the config so cloud-init doesn't reset it on reboot:**

```bash
echo 'network: {config: disabled}' | sudo tee /etc/cloud/cloud.cfg.d/99-disable-network-config.cfg
```

**Verify IPv6 works:**

```bash
ping6 -c 3 2001:4860:4860::8888
```

### 7. Test the database connection

```bash
.venv/bin/python3 scripts/find_working_connection.py
```

### 8. Run migrations

```bash
.venv/bin/alembic upgrade head
```

### 9. Install systemd service

```bash
sudo cp /opt/oriexa/repo/scripts/oriexa-api.service /etc/systemd/system/oriexa-api.service
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now oriexa-api
sudo systemctl status oriexa-api
```

---

## Every future deploy (git push -> droplet update)

```bash
cd /opt/oriexa/repo
git pull origin main
uv pip install -e .
.venv/bin/alembic upgrade head
sudo systemctl daemon-reload
sudo systemctl restart oriexa-api oriexa-swarm oriexa-worker
sudo systemctl status oriexa-api
sudo systemctl status oriexa-swarm
sudo systemctl status oriexa-worker
```

### Verify deploy env for swarm/worker (required for Vercel deploys)

```bash
cd /opt/oriexa/repo
grep -E '^(VERCEL_TOKEN|VERCEL_ORG_ID|VERCEL_PROJECT_ID)=' .env
sudo systemctl show oriexa-swarm --property=Environment
sudo systemctl show oriexa-worker --property=Environment
```

Recommended for mixed task types (including plain HTML/CSS/JS):

```bash
echo 'VERCEL_USE_LINKED_PROJECT=false' >> /opt/oriexa/repo/.env
```

Only set `VERCEL_USE_LINKED_PROJECT=true` if you intentionally want every task
deployment to use one preconfigured Vercel project.

If Vercel still fails from the agent:

```bash
sudo journalctl -u oriexa-swarm -n 150 --no-pager
sudo journalctl -u oriexa-worker -n 150 --no-pager
```

---

## Useful commands

```bash
# Live logs
sudo journalctl -u oriexa-api -f

# Restart service
sudo systemctl restart oriexa-api

# Stop service
sudo systemctl stop oriexa-api

# Check service status
sudo systemctl status oriexa-api

# Test DB connection
cd /opt/oriexa/repo && .venv/bin/python3 scripts/find_working_connection.py

# Open firewall for port 8000
ufw allow 8000/tcp
```

---

## Troubleshooting

| Error | Fix |
|---|---|
| `Network is unreachable` | IPv6 not enabled — follow Step 6 |
| `Tenant or user not found` | Using pooler URL instead of direct — use `db.PROJECT.supabase.co:5432` |
| `Could not parse SQLAlchemy URL` | Duplicate `DATABASE_URL=` key or spaces in URL — check with `grep DATABASE_URL .env \| cat -A` |
| `No module named 'app'` | Wrong Python — use `.venv/bin/alembic`, not system `alembic` |
| `alembic: command not found` | Venv not installed — run `uv pip install -e .` |
| Service not starting | Check logs: `sudo journalctl -u oriexa-api -n 50` |
| Vercel deploy fails from agent | Ensure `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID` are in `/opt/oriexa/repo/.env`, then restart `oriexa-swarm` and `oriexa-worker` |
| `Couldn't find any pages or app directory` | Deploy is being forced into a Next.js-configured project. Set `VERCEL_USE_LINKED_PROJECT=false` (or remove `VERCEL_PROJECT_ID`) and restart swarm/worker |
| IPv6 lost after reboot | Run the cloud-init disable command in Step 6 |

