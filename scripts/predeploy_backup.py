"""Create a verified SQLite snapshot before an in-place deployment."""

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
os.chdir(ROOT)
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import config
from backend.migrations import create_database_backup


def main():
    if not config.DB_PATH.is_file():
        print("No existing SQLite database; pre-deployment backup skipped.")
        return
    backup = create_database_backup(
        config.DB_PATH,
        config.DB_MIGRATION_BACKUP_DIR,
        label="pre-deploy",
        keep=config.DB_MIGRATION_BACKUP_KEEP,
        retention_label="pre-deploy",
    )
    print(f"Created verified pre-deployment backup: {backup}")


if __name__ == "__main__":
    main()
