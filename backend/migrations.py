"""Versioned, transactional SQLite schema migrations and backups."""

import os
import shutil
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


CURRENT_SCHEMA_VERSION = 4


class DatabaseMigrationError(RuntimeError):
    def __init__(self, message, *, backup_path=None):
        super().__init__(message)
        self.backup_path = Path(backup_path) if backup_path else None


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    apply: object


def _utc_stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _execute_sql(conn, sql):
    statement = ""
    for line in sql.splitlines():
        statement += line + "\n"
        if sqlite3.complete_statement(statement):
            if statement.strip():
                conn.execute(statement)
            statement = ""
    if statement.strip():
        raise sqlite3.OperationalError("incomplete migration SQL")


def _columns(conn, table):
    return {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}


def _add_column(conn, table, name, declaration):
    if name not in _columns(conn, table):
        conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")


def _migration_1_initial_schema(conn):
    _execute_sql(conn, """
    CREATE TABLE IF NOT EXISTS schema_migrations (
        version INTEGER PRIMARY KEY,
        name TEXT NOT NULL,
        applied_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS games (
        appid INTEGER PRIMARY KEY, name TEXT NOT NULL, header_image TEXT,
        short_description TEXT, developer TEXT, publisher TEXT, release_date TEXT,
        is_free INTEGER DEFAULT 0, screenshots_json TEXT, tracked INTEGER DEFAULT 1,
        updated_at TEXT
    );
    CREATE TABLE IF NOT EXISTS price_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, appid INTEGER NOT NULL, region TEXT NOT NULL,
        currency TEXT, initial INTEGER, final INTEGER, discount_percent INTEGER,
        final_formatted TEXT, source TEXT NOT NULL, fetched_at TEXT NOT NULL,
        FOREIGN KEY(appid) REFERENCES games(appid)
    );
    CREATE TABLE IF NOT EXISTS player_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, appid INTEGER NOT NULL,
        player_count INTEGER NOT NULL, fetched_at TEXT NOT NULL,
        FOREIGN KEY(appid) REFERENCES games(appid)
    );
    CREATE TABLE IF NOT EXISTS review_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT, appid INTEGER NOT NULL, review_score INTEGER,
        review_score_desc TEXT, total_positive INTEGER, total_negative INTEGER,
        total_reviews INTEGER, fetched_at TEXT NOT NULL,
        FOREIGN KEY(appid) REFERENCES games(appid)
    );
    CREATE TABLE IF NOT EXISTS hot_games (
        appid INTEGER PRIMARY KEY, rank INTEGER, name TEXT, current_players INTEGER,
        peak_players INTEGER, header_image TEXT, source TEXT NOT NULL, fetched_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS game_latest_state (
        appid INTEGER PRIMARY KEY, current_players INTEGER, players_updated_at TEXT,
        cn_price TEXT, cn_price_final INTEGER, cn_price_currency TEXT,
        cn_discount_percent INTEGER DEFAULT 0, price_updated_at TEXT, review_score REAL,
        total_reviews INTEGER, review_updated_at TEXT, metadata_updated_at TEXT,
        historical_low_cny REAL, historical_low_updated_at TEXT, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS historical_lows (
        appid INTEGER NOT NULL, itad_game_id TEXT NOT NULL, country TEXT NOT NULL,
        shop_id INTEGER, shop_name TEXT, currency TEXT, amount REAL, amount_int INTEGER,
        amount_cny REAL, regular_amount_int INTEGER, cut INTEGER, low_at TEXT,
        fetched_at TEXT NOT NULL, PRIMARY KEY(appid, country),
        FOREIGN KEY(appid) REFERENCES games(appid)
    );
    CREATE TABLE IF NOT EXISTS crawl_state (key TEXT PRIMARY KEY, value TEXT);
    CREATE TABLE IF NOT EXISTS crawl_tasks (
        appid INTEGER NOT NULL, task_type TEXT NOT NULL, priority INTEGER NOT NULL DEFAULT 0,
        status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT, attempt_count INTEGER NOT NULL DEFAULT 0, last_error TEXT,
        locked_until TEXT, completed_at TEXT, updated_at TEXT NOT NULL, generation INTEGER,
        PRIMARY KEY(appid, task_type), FOREIGN KEY(appid) REFERENCES games(appid)
    );
    CREATE TABLE IF NOT EXISTS steam_app_names (
        appid INTEGER PRIMARY KEY, name TEXT NOT NULL, updated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS niche_pool (
        appid INTEGER PRIMARY KEY, name TEXT NOT NULL, header_image TEXT,
        current_players INTEGER, peak_players INTEGER, review_score REAL,
        total_reviews INTEGER, cn_price TEXT, cn_price_final INTEGER,
        cn_price_currency TEXT, cn_discount_percent INTEGER DEFAULT 0,
        is_free INTEGER DEFAULT 0, release_date TEXT, weighted_score REAL,
        source TEXT NOT NULL DEFAULT 'steam_discovery', eligible INTEGER NOT NULL DEFAULT 0,
        fetched_at TEXT NOT NULL, evaluated_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS steam_catalog (
        appid INTEGER PRIMARY KEY, name TEXT NOT NULL,
        source TEXT NOT NULL DEFAULT 'steam_applist', updated_at TEXT NOT NULL,
        last_seen_at TEXT, app_type TEXT NOT NULL DEFAULT 'unknown',
        app_type_checked_at TEXT, scan_generation INTEGER, last_enriched_at TEXT,
        next_enrich_at TEXT, enrich_status TEXT NOT NULL DEFAULT 'pending',
        enrich_attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT
    );
    CREATE TABLE IF NOT EXISTS niche_recommendation_snapshots (
        recommendation_date TEXT PRIMARY KEY, appid INTEGER NOT NULL, name TEXT NOT NULL,
        current_players INTEGER, review_score REAL, total_reviews INTEGER,
        weighted_score REAL, created_at TEXT NOT NULL
    );
    CREATE TABLE IF NOT EXISTS daily_home_snapshots (
        recommendation_date TEXT PRIMARY KEY, historical_low_appid INTEGER,
        meme_url TEXT, created_at TEXT NOT NULL
    );
    """)


def _migration_2_legacy_columns(conn):
    _add_column(conn, "games", "screenshots_json", "TEXT")
    _add_column(conn, "games", "itad_game_id", "TEXT")
    _add_column(conn, "niche_pool", "release_date", "TEXT")
    _add_column(conn, "niche_pool", "peak_players", "INTEGER")
    _add_column(conn, "crawl_tasks", "status", "TEXT NOT NULL DEFAULT 'pending'")
    _add_column(conn, "crawl_tasks", "attempts", "INTEGER NOT NULL DEFAULT 0")
    _add_column(conn, "crawl_tasks", "generation", "INTEGER")
    conn.execute("""
        UPDATE niche_pool SET peak_players=COALESCE(
            (SELECT MAX(p.player_count) FROM player_snapshots p WHERE p.appid=niche_pool.appid),
            current_players, 0
        )
    """)


def _migration_3_catalog_classification(conn):
    _add_column(conn, "steam_catalog", "last_seen_at", "TEXT")
    _add_column(conn, "steam_catalog", "app_type", "TEXT NOT NULL DEFAULT 'unknown'")
    _add_column(conn, "steam_catalog", "app_type_checked_at", "TEXT")
    _add_column(conn, "steam_catalog", "scan_generation", "INTEGER")
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    conn.execute("""
        UPDATE steam_catalog
        SET app_type='game', app_type_checked_at=COALESCE(app_type_checked_at, last_enriched_at, updated_at)
        WHERE app_type='unknown' AND enrich_status='done'
    """)
    conn.execute("""
        UPDATE steam_catalog SET enrich_status='pending', next_enrich_at=?, last_error=NULL
        WHERE app_type='unknown' AND enrich_status='skipped' AND app_type_checked_at IS NULL
    """, (stamp,))


def _migration_4_indexes_triggers_and_cleanup(conn):
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    _execute_sql(conn, """
    CREATE UNIQUE INDEX IF NOT EXISTS idx_games_appid ON games(appid);
    CREATE INDEX IF NOT EXISTS idx_price_app_region_time ON price_snapshots(appid, region, fetched_at);
    CREATE INDEX IF NOT EXISTS idx_players_app_time ON player_snapshots(appid, fetched_at);
    CREATE INDEX IF NOT EXISTS idx_reviews_app_time ON review_snapshots(appid, fetched_at);
    CREATE INDEX IF NOT EXISTS idx_hot_games_rank ON hot_games(rank);
    CREATE INDEX IF NOT EXISTS idx_hot_games_players ON hot_games(current_players);
    CREATE INDEX IF NOT EXISTS idx_game_latest_state_updated ON game_latest_state(updated_at);
    CREATE INDEX IF NOT EXISTS idx_historical_lows_app_country ON historical_lows(appid, country);
    CREATE INDEX IF NOT EXISTS idx_crawl_tasks_due ON crawl_tasks(task_type, next_attempt_at, priority, locked_until);
    CREATE INDEX IF NOT EXISTS idx_niche_pool_eligible ON niche_pool(eligible, weighted_score DESC);
    CREATE INDEX IF NOT EXISTS idx_catalog_enrich_queue ON steam_catalog(enrich_status, next_enrich_at, updated_at);
    CREATE INDEX IF NOT EXISTS idx_catalog_type_name ON steam_catalog(app_type, name);
    CREATE INDEX IF NOT EXISTS idx_catalog_scan_generation ON steam_catalog(scan_generation, appid);
    CREATE TRIGGER IF NOT EXISTS latest_player_snapshot AFTER INSERT ON player_snapshots BEGIN
      INSERT INTO game_latest_state(appid, current_players, players_updated_at, updated_at)
      VALUES (NEW.appid, NEW.player_count, NEW.fetched_at, NEW.fetched_at)
      ON CONFLICT(appid) DO UPDATE SET
        current_players=CASE WHEN excluded.players_updated_at >= game_latest_state.players_updated_at OR game_latest_state.players_updated_at IS NULL THEN excluded.current_players ELSE game_latest_state.current_players END,
        players_updated_at=MAX(COALESCE(game_latest_state.players_updated_at, ''), excluded.players_updated_at),
        updated_at=MAX(game_latest_state.updated_at, excluded.updated_at);
    END;
    CREATE TRIGGER IF NOT EXISTS latest_review_snapshot AFTER INSERT ON review_snapshots BEGIN
      INSERT INTO game_latest_state(appid, review_score, total_reviews, review_updated_at, updated_at)
      VALUES (NEW.appid, NEW.review_score, NEW.total_reviews, NEW.fetched_at, NEW.fetched_at)
      ON CONFLICT(appid) DO UPDATE SET
        review_score=CASE WHEN excluded.review_updated_at >= game_latest_state.review_updated_at OR game_latest_state.review_updated_at IS NULL THEN excluded.review_score ELSE game_latest_state.review_score END,
        total_reviews=CASE WHEN excluded.review_updated_at >= game_latest_state.review_updated_at OR game_latest_state.review_updated_at IS NULL THEN excluded.total_reviews ELSE game_latest_state.total_reviews END,
        review_updated_at=MAX(COALESCE(game_latest_state.review_updated_at, ''), excluded.review_updated_at),
        updated_at=MAX(game_latest_state.updated_at, excluded.updated_at);
    END;
    CREATE TRIGGER IF NOT EXISTS latest_cn_price_snapshot AFTER INSERT ON price_snapshots WHEN NEW.region='CN' BEGIN
      INSERT INTO game_latest_state(appid, cn_price, cn_price_final, cn_price_currency, cn_discount_percent, price_updated_at, updated_at)
      VALUES (NEW.appid, NEW.final_formatted, NEW.final, NEW.currency, NEW.discount_percent, NEW.fetched_at, NEW.fetched_at)
      ON CONFLICT(appid) DO UPDATE SET
        cn_price=CASE WHEN excluded.price_updated_at >= game_latest_state.price_updated_at OR game_latest_state.price_updated_at IS NULL THEN excluded.cn_price ELSE game_latest_state.cn_price END,
        cn_price_final=CASE WHEN excluded.price_updated_at >= game_latest_state.price_updated_at OR game_latest_state.price_updated_at IS NULL THEN excluded.cn_price_final ELSE game_latest_state.cn_price_final END,
        cn_price_currency=CASE WHEN excluded.price_updated_at >= game_latest_state.price_updated_at OR game_latest_state.price_updated_at IS NULL THEN excluded.cn_price_currency ELSE game_latest_state.cn_price_currency END,
        cn_discount_percent=CASE WHEN excluded.price_updated_at >= game_latest_state.price_updated_at OR game_latest_state.price_updated_at IS NULL THEN excluded.cn_discount_percent ELSE game_latest_state.cn_discount_percent END,
        price_updated_at=MAX(COALESCE(game_latest_state.price_updated_at, ''), excluded.price_updated_at),
        updated_at=MAX(game_latest_state.updated_at, excluded.updated_at);
    END;
    CREATE TRIGGER IF NOT EXISTS latest_cn_historical_low AFTER INSERT ON historical_lows WHEN NEW.country='CN' BEGIN
      INSERT INTO game_latest_state(appid, historical_low_cny, historical_low_updated_at, updated_at)
      VALUES (NEW.appid, NEW.amount_cny, NEW.fetched_at, NEW.fetched_at)
      ON CONFLICT(appid) DO UPDATE SET historical_low_cny=excluded.historical_low_cny,
        historical_low_updated_at=excluded.historical_low_updated_at,
        updated_at=MAX(game_latest_state.updated_at, excluded.updated_at);
    END;
    """)
    conn.execute("""
        DELETE FROM niche_pool WHERE appid IN (
            SELECT appid FROM steam_catalog WHERE app_type NOT IN ('unknown', 'game')
        )
    """)
    conn.execute("""
        UPDATE crawl_tasks SET status='skipped', locked_until=NULL,
            last_error='screenshots disabled: Steam-KaKaBase now loads header images only', updated_at=?
        WHERE task_type='screenshots' AND status IN ('pending', 'retry', 'running')
    """, (stamp,))
    conn.execute("""
        UPDATE crawl_tasks SET status='not_available', completed_at=?, locked_until=NULL,
            last_error='merged into preview task', updated_at=?
        WHERE task_type IN ('price', 'static') AND status IN ('pending', 'retry', 'running')
    """, (stamp, stamp))
    conn.execute("UPDATE hot_games SET name=NULL WHERE name LIKE 'App %' OR name LIKE 'Steam App %'")
    conn.execute("""
        UPDATE games SET name='未命名游戏', updated_at=?
        WHERE name LIKE 'App %' OR name LIKE 'Steam App %'
    """, (stamp,))


MIGRATIONS = (
    Migration(1, "initial_schema", _migration_1_initial_schema),
    Migration(2, "legacy_columns", _migration_2_legacy_columns),
    Migration(3, "catalog_classification", _migration_3_catalog_classification),
    Migration(4, "indexes_triggers_and_cleanup", _migration_4_indexes_triggers_and_cleanup),
)


def get_schema_version(conn):
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _has_user_tables(conn):
    return bool(conn.execute("""
        SELECT 1 FROM sqlite_master
        WHERE type='table' AND name NOT LIKE 'sqlite_%'
        LIMIT 1
    """).fetchone())


def create_database_backup(db_path, backup_dir=None, *, label="manual", keep=10):
    db_path = Path(db_path)
    if not db_path.is_file():
        raise FileNotFoundError(db_path)
    backup_dir = Path(backup_dir or db_path.parent / "backups")
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"{db_path.stem}-{label}-{_utc_stamp()}-{uuid4().hex[:8]}.sqlite3"
    source = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
    target = sqlite3.connect(destination)
    try:
        source.backup(target)
        if target.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("backup integrity check failed")
    finally:
        target.close()
        source.close()
    backups = sorted(backup_dir.glob(f"{db_path.stem}-*.sqlite3"), key=lambda path: path.stat().st_mtime, reverse=True)
    for old in backups[max(1, int(keep)):]:
        old.unlink(missing_ok=True)
    return destination


def restore_database_backup(db_path, backup_path):
    db_path = Path(db_path)
    backup_path = Path(backup_path)
    if not backup_path.is_file():
        raise FileNotFoundError(backup_path)
    if db_path.resolve() == backup_path.resolve():
        raise ValueError("backup path must differ from database path")
    source = sqlite3.connect(f"file:{backup_path.resolve().as_posix()}?mode=ro", uri=True)
    try:
        if source.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            raise sqlite3.DatabaseError("backup integrity check failed")
    finally:
        source.close()
    temporary = db_path.with_name(f".{db_path.name}.restore-{uuid4().hex}.tmp")
    previous = db_path.with_name(f".{db_path.name}.previous-{uuid4().hex}.tmp")
    shutil.copy2(backup_path, temporary)
    for suffix in ("-wal", "-shm"):
        Path(f"{db_path}{suffix}").unlink(missing_ok=True)
    if db_path.exists():
        os.replace(db_path, previous)
    try:
        os.replace(temporary, db_path)
    except Exception:
        if previous.exists():
            os.replace(previous, db_path)
        raise
    finally:
        temporary.unlink(missing_ok=True)
    previous.unlink(missing_ok=True)


def migrate_database(db_path, *, timeout=30, backup_dir=None, backup_keep=10, migrations=None):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    migrations = tuple(migrations or MIGRATIONS)
    target_version = max((migration.version for migration in migrations), default=0)
    conn = sqlite3.connect(db_path, timeout=timeout, isolation_level=None)
    backup_path = None
    try:
        conn.execute(f"PRAGMA busy_timeout = {int(timeout * 1000)}")
        current_version = get_schema_version(conn)
        if current_version > target_version:
            raise DatabaseMigrationError(
                f"database schema v{current_version} is newer than supported v{target_version}"
            )
        pending = [migration for migration in migrations if migration.version > current_version]
        if not pending:
            return {"from_version": current_version, "to_version": current_version, "backup_path": None, "applied": []}
        if _has_user_tables(conn):
            conn.close()
            backup_path = create_database_backup(
                db_path,
                backup_dir,
                label=f"before-v{current_version}-to-v{target_version}",
                keep=backup_keep,
            )
            conn = sqlite3.connect(db_path, timeout=timeout, isolation_level=None)
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("BEGIN IMMEDIATE")
        applied = []
        for migration in pending:
            migration.apply(conn)
            conn.execute(f"PRAGMA user_version = {migration.version}")
            conn.execute(
                "INSERT OR REPLACE INTO schema_migrations(version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
            )
            applied.append(migration.version)
        conn.commit()
        return {
            "from_version": current_version,
            "to_version": target_version,
            "backup_path": str(backup_path) if backup_path else None,
            "applied": applied,
        }
    except Exception as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        if isinstance(exc, DatabaseMigrationError):
            raise
        message = f"database migration failed: {exc}"
        if backup_path:
            message += f"; backup: {backup_path}"
        raise DatabaseMigrationError(message, backup_path=backup_path) from exc
    finally:
        conn.close()


def validate_schema(conn):
    version = get_schema_version(conn)
    if version != CURRENT_SCHEMA_VERSION:
        raise DatabaseMigrationError(
            f"database schema v{version} is not current v{CURRENT_SCHEMA_VERSION}"
        )
    return version
