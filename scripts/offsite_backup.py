"""Create or reuse a fresh SQLite backup and upload it with rclone."""

import os
import socket
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config
from backend.db import set_crawl_state, transaction
from backend.migrations import create_database_backup


def latest_daily_backup(backup_dir, database_stem):
    candidates = sorted(
        Path(backup_dir).glob(f"{database_stem}-daily-*.sqlite3"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def ensure_fresh_backup(max_age_hours=20):
    latest = latest_daily_backup(config.DB_MIGRATION_BACKUP_DIR, config.DB_PATH.stem)
    fresh_after = time.time() - float(max_age_hours) * 3600
    if latest and latest.stat().st_mtime >= fresh_after:
        return latest
    return create_database_backup(
        config.DB_PATH,
        config.DB_MIGRATION_BACKUP_DIR,
        label="daily",
        keep=config.DB_DAILY_BACKUP_KEEP,
        retention_label="daily",
    )


def validate_backup(path):
    uri = f"file:{Path(path).resolve().as_posix()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        result = conn.execute("PRAGMA quick_check").fetchone()[0]
    if result != "ok":
        raise sqlite3.DatabaseError(f"backup integrity check failed: {result}")


def upload_backup(path, remote, *, hostname=None, retention_days=30, runner=subprocess.run):
    remote = str(remote or "").strip().rstrip("/")
    if ":" not in remote:
        raise ValueError("STEAMKB_OFFSITE_REMOTE must be an rclone remote path")
    host = hostname or socket.gethostname()
    remote_dir = f"{remote}/{host}"
    target = f"{remote_dir}/{Path(path).name}"
    runner(["rclone", "copyto", str(path), target], check=True)
    runner(
        [
            "rclone", "delete", remote_dir,
            "--min-age", f"{max(1, int(retention_days))}d",
            "--include", "*.sqlite3",
        ],
        check=True,
    )
    return target


def main():
    remote = os.getenv("STEAMKB_OFFSITE_REMOTE", "").strip()
    if not remote:
        raise SystemExit("STEAMKB_OFFSITE_REMOTE is not configured")
    retention_days = int(os.getenv("STEAMKB_OFFSITE_RETENTION_DAYS", "30"))
    backup = ensure_fresh_backup()
    validate_backup(backup)
    target = upload_backup(backup, remote, retention_days=retention_days)
    with transaction() as conn:
        set_crawl_state(conn, "offsite_database_backup_at", time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        set_crawl_state(conn, "offsite_database_backup_path", target)
    print(f"Uploaded verified database backup: {target}")


if __name__ == "__main__":
    main()
