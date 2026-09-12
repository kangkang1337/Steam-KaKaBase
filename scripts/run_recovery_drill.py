"""Download the newest offsite SQLite backup and verify it in an isolated directory."""
import os
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def main():
    remote = os.getenv("STEAMKB_OFFSITE_REMOTE", "").strip().rstrip("/")
    if ":" not in remote:
        raise SystemExit("STEAMKB_OFFSITE_REMOTE is not configured")
    run_id = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    work = Path("/var/lib/steamkb-restore-drill")
    work.mkdir(parents=True, exist_ok=True)
    remote_dir = f"{remote}/{socket.gethostname()}"
    listed = subprocess.run(["rclone", "lsf", remote_dir, "--include", "*.sqlite3"], check=True, capture_output=True, text=True)
    backups = sorted(line.strip() for line in listed.stdout.splitlines() if line.strip())
    if not backups:
        raise SystemExit("no offsite SQLite backup was found")
    source_name = backups[-1]
    downloaded = work / f"offsite-{run_id}.sqlite3"
    output = work / f"result-{run_id}"
    subprocess.run(["rclone", "copyto", f"{remote_dir}/{source_name}", str(downloaded)], check=True)
    subprocess.run([sys.executable, str(ROOT / "scripts" / "rehearse_database_restore.py"), str(downloaded), str(output)], check=True)

if __name__ == "__main__":
    main()
