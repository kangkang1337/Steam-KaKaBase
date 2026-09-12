"""Create a verified manual backup and expose its completion in monitoring."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config
from backend.db import now_iso, set_crawl_state, transaction
from backend.migrations import create_database_backup


def main():
    backup = create_database_backup(
        config.DB_PATH,
        config.DB_MIGRATION_BACKUP_DIR,
        keep=config.DB_MIGRATION_BACKUP_KEEP,
        retention_label="manual",
    )
    with transaction() as conn:
        set_crawl_state(conn, "daily_database_backup_at", now_iso())
        set_crawl_state(conn, "daily_database_backup_path", str(backup))
    print(f"Created verified manual backup: {backup}")


if __name__ == "__main__":
    main()
