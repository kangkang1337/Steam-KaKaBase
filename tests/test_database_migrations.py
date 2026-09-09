import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from backend import migrations


def test_migrate_legacy_database_creates_backup_and_history(tmp_path):
    database = tmp_path / "legacy.sqlite3"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE legacy_marker(value TEXT)")
        conn.execute("INSERT INTO legacy_marker VALUES ('preserved')")

    result = migrations.migrate_database(database, backup_dir=backup_dir)

    assert result["from_version"] == 0
    assert result["to_version"] == migrations.CURRENT_SCHEMA_VERSION
    backup = Path(result["backup_path"])
    assert backup.is_file()
    with sqlite3.connect(database) as conn:
        assert migrations.get_schema_version(conn) == migrations.CURRENT_SCHEMA_VERSION
        assert conn.execute("SELECT value FROM legacy_marker").fetchone() == ("preserved",)
        history = conn.execute(
            "SELECT version, name FROM schema_migrations ORDER BY version"
        ).fetchall()
    assert [row[0] for row in history] == [1, 2, 3, 4, 5, 6]


def test_search_index_is_seeded_and_kept_in_sync(tmp_path):
    database = tmp_path / "search-index.sqlite3"
    migrations.migrate_database(database)

    with sqlite3.connect(database) as conn:
        conn.execute(
            "INSERT INTO steam_catalog(appid, name, updated_at) VALUES (367520, 'Hollow Knight', 'now')"
        )
        conn.execute(
            """
            INSERT INTO games(appid, name, short_description, updated_at)
            VALUES (367520, 'Hollow Knight', '在空洞骑士中探索地下王国', 'now')
            """
        )
        match = conn.execute(
            "SELECT appid FROM game_search_fts WHERE game_search_fts MATCH ?",
            ('"空洞骑士"',),
        ).fetchone()
        conn.execute(
            "UPDATE games SET name='Hollow Knight Updated' WHERE appid=367520"
        )
        updated = conn.execute(
            "SELECT name FROM game_search_fts WHERE rowid=367520"
        ).fetchone()

    assert match == (367520,)
    assert updated == ("Hollow Knight Updated",)


def test_current_database_does_not_create_redundant_backup(tmp_path):
    database = tmp_path / "current.sqlite3"
    backup_dir = tmp_path / "backups"
    migrations.migrate_database(database, backup_dir=backup_dir)

    result = migrations.migrate_database(database, backup_dir=backup_dir)

    assert result["applied"] == []
    assert result["backup_path"] is None
    assert not backup_dir.exists()


def test_failed_migration_rolls_back_and_keeps_recovery_backup(tmp_path):
    database = tmp_path / "rollback.sqlite3"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE marker(value TEXT)")
        conn.execute("INSERT INTO marker VALUES ('before')")

    def create_history(conn):
        conn.execute(
            "CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY, name TEXT, applied_at TEXT)"
        )

    def fail_after_write(conn):
        conn.execute("CREATE TABLE should_rollback(value TEXT)")
        raise RuntimeError("injected migration failure")

    planned = (
        migrations.Migration(1, "history", create_history),
        migrations.Migration(2, "failure", fail_after_write),
    )

    with pytest.raises(migrations.DatabaseMigrationError) as caught:
        migrations.migrate_database(
            database,
            backup_dir=backup_dir,
            migrations=planned,
        )

    assert caught.value.backup_path.is_file()
    with sqlite3.connect(database) as conn:
        assert migrations.get_schema_version(conn) == 0
        assert conn.execute("SELECT value FROM marker").fetchone() == ("before",)
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='should_rollback'"
        ).fetchone() is None
        assert conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
        ).fetchone() is None


def test_restore_replaces_database_with_valid_backup(tmp_path):
    database = tmp_path / "restore.sqlite3"
    backup_dir = tmp_path / "backups"
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("CREATE TABLE marker(value TEXT)")
        conn.execute("INSERT INTO marker VALUES ('original')")
        conn.commit()
    backup = migrations.create_database_backup(database, backup_dir)
    with closing(sqlite3.connect(database)) as conn:
        conn.execute("UPDATE marker SET value='changed'")
        conn.commit()

    migrations.restore_database_backup(database, backup)

    with closing(sqlite3.connect(database)) as conn:
        assert conn.execute("SELECT value FROM marker").fetchone() == ("original",)


def test_backup_retention_keeps_configured_count(tmp_path):
    database = tmp_path / "retention.sqlite3"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE marker(value TEXT)")
    for index in range(4):
        migrations.create_database_backup(database, backup_dir, label=f"manual-{index}", keep=2)

    assert len(list(backup_dir.glob("*.sqlite3"))) == 2


def test_daily_backup_retention_does_not_remove_manual_backup(tmp_path):
    database = tmp_path / "retention-by-kind.sqlite3"
    backup_dir = tmp_path / "backups"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE marker(value TEXT)")
    migrations.create_database_backup(database, backup_dir, label="manual", keep=10)
    for _ in range(3):
        migrations.create_database_backup(
            database,
            backup_dir,
            label="daily",
            keep=2,
            retention_label="daily",
        )
    assert len(list(backup_dir.glob("*-daily-*.sqlite3"))) == 2
    assert len(list(backup_dir.glob("*-manual-*.sqlite3"))) == 1
