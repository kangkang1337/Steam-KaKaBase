#!/usr/bin/env bash
set -Eeuo pipefail

command -v nginx >/dev/null || { echo "nginx is required" >&2; exit 1; }
command -v systemd-analyze >/dev/null || { echo "systemd-analyze is required" >&2; exit 1; }

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
TEMP_DIR=$(mktemp -d)
trap 'rm -rf "${TEMP_DIR}"' EXIT

APP_DIR=${TEMP_DIR}/app
mkdir -p \
  "${APP_DIR}/.venv/bin" \
  "${APP_DIR}/data" \
  "${TEMP_DIR}/client_body" \
  "${TEMP_DIR}/proxy" \
  "${TEMP_DIR}/fastcgi" \
  "${TEMP_DIR}/uwsgi" \
  "${TEMP_DIR}/scgi"
ln -s "$(command -v python3)" "${APP_DIR}/.venv/bin/python"
touch "${APP_DIR}/.env" "${TEMP_DIR}/rclone.conf"

render_unit() {
  local name=$1
  sed -e "s|@@APP_DIR@@|${APP_DIR}|g" \
      -e 's|@@APP_USER@@|www-data|g' \
      -e "s|/etc/rclone/rclone.conf|${TEMP_DIR}/rclone.conf|g" \
      "${ROOT}/deploy/systemd/${name}" > "${TEMP_DIR}/${name}"
}

render_unit steam-kakabase-web.service
render_unit steam-kakabase-crawler.service
render_unit steam-kakabase-backup.service
cp "${ROOT}/deploy/systemd/steam-kakabase-backup.timer" "${TEMP_DIR}/steam-kakabase-backup.timer"

systemd-analyze verify \
  "${TEMP_DIR}/steam-kakabase-web.service" \
  "${TEMP_DIR}/steam-kakabase-crawler.service" \
  "${TEMP_DIR}/steam-kakabase-backup.service" \
  "${TEMP_DIR}/steam-kakabase-backup.timer"

sed -e 's|@@DOMAIN@@|steam.example.com|g' \
  -e 's|listen 80;|listen 18080;|g' \
  -e 's|listen \[::\]:80;|listen [::]:18080;|g' \
  "${ROOT}/deploy/nginx/steam-kakabase.conf" > "${TEMP_DIR}/site.conf"

cat > "${TEMP_DIR}/nginx.conf" <<EOF
pid ${TEMP_DIR}/nginx.pid;
error_log stderr;
events {}
http {
    access_log ${TEMP_DIR}/access.log;
    client_body_temp_path ${TEMP_DIR}/client_body;
    proxy_temp_path ${TEMP_DIR}/proxy;
    fastcgi_temp_path ${TEMP_DIR}/fastcgi;
    uwsgi_temp_path ${TEMP_DIR}/uwsgi;
    scgi_temp_path ${TEMP_DIR}/scgi;
    include /etc/nginx/mime.types;
    include ${ROOT}/deploy/nginx/steam-kakabase-rate-limits.conf;
    include ${TEMP_DIR}/site.conf;
}
EOF

nginx -p /etc/nginx -t -c "${TEMP_DIR}/nginx.conf"
echo "Linux deployment configuration check passed."
