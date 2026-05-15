#!/usr/bin/env bash
# One-shot VPS setup script for Ubuntu 22.04 / 24.04
# Run as root: curl -fsSL https://raw.githubusercontent.com/OWNER/REPO/main/scripts/server-setup.sh | bash
set -euo pipefail

DEPLOY_PATH="${DEPLOY_PATH:-/opt/botji}"
BOTJI_USER="${BOTJI_USER:-botji}"
REPO_URL="${REPO_URL:-https://github.com/OWNER/botji-docker-setup.git}"

echo "==> Updating system"
apt-get update -q && apt-get upgrade -yq

echo "==> Installing Docker"
curl -fsSL https://get.docker.com | sh
systemctl enable --now docker

echo "==> Installing Caddy"
apt-get install -yq debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/gpg.key | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt | tee /etc/apt/sources.list.d/caddy-stable.list
apt-get update -q && apt-get install -yq caddy

echo "==> Installing tools (git, make, rclone)"
apt-get install -yq git make curl unzip
curl https://rclone.org/install.sh | bash

echo "==> Creating deploy user and directories"
useradd -m -s /bin/bash "$BOTJI_USER" 2>/dev/null || true
usermod -aG docker "$BOTJI_USER"
mkdir -p "$DEPLOY_PATH" "$DEPLOY_PATH/data" "$DEPLOY_PATH/workspace" "$DEPLOY_PATH/backups"
chown -R "$BOTJI_USER:$BOTJI_USER" "$DEPLOY_PATH"

echo "==> Cloning repo"
if [ ! -d "$DEPLOY_PATH/.git" ]; then
    git clone "$REPO_URL" "$DEPLOY_PATH"
    chown -R "$BOTJI_USER:$BOTJI_USER" "$DEPLOY_PATH"
fi

echo "==> Setting up SSH key for CI deploy"
mkdir -p /home/$BOTJI_USER/.ssh
chmod 700 /home/$BOTJI_USER/.ssh
echo "# Paste your CI public key below, then save" > /home/$BOTJI_USER/.ssh/authorized_keys
chmod 600 /home/$BOTJI_USER/.ssh/authorized_keys
chown -R $BOTJI_USER:$BOTJI_USER /home/$BOTJI_USER/.ssh

echo "==> Installing systemd services"
cp "$DEPLOY_PATH/systemd/botji.service" /etc/systemd/system/
cp "$DEPLOY_PATH/systemd/botji-backup.service" /etc/systemd/system/
cp "$DEPLOY_PATH/systemd/botji-backup.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable botji botji-backup.timer

echo ""
echo "==> Setup complete. Next steps:"
echo "  1. Copy your .env to $DEPLOY_PATH/.env  (chmod 600)"
echo "  2. Set BOTJI_DATA_DIR=$DEPLOY_PATH/data in .env"
echo "  3. Set BOTJI_WORKSPACE_DIR=$DEPLOY_PATH/workspace in .env"
echo "  4. Edit $DEPLOY_PATH/caddy/Caddyfile — set your domain"
echo "  5. cp $DEPLOY_PATH/caddy/Caddyfile /etc/caddy/Caddyfile && systemctl reload caddy"
echo "  6. cd $DEPLOY_PATH && make bootstrap && make up"
echo "  7. systemctl start botji"
echo "  8. Add CI secrets to GitHub: VPS_HOST, VPS_USER, VPS_SSH_KEY, VPS_PORT"
