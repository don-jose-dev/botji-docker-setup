# Cloud Deployment Guide

## Recommended server: Hostinger KVM 2

**$8.99/month** — 2 vCPU, 8GB RAM, NVMe SSD, 1Gbps  
OS: Ubuntu 22.04 or 24.04 LTS

DigitalOcean equivalent costs $24/month for less RAM. Hostinger wins on price/performance.

---

## Prerequisites

- GitHub account with this repo pushed
- Domain name pointed at your VPS IP (for dashboard HTTPS)
- `TELEGRAM_BOT_TOKEN` from @BotFather
- `OPENAI_API_KEY` (optional — for image generation)

---

## Step 1 — Provision the server

Buy Hostinger KVM 2, choose Ubuntu 22.04 LTS. SSH in as root.

```bash
# One-shot server setup (installs Docker, Caddy, rclone, creates deploy user)
export REPO_URL=https://github.com/YOUR_GITHUB_USER/botji-docker-setup.git
export DEPLOY_PATH=/opt/botji
curl -fsSL https://raw.githubusercontent.com/YOUR_GITHUB_USER/botji-docker-setup/main/scripts/server-setup.sh | bash
```

---

## Step 2 — Create the .env on the server

```bash
cd /opt/botji
cp .env.example .env
chmod 600 .env
nano .env
```

Required values:
```env
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_ALLOWED_USERS=your_telegram_user_id
API_SERVER_KEY=$(openssl rand -hex 32)
OPENAI_API_KEY=sk-...          # optional

BOTJI_DATA_DIR=/opt/botji/data
BOTJI_WORKSPACE_DIR=/opt/botji/workspace
BOTJI_PROD_IMAGE=ghcr.io/YOUR_GITHUB_USER/botji-hermes:latest
```

---

## Step 3 — Bootstrap and start

```bash
cd /opt/botji
make bootstrap
make up
systemctl start botji
```

Check it's healthy:
```bash
docker ps
make logs
```

---

## Step 4 — Set up HTTPS (Caddy)

```bash
# Edit domain
nano /opt/botji/caddy/Caddyfile
# Replace YOUR_DOMAIN with your actual domain

# Generate dashboard password
caddy hash-password
# Paste the output hash into Caddyfile where it says REPLACE_WITH_CADDY_HASH...

# Install and start
cp /opt/botji/caddy/Caddyfile /etc/caddy/Caddyfile
systemctl enable --now caddy
systemctl reload caddy
```

Your dashboard is now at `https://YOUR_DOMAIN` with basic auth.

---

## Step 5 — Set up CI/CD (GitHub Actions)

**Create a deploy SSH key:**
```bash
ssh-keygen -t ed25519 -f ~/.ssh/botji_deploy -N ""
cat ~/.ssh/botji_deploy.pub >> /home/botji/.ssh/authorized_keys
cat ~/.ssh/botji_deploy        # copy the private key
```

**Add these GitHub Secrets** (repo → Settings → Secrets → Actions):

| Secret | Value |
|--------|-------|
| `VPS_HOST` | Your server IP |
| `VPS_USER` | `botji` |
| `VPS_SSH_KEY` | Contents of `~/.ssh/botji_deploy` |
| `VPS_PORT` | `22` |
| `DEPLOY_PATH` | `/opt/botji` |

**Allow GitHub Actions to push to GHCR:**
Go to your GitHub profile → Packages → botji-hermes → Package Settings → Add repository access.

Now every push to `main` automatically builds, pushes, and deploys.

---

## Step 6 — Set up cloud backups

Configure rclone with your cloud storage provider (Backblaze B2 is cheapest):

```bash
rclone config
# Choose: n (new remote) → name: b2 → storage: Backblaze B2
# Enter your B2 Account ID and Application Key
```

Test:
```bash
cd /opt/botji
RCLONE_REMOTE=b2:botji-backups make backup-cloud
```

Enable daily automated backups:
```bash
systemctl enable --now botji-backup.timer
systemctl list-timers botji-backup.timer
```

---

## Operations reference

| Task | Command |
|------|---------|
| View logs | `make logs` |
| Apply config/SOUL change | `make reload` |
| Manual backup | `make snapshot` |
| Upload backup to cloud | `make backup-cloud` |
| Restore from backup | `make restore BACKUP=backups/botji-hermes-home-xxx.tgz` |
| Check health | `docker inspect botji-hermes --format='{{.State.Health.Status}}'` |
| Smoke test | `make smoke-agent` |
| Stop | `systemctl stop botji` |
| Start | `systemctl start botji` |

---

## Updating Botji

```bash
# CI/CD does this automatically on git push.
# Manual update:
cd /opt/botji
git pull
docker compose -f docker-compose.yml -f docker-compose.prod.yml pull
make reload
```

---

## Secrets rotation

| Secret | How to rotate |
|--------|--------------|
| `API_SERVER_KEY` | Generate new: `openssl rand -hex 32`. Update `.env`, run `make reload`. |
| `TELEGRAM_BOT_TOKEN` | Revoke in @BotFather, get new token, update `.env`, run `make reload`. |
| `OPENAI_API_KEY` | Revoke in OpenAI dashboard, update `.env`, run `make reload`. |
| Deploy SSH key | Generate new keypair, update server `authorized_keys` and GitHub secret. |

---

## Troubleshooting

**Bot not responding in Telegram**
```bash
docker logs botji-hermes --tail 50
# Check: is TELEGRAM_BOT_TOKEN correct? Is the container healthy?
```

**Container keeps restarting**
```bash
docker inspect botji-hermes | grep -A5 '"State"'
# Usually: out of memory → increase BOTJI_MEMORY_LIMIT in .env
```

**Dashboard not loading**
```bash
systemctl status caddy
# Check: is your domain DNS pointing to the server?
# Check: port 80/443 open in firewall?
ufw allow 80 && ufw allow 443
```

**Restore after data loss**
```bash
make restore BACKUP=backups/botji-hermes-home-20260516T030000Z.tgz
```
