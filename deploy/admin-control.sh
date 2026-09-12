#!/usr/bin/env bash
# Root-owned, fixed-action bridge. Never accept commands, paths, or unit names.
set -Eeuo pipefail
[[ ${EUID} -eq 0 && $# -eq 1 ]] || exit 64

case "$1" in
  local_backup) exec /bin/systemctl start --no-block steam-kakabase-local-backup.service ;;
  offsite_backup) exec /bin/systemctl start --no-block steam-kakabase-backup.service ;;
  recovery_drill) exec /bin/systemctl start --no-block steam-kakabase-recovery-drill.service ;;
  restart_crawler) exec /bin/systemctl restart steam-kakabase-crawler.service ;;
  restart_web) exec /bin/systemd-run --quiet --unit=steam-kakabase-admin-restart-web --on-active=3s /bin/systemctl restart steam-kakabase-web.service ;;
  *) exit 64 ;;
esac
