import gc
import sqlite3
import time
import uuid
from pathlib import Path

import pytest

from backend import _runtime
from backend import config, db


@pytest.fixture
def isolated_runtime(monkeypatch):
    """Run database tests against a fresh SQLite file for every test."""
    temp_dir = Path(__file__).parent / ".tmp"
    temp_dir.mkdir(exist_ok=True)
    test_id = uuid.uuid4().hex
    test_db = temp_dir / f"{test_id}.sqlite3"
    test_log = temp_dir / f"{test_id}.log"
    opened_connections = []
    original_connect = sqlite3.connect

    def tracked_connect(*args, **kwargs):
        connection = original_connect(*args, **kwargs)
        opened_connections.append(connection)
        return connection

    monkeypatch.setattr(sqlite3, "connect", tracked_connect)
    monkeypatch.setattr(_runtime, "DB_PATH", test_db)
    monkeypatch.setattr(_runtime, "LOG_PATH", test_log)
    monkeypatch.setattr(config, "DB_PATH", test_db)
    monkeypatch.setattr(config, "LOG_PATH", test_log)
    monkeypatch.setattr(db, "DB_PATH", test_db)

    _runtime.init_db()
    try:
        yield _runtime
    finally:
        for connection in reversed(opened_connections):
            try:
                connection.close()
            except sqlite3.ProgrammingError:
                pass
        gc.collect()
        for path in (test_db, Path(f"{test_db}-shm"), Path(f"{test_db}-wal"), test_log):
            for attempt in range(10):
                try:
                    path.unlink(missing_ok=True)
                    break
                except PermissionError:
                    if attempt == 9:
                        # sqlite3 context managers commit but do not guarantee that
                        # Windows releases the file handle before fixture teardown.
                        break
                    gc.collect()
                    time.sleep(0.05)


@pytest.fixture
def insert_game(isolated_runtime):
    def insert(appid=730, name="Counter-Strike 2", tracked=0):
        runtime = isolated_runtime
        with sqlite3.connect(runtime.DB_PATH) as conn:
            conn.execute(
                "INSERT INTO games(appid, name, tracked, updated_at) VALUES (?, ?, ?, ?)",
                (appid, name, tracked, runtime.now_iso()),
            )
        return appid

    return insert
