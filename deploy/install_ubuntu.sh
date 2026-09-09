#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo "Usage: sudo ./deploy/install_ubuntu.sh DOMAIN EMAIL [APP_DIR] [APP_USER]" >&2
  exit 2
}

[[ $# -ge 2 ]] || usage
[[ ${EUID} -eq 0 ]] || { echo "Run this script with sudo." >&2; exit 1; }

DOMAIN=$1
EMAIL=$2
APP_DIR=${3:-/opt/steam-kakabase}
APP_USER=${4:-steamkb}
SOURCE_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
ADMIN_USER=${SUDO_USER:-}

[[ ${DOMAIN} =~ ^[A-Za-z0-9.-]+$ ]] || { echo "Invalid domain." >&2; exit 1; }
[[ ${EMAIL} =~ ^[^[:space:]@]+@[^[:space:]@]+$ ]] || { echo "Invalid email." >&2; exit 1; }
[[ ${APP_USER} =~ ^[a-z_][a-z0-9_-]*$ ]] || { echo "Invalid app user." >&2; exit 1; }
[[ ${APP_DIR} =~ ^/[A-Za-z0-9._/-]+$ ]] || { echo "Invalid app directory." >&2; exit 1; }

if [[ -z ${ADMIN_USER} || ${ADMIN_USER} == root ]]; then
  echo "Refusing to disable root SSH before a non-root sudo account is in use." >&2
  echo "Create and test a sudo user with an SSH key, then run this script through sudo." >&2
  exit 1
fi
ADMIN_HOME=$(getent passwd "${ADMIN_USER}" | cut -d: -f6)
if [[ -z ${ADMIN_HOME} || ! -s ${ADMIN_HOME}/.ssh/authorized_keys ]]; then
  echo "Refusing SSH hardening: ${ADMIN_USER} has no non-empty authorized_keys file." >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y python3 python3-venv nginx certbot python3-certbot-nginx ufw rclone rsync sqlite3 curl

if ! id "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --home-dir "/var/lib/${APP_USER}" --shell /usr/sbin/nologin "${APP_USER}"
fi

mkdir -p "${APP_DIR}"
if [[ $(realpath "${SOURCE_DIR}") != $(realpath "${APP_DIR}") ]]; then
  rsync -a --delete \
    --exclude .git --exclude .env --exclude data/ \
    "${SOURCE_DIR}/" "${APP_DIR}/"
fi

python3 -m venv "${APP_DIR}/.venv"
"${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip
"${APP_DIR}/.venv/bin/python" -m pip install -r "${APP_DIR}/requirements.txt"

mkdir -p "${APP_DIR}/data"
chown -R root:root "${APP_DIR}"
chown -R "${APP_USER}:${APP_USER}" "${APP_DIR}/data"
chmod 750 "${APP_DIR}/data"

set_env() {
  local key=$1 value=$2 file=${APP_DIR}/.env
  if grep -qE "^${key}=" "${file}"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "${file}"
  else
    printf '%s=%s\n' "${key}" "${value}" >> "${file}"
  fi
}

if [[ ! -f ${APP_DIR}/.env ]]; then
  install -m 640 -o root -g "${APP_USER}" "${APP_DIR}/.env.example" "${APP_DIR}/.env"
  ADMIN_TOKEN=$(python3 -c 'import secrets; print(secrets.token_urlsafe(48))')
  set_env ITAD_API_KEY ""
  set_env STEAM_API_KEY ""
  set_env STEAMKB_ENV production
  set_env STEAMKB_HOST 127.0.0.1
  set_env STEAMKB_PORT 8765
  set_env STEAMKB_ADMIN_TOKEN "${ADMIN_TOKEN}"
  set_env STEAMKB_ALLOWED_HOSTS "${DOMAIN},127.0.0.1,localhost"
  set_env STEAMKB_CORS_ALLOWED_ORIGINS ""
  set_env USE_PROXY false
  set_env STEAMKB_PROXY_URL ""
  echo "Created ${APP_DIR}/.env with a random admin token. Add API keys there after installation."
else
  chown root:"${APP_USER}" "${APP_DIR}/.env"
  chmod 640 "${APP_DIR}/.env"
fi

set_env STEAMKB_ENV production
set_env STEAMKB_HOST 127.0.0.1
set_env STEAMKB_PORT 8765
set_env STEAMKB_ALLOWED_HOSTS "${DOMAIN},127.0.0.1,localhost"
set_env STEAMKB_CORS_ALLOWED_ORIGINS ""

grep -qx 'STEAMKB_ENV=production' "${APP_DIR}/.env" || {
  echo "${APP_DIR}/.env must set STEAMKB_ENV=production" >&2
  exit 1
}
TOKEN_LENGTH=$(awk -F= '/^STEAMKB_ADMIN_TOKEN=/{sub(/^[^=]*=/, ""); print length; exit}' "${APP_DIR}/.env")
[[ ${TOKEN_LENGTH:-0} -ge 32 ]] || { echo "STEAMKB_ADMIN_TOKEN must be at least 32 characters." >&2; exit 1; }

render_template() {
  local source=$1 destination=$2
  sed -e "s|@@APP_DIR@@|${APP_DIR}|g" \
      -e "s|@@APP_USER@@|${APP_USER}|g" \
      -e "s|@@DOMAIN@@|${DOMAIN}|g" \
      "${source}" > "${destination}"
}

render_template "${APP_DIR}/deploy/systemd/steam-kakabase-web.service" /etc/systemd/system/steam-kakabase-web.service
render_template "${APP_DIR}/deploy/systemd/steam-kakabase-crawler.service" /etc/systemd/system/steam-kakabase-crawler.service
render_template "${APP_DIR}/deploy/systemd/steam-kakabase-backup.service" /etc/systemd/system/steam-kakabase-backup.service
install -m 644 "${APP_DIR}/deploy/systemd/steam-kakabase-backup.timer" /etc/systemd/system/steam-kakabase-backup.timer
install -m 644 "${APP_DIR}/deploy/nginx/steam-kakabase-rate-limits.conf" /etc/nginx/conf.d/steam-kakabase-rate-limits.conf
render_template "${APP_DIR}/deploy/nginx/steam-kakabase.conf" /etc/nginx/sites-available/steam-kakabase
ln -sfn /etc/nginx/sites-available/steam-kakabase /etc/nginx/sites-enabled/steam-kakabase
rm -f /etc/nginx/sites-enabled/default

nginx -t
systemctl daemon-reload
systemctl enable steam-kakabase-web.service steam-kakabase-crawler.service
systemctl restart steam-kakabase-web.service steam-kakabase-crawler.service
systemctl enable --now nginx.service

ready=0
for _attempt in $(seq 1 20); do
  if curl --fail --silent --show-error http://127.0.0.1:8765/ready >/dev/null; then
    ready=1
    break
  fi
  sleep 1
done
if [[ ${ready} -ne 1 ]]; then
  echo "Steam-KaKaBase web service did not become ready within 20 seconds." >&2
  systemctl status steam-kakabase-web.service --no-pager >&2 || true
  exit 1
fi

ufw default deny incoming
ufw default allow outgoing
ufw allow OpenSSH
ufw allow 'Nginx Full'
ufw --force enable

install -m 644 "${APP_DIR}/deploy/ssh/99-steam-kakabase-hardening.conf" /etc/ssh/sshd_config.d/99-steam-kakabase-hardening.conf
sshd -t
systemctl reload ssh.service

certbot --nginx --non-interactive --agree-tos --redirect \
  --email "${EMAIL}" --domains "${DOMAIN}"
nginx -t
systemctl reload nginx.service

echo "Deployment completed: https://${DOMAIN}"
echo "Offsite backup is not enabled until rclone and STEAMKB_OFFSITE_REMOTE are configured."
