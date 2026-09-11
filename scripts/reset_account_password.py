"""Reset one account after the administrator has verified its owner out of band."""

import getpass
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from backend import auth
from backend.db import init_db


def main():
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/reset_account_password.py USERNAME")
    password = getpass.getpass("New password: ")
    confirm = getpass.getpass("Confirm new password: ")
    if password != confirm:
        raise SystemExit("Passwords do not match.")
    init_db()
    auth.reset_password(sys.argv[1], password)
    print("Password reset; all existing sessions for this account were invalidated.")


if __name__ == "__main__":
    main()
