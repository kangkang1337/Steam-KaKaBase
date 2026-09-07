import sqlite3
from contextlib import closing
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


FIXED_NOW = datetime(2026, 9, 7, 12, 0, tzinfo=timezone.utc)
TEST_TEMP = Path(__file__).parent / ".tmp"


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return FIXED_NOW if tz else FIXED_NOW.replace(tzinfo=None)


def stamp(*, days=0, hours=0):
    return (FIXED_NOW - timedelta(days=days, hours=hours)).replace(microsecond=0).isoformat()


def test_init_db_migrates_legacy_schema_idempotently(monkeypatch):
    from backend import _runtime as runtime

    TEST_TEMP.mkdir(exist_ok=True)
    token = uuid4().hex
    database = TEST_TEMP / f"legacy-{token}.sqlite3"
    log_path = TEST_TEMP / f"legacy-{token}.log"
    monkeypatch.setattr(runtime, "DB_PATH", database)
    monkeypatch.setattr(runtime, "LOG_PATH", log_path)
    with closing(sqlite3.connect(database)) as conn:
        conn.executescript(
            """
            CREATE TABLE games (
                appid INTEGER PRIMARY KEY, name TEXT NOT NULL, header_image TEXT,
                short_description TEXT, developer TEXT, publisher TEXT,
                release_date TEXT, is_free INTEGER DEFAULT 0,
                tracked INTEGER DEFAULT 1, updated_at TEXT
            );
            CREATE TABLE player_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT, appid INTEGER NOT NULL,
                player_count INTEGER NOT NULL, fetched_at TEXT NOT NULL
            );
            CREATE TABLE niche_pool (
                appid INTEGER PRIMARY KEY, name TEXT NOT NULL, header_image TEXT,
                current_players INTEGER, review_score REAL, total_reviews INTEGER,
                cn_price TEXT, cn_price_final INTEGER, cn_price_currency TEXT,
                cn_discount_percent INTEGER DEFAULT 0, is_free INTEGER DEFAULT 0,
                weighted_score REAL, source TEXT NOT NULL DEFAULT 'steam_discovery',
                eligible INTEGER NOT NULL DEFAULT 0, fetched_at TEXT NOT NULL,
                evaluated_at TEXT NOT NULL
            );
            CREATE TABLE crawl_tasks (
                appid INTEGER NOT NULL, task_type TEXT NOT NULL,
                priority INTEGER NOT NULL DEFAULT 0, next_attempt_at TEXT,
                attempt_count INTEGER NOT NULL DEFAULT 0, last_error TEXT,
                locked_until TEXT, completed_at TEXT, updated_at TEXT NOT NULL,
                PRIMARY KEY(appid, task_type)
            );
            CREATE TABLE hot_games (
                appid INTEGER PRIMARY KEY, rank INTEGER, name TEXT,
                current_players INTEGER, peak_players INTEGER, header_image TEXT,
                source TEXT NOT NULL, fetched_at TEXT NOT NULL
            );
            CREATE TABLE steam_catalog (
                appid INTEGER PRIMARY KEY, name TEXT NOT NULL,
                source TEXT NOT NULL DEFAULT 'steam_applist', updated_at TEXT NOT NULL,
                last_enriched_at TEXT, next_enrich_at TEXT,
                enrich_status TEXT NOT NULL DEFAULT 'pending',
                enrich_attempts INTEGER NOT NULL DEFAULT 0, last_error TEXT
            );
            INSERT INTO games(appid, name, tracked, updated_at)
            VALUES (10, 'Legacy Game', 0, '2026-01-01T00:00:00+00:00');
            INSERT INTO player_snapshots(appid, player_count, fetched_at)
            VALUES (10, 25, '2026-01-01T00:00:00+00:00'),
                   (10, 80, '2026-02-01T00:00:00+00:00');
            INSERT INTO niche_pool(
                appid, name, current_players, weighted_score, eligible,
                fetched_at, evaluated_at
            ) VALUES (10, 'Legacy Game', 25, 1, 1,
                      '2026-02-01T00:00:00+00:00', '2026-02-01T00:00:00+00:00');
            INSERT INTO crawl_tasks(
                appid, task_type, priority, next_attempt_at, updated_at
            ) VALUES (10, 'screenshots', 10,
                      '2026-02-01T00:00:00+00:00', '2026-02-01T00:00:00+00:00');
            INSERT INTO steam_catalog(
                appid, name, updated_at, last_enriched_at, enrich_status
            ) VALUES (10, 'Legacy Game', '2026-02-01T00:00:00+00:00',
                      '2026-02-01T00:00:00+00:00', 'done');
            """
        )

    runtime.init_db()
    runtime.init_db()

    with closing(sqlite3.connect(database)) as conn:
        game_columns = {row[1] for row in conn.execute("PRAGMA table_info(games)")}
        niche_columns = {row[1] for row in conn.execute("PRAGMA table_info(niche_pool)")}
        task_columns = {row[1] for row in conn.execute("PRAGMA table_info(crawl_tasks)")}
        catalog_columns = {row[1] for row in conn.execute("PRAGMA table_info(steam_catalog)")}
        peak = conn.execute("SELECT peak_players FROM niche_pool WHERE appid = 10").fetchone()[0]
        task = conn.execute(
            "SELECT status, last_error FROM crawl_tasks WHERE appid = 10 AND task_type = 'screenshots'"
        ).fetchone()
        catalog_type = conn.execute(
            "SELECT app_type FROM steam_catalog WHERE appid = 10"
        ).fetchone()[0]

    assert {"screenshots_json", "itad_game_id"} <= game_columns
    assert {"release_date", "peak_players"} <= niche_columns
    assert {"status", "attempts", "generation"} <= task_columns
    assert {"last_seen_at", "app_type", "app_type_checked_at", "scan_generation"} <= catalog_columns
    assert peak == 80
    assert task[0] == "skipped"
    assert "screenshots disabled" in task[1]
    assert catalog_type == "game"
    for path in (database, Path(f"{database}-wal"), Path(f"{database}-shm"), log_path):
        path.unlink(missing_ok=True)


def test_player_history_compaction_boundaries(isolated_runtime, monkeypatch, insert_game):
    runtime = isolated_runtime
    appid = insert_game(101, "Player History")
    monkeypatch.setattr(runtime, "datetime", FrozenDateTime)
    rows = [
        (appid, 1, stamp(days=1, hours=1)),
        (appid, 2, stamp(days=1, hours=2)),
        (appid, 3, stamp(days=10, hours=1)),
        (appid, 4, stamp(days=10, hours=2)),
        (appid, 5, "2025-08-03T10:00:00+00:00"),
        (appid, 6, "2025-08-20T10:00:00+00:00"),
        (appid, 7, stamp(days=731)),
    ]
    with sqlite3.connect(runtime.DB_PATH) as conn:
        conn.executemany(
            "INSERT INTO player_snapshots(appid, player_count, fetched_at) VALUES (?, ?, ?)",
            rows,
        )

    assert runtime.compact_player_snapshots_once() is True
    assert runtime.compact_player_snapshots_once() is False
    with sqlite3.connect(runtime.DB_PATH) as conn:
        kept = conn.execute(
            "SELECT player_count FROM player_snapshots WHERE appid = ? ORDER BY player_count",
            (appid,),
        ).fetchall()

    assert [row[0] for row in kept] == [1, 2, 3, 5]


def test_price_history_compaction_preserves_series_boundaries(isolated_runtime, monkeypatch, insert_game):
    runtime = isolated_runtime
    appid = insert_game(102, "Price History")
    monkeypatch.setattr(runtime, "datetime", FrozenDateTime)

    def price(value, fetched_at, region="CN", source="steam"):
        return (appid, region, "CNY", value, value, 0, f"¥{value / 100:.2f}", source, fetched_at)

    rows = [
        price(100, stamp(days=1, hours=1)),
        price(200, stamp(days=1, hours=2)),
        price(300, stamp(days=40, hours=1)),
        price(400, stamp(days=40, hours=2)),
        price(500, "2025-08-03T10:00:00+00:00"),
        price(600, "2025-08-20T10:00:00+00:00"),
        price(700, stamp(days=731)),
        price(800, stamp(days=40, hours=2), source="itad"),
    ]
    with sqlite3.connect(runtime.DB_PATH) as conn:
        conn.executemany(
            """
            INSERT INTO price_snapshots(
                appid, region, currency, initial, final, discount_percent,
                final_formatted, source, fetched_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )

    assert runtime.compact_price_snapshots_once() is True
    assert runtime.compact_price_snapshots_once() is False
    with sqlite3.connect(runtime.DB_PATH) as conn:
        kept = conn.execute(
            "SELECT final, source FROM price_snapshots WHERE appid = ? ORDER BY final",
            (appid,),
        ).fetchall()

    assert kept == [(100, "steam"), (200, "steam"), (300, "steam"), (500, "steam"), (800, "itad")]


def test_cleanup_removes_only_expired_terminal_records(isolated_runtime, monkeypatch, insert_game):
    runtime = isolated_runtime
    appid = insert_game(103, "Cleanup")
    monkeypatch.setattr(runtime, "datetime", FrozenDateTime)
    old_task = stamp(days=runtime.CRAWL_TASK_RETENTION_DAYS + 1)
    old_recommendation = (FIXED_NOW - timedelta(days=runtime.RECOMMENDATION_RETENTION_DAYS + 1)).strftime("%Y-%m-%d")
    recent_recommendation = FIXED_NOW.strftime("%Y-%m-%d")
    with sqlite3.connect(runtime.DB_PATH) as conn:
        conn.executemany(
            """
            INSERT INTO crawl_tasks(
                appid, task_type, priority, status, next_attempt_at,
                completed_at, updated_at
            ) VALUES (?, ?, 1, ?, ?, ?, ?)
            """,
            [
                (appid, "old_done", "done", old_task, old_task, old_task),
                (appid, "old_pending", "pending", old_task, None, old_task),
                (appid, "recent_done", "done", stamp(days=1), stamp(days=1), stamp(days=1)),
            ],
        )
        conn.executemany(
            """
            INSERT INTO niche_recommendation_snapshots(
                recommendation_date, appid, name, created_at
            ) VALUES (?, ?, ?, ?)
            """,
            [
                (old_recommendation, appid, "Old", old_task),
                (recent_recommendation, appid, "Recent", stamp()),
            ],
        )

    assert runtime.cleanup_old_records_once() is True
    assert runtime.cleanup_old_records_once() is False
    with sqlite3.connect(runtime.DB_PATH) as conn:
        tasks = {row[0] for row in conn.execute("SELECT task_type FROM crawl_tasks")}
        recommendations = {
            row[0] for row in conn.execute("SELECT recommendation_date FROM niche_recommendation_snapshots")
        }

    assert tasks == {"old_pending", "recent_done"}
    assert recommendations == {recent_recommendation}
