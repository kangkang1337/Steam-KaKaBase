"""Root systemd socket broker for a small allowlist of maintenance actions."""

import json
import os
import socket
import subprocess


ACTIONS = {
    "local_backup": ["/bin/systemctl", "start", "--no-block", "steam-kakabase-local-backup.service"],
    "offsite_backup": ["/bin/systemctl", "start", "--no-block", "steam-kakabase-backup.service"],
    "recovery_drill": ["/bin/systemctl", "start", "--no-block", "steam-kakabase-recovery-drill.service"],
    "restart_crawler": ["/bin/systemctl", "restart", "steam-kakabase-crawler.service"],
    "restart_web": ["/bin/systemd-run", "--quiet", "--collect", "--unit=steam-kakabase-admin-restart-web", "--on-active=3s", "/bin/systemctl", "restart", "steam-kakabase-web.service"],
}


def execute(action, runner=subprocess.run):
    command = ACTIONS.get(action)
    if command is None:
        return {"ok": False, "detail": "unsupported action"}
    result = runner(command, check=False, capture_output=True, text=True, timeout=10)
    detail = (result.stderr or result.stdout or "queued").strip().replace("\n", " ")[:300]
    return {"ok": result.returncode == 0, "detail": detail}


def main():
    if int(os.environ.get("LISTEN_PID", "0")) != os.getpid() or int(os.environ.get("LISTEN_FDS", "0")) < 1:
        raise SystemExit("must be started by systemd socket activation")
    listener = socket.socket(fileno=3)
    while True:
        connection, _ = listener.accept()
        with connection:
            try:
                raw = connection.recv(512)
                request = json.loads(raw.decode("utf-8"))
                action = request.get("action") if isinstance(request, dict) else None
                response = execute(action)
            except (OSError, ValueError, subprocess.SubprocessError) as exc:
                response = {"ok": False, "detail": type(exc).__name__}
            connection.sendall(json.dumps(response, separators=(",", ":")).encode("utf-8"))


if __name__ == "__main__":
    main()
