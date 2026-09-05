"""SQLite lifecycle, connections and crawl-task persistence."""

import sqlite3
from contextlib import contextmanager

from .config import DB_PATH, DB_TIMEOUT_SECONDS
from ._runtime import (
    claim_crawl_tasks,
    cleanup_old_records_once,
    compact_player_snapshots_once,
    compact_price_snapshots_once,
    complete_crawl_tasks,
    enqueue_crawl_tasks,
    enqueue_crawl_tasks_in_conn,
    ensure_schema,
    fail_crawl_tasks,
    get_crawl_state,
    init_db,
    mark_crawl_tasks_not_available,
    set_crawl_state,
)


def connect(*, rows=False):
    conn = sqlite3.connect(DB_PATH, timeout=DB_TIMEOUT_SECONDS)
    if rows:
        conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def transaction(*, rows=False):
    conn = connect(rows=rows)
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


__all__ = [
    "claim_crawl_tasks", "cleanup_old_records_once", "compact_player_snapshots_once",
    "compact_price_snapshots_once", "complete_crawl_tasks", "connect",
    "enqueue_crawl_tasks", "enqueue_crawl_tasks_in_conn", "ensure_schema",
    "fail_crawl_tasks", "get_crawl_state", "init_db",
    "mark_crawl_tasks_not_available", "set_crawl_state", "transaction",
]
