"""Restore a SQLite backup into a new directory and verify it is usable.

This is deliberately separate from ``manage_database.py restore``: it never
targets the configured production database and refuses an existing output
directory.  Use it for recovery drills, including downloaded offsite backups.
"""

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend.migrations import CURRENT_SCHEMA_VERSION, get_schema_version, migrate_database, restore_database_backup
from backend import config


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup", type=Path, help="SQLite backup to rehearse")
    parser.add_argument("output_dir", type=Path, help="new, empty directory for the restored copy")
    args = parser.parse_args()

    backup = args.backup.resolve()
    output_dir = args.output_dir.resolve()
    if not backup.is_file():
        parser.error(f"backup does not exist: {backup}")
    if output_dir.exists():
        parser.error(f"output directory already exists; refusing to overwrite: {output_dir}")

    output_dir.mkdir(parents=True)
    restored = output_dir / "steamkb.sqlite3"
    restore_database_backup(restored, backup)
    migration = migrate_database(restored, backup_dir=output_dir / "backups")

    with sqlite3.connect(restored) as conn:
        quick_check = conn.execute("PRAGMA quick_check").fetchone()[0]
        schema_version = get_schema_version(conn)
        games = conn.execute("SELECT count(*) FROM games").fetchone()[0]
    if quick_check != "ok" or schema_version != CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"restore verification failed: quick_check={quick_check!r}, schema=v{schema_version}"
        )

    marker = config.DATA_DIR / "runtime" / "recovery_drill.json"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(json.dumps({
        "success": True,
        "completed_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "source_name": backup.name,
        "schema": schema_version,
        "games": games,
        "quick_check": quick_check,
    }, ensure_ascii=False), encoding="utf-8")

    print(f"Restored copy: {restored}")
    print(f"Schema: v{schema_version}; games: {games}; quick_check: {quick_check}")
    print(f"Migration: v{migration['from_version']} -> v{migration['to_version']}")


if __name__ == "__main__":
    main()
