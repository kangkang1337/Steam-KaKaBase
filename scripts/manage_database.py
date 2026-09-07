"""Inspect, migrate, back up, or restore the Steam-KaKaBase database."""

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config
from backend.migrations import (
    CURRENT_SCHEMA_VERSION,
    create_database_backup,
    get_schema_version,
    migrate_database,
    restore_database_backup,
)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("status")
    subparsers.add_parser("migrate")
    subparsers.add_parser("backup")
    restore = subparsers.add_parser("restore")
    restore.add_argument("backup", type=Path)
    restore.add_argument("--confirm", action="store_true")
    args = parser.parse_args()

    if args.command == "status":
        with sqlite3.connect(config.DB_PATH) as conn:
            version = get_schema_version(conn)
        print(f"Database: {config.DB_PATH}")
        print(f"Schema: v{version} / v{CURRENT_SCHEMA_VERSION}")
        return

    if args.command == "migrate":
        result = migrate_database(
            config.DB_PATH,
            backup_dir=config.DB_MIGRATION_BACKUP_DIR,
            backup_keep=config.DB_MIGRATION_BACKUP_KEEP,
        )
        print(result)
        return

    if args.command == "backup":
        path = create_database_backup(
            config.DB_PATH,
            config.DB_MIGRATION_BACKUP_DIR,
            keep=config.DB_MIGRATION_BACKUP_KEEP,
        )
        print(path)
        return

    if not args.confirm:
        parser.error("restore requires --confirm; stop the backend before restoring")
    safety = create_database_backup(
        config.DB_PATH,
        config.DB_MIGRATION_BACKUP_DIR,
        label="before-restore",
        keep=10_000,
    )
    restore_database_backup(config.DB_PATH, args.backup)
    print(f"Restored: {args.backup}")
    print(f"Previous database backup: {safety}")


if __name__ == "__main__":
    main()
