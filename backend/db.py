"""SQLite lifecycle, connections and crawl-task persistence."""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from .config import DB_PATH, DB_TIMEOUT_SECONDS
from .migrations import (
    CURRENT_SCHEMA_VERSION,
    DatabaseMigrationError,
    create_database_backup,
    get_schema_version,
    migrate_database,
    restore_database_backup,
)
from ._runtime import (
    cleanup_old_records_once,
    compact_player_snapshots_once,
    compact_price_snapshots_once,
    ensure_schema,
    init_db,
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


def now_iso():
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def query_latest_prices_by_region(conn, appid):
    """Return the latest cached price row for each region."""
    return conn.execute(
        """
        SELECT ps.region,
               ps.currency,
               ps.initial,
               ps.final,
               ps.discount_percent,
               ps.final_formatted,
               ps.source,
               ps.fetched_at,
               hl.amount_cny AS historical_low_cny,
               hl.currency AS historical_low_currency,
               hl.amount_int AS historical_low_amount_int,
               hl.low_at AS historical_low_at
        FROM price_snapshots ps
        JOIN (
            SELECT region, MAX(fetched_at) AS fetched_at
            FROM price_snapshots
            WHERE appid = ?
            GROUP BY region
        ) latest ON latest.region = ps.region AND latest.fetched_at = ps.fetched_at
        LEFT JOIN historical_lows hl ON hl.appid = ps.appid
            AND hl.country = CASE WHEN ps.region = 'CN' THEN 'CN' ELSE 'US' END
        WHERE ps.appid = ?
        ORDER BY ps.region
        """,
        (int(appid), int(appid)),
    ).fetchall()


def query_game_detail(conn, appid, history_limit):
    """Read a game's cached detail data without scheduling external work."""
    appid = int(appid)
    game = conn.execute("SELECT * FROM games WHERE appid = ?", (appid,)).fetchone()
    if not game:
        return None
    price_history = conn.execute(
        """
        SELECT region, currency, initial, final, discount_percent, final_formatted, source, fetched_at
        FROM (
            SELECT region, currency, initial, final, discount_percent, final_formatted, source, fetched_at
            FROM price_snapshots
            WHERE appid = ? AND region IN ('US', 'CN', 'ITAD-US')
            ORDER BY fetched_at DESC
            LIMIT ?
        )
        ORDER BY fetched_at ASC
        """,
        (appid, int(history_limit)),
    ).fetchall()
    players = conn.execute(
        """
        SELECT player_count, fetched_at
        FROM (
            SELECT player_count, fetched_at
            FROM player_snapshots
            WHERE appid = ?
            ORDER BY fetched_at DESC
            LIMIT ?
        )
        ORDER BY fetched_at ASC
        """,
        (appid, int(history_limit)),
    ).fetchall()
    reviews = conn.execute(
        """
        SELECT review_score, review_score_desc, total_positive, total_negative, total_reviews, fetched_at
        FROM review_snapshots
        WHERE appid = ?
        ORDER BY fetched_at DESC
        LIMIT 1
        """,
        (appid,),
    ).fetchone()
    site_peak = conn.execute(
        """
        SELECT MAX(player_count) AS peak_players, MIN(fetched_at) AS recorded_since
        FROM player_snapshots
        WHERE appid = ?
        """,
        (appid,),
    ).fetchone()
    has_historical_low = conn.execute(
        "SELECT 1 FROM historical_lows WHERE appid = ? LIMIT 1", (appid,)
    ).fetchone()
    return {
        "game": game,
        "prices": query_latest_prices_by_region(conn, appid),
        "price_history": price_history,
        "players": players,
        "reviews": reviews,
        "site_peak": site_peak,
        "has_historical_low": bool(has_historical_low),
    }


def query_tracked_games():
    with transaction(rows=True) as conn:
        return conn.execute(
            """
            SELECT g.*,
                   (SELECT player_count FROM player_snapshots WHERE appid = g.appid ORDER BY fetched_at DESC LIMIT 1) AS player_count,
                   (SELECT review_score FROM review_snapshots WHERE appid = g.appid ORDER BY fetched_at DESC LIMIT 1) AS review_score,
                   (SELECT final_formatted FROM price_snapshots WHERE appid = g.appid AND region = 'CN' ORDER BY fetched_at DESC LIMIT 1) AS cn_price,
                   (SELECT final FROM price_snapshots WHERE appid = g.appid AND region = 'CN' ORDER BY fetched_at DESC LIMIT 1) AS cn_price_final,
                   (SELECT currency FROM price_snapshots WHERE appid = g.appid AND region = 'CN' ORDER BY fetched_at DESC LIMIT 1) AS cn_price_currency,
                   (SELECT discount_percent FROM price_snapshots WHERE appid = g.appid AND region = 'CN' ORDER BY fetched_at DESC LIMIT 1) AS cn_discount_percent,
                   (SELECT amount_cny FROM historical_lows WHERE appid = g.appid AND country = 'CN' LIMIT 1) AS cn_historical_low_cny
            FROM games g
            WHERE tracked = 1
            ORDER BY player_count DESC, name ASC
            """
        ).fetchall()


def query_hot_games(limit, unknown_name):
    with transaction(rows=True) as conn:
        rows = conn.execute(
            """
            WITH hot AS (
                SELECT h.appid, h.rank AS original_rank,
                       CASE
                           WHEN g.name IS NOT NULL AND TRIM(g.name) != ''
                                AND g.name != ? AND g.name NOT LIKE 'App %'
                                AND g.name NOT LIKE 'Steam App %' THEN g.name
                           WHEN h.name IS NOT NULL AND TRIM(h.name) != ''
                                AND h.name != ? AND h.name NOT LIKE 'App %'
                                AND h.name NOT LIKE 'Steam App %' THEN h.name
                           WHEN san.name IS NOT NULL AND TRIM(san.name) != '' THEN san.name
                           ELSE ?
                       END AS name,
                       COALESCE(NULLIF(TRIM(g.header_image), ''), NULLIF(TRIM(h.header_image), '')) AS header_image,
                       COALESCE(gls.current_players, h.current_players, 0) AS current_players,
                       h.peak_players, h.source, h.fetched_at, g.tracked, g.is_free,
                       gls.review_score, gls.cn_price, gls.cn_price_final,
                       gls.cn_price_currency, gls.cn_discount_percent,
                       gls.historical_low_cny AS cn_historical_low_cny
                FROM hot_games h
                LEFT JOIN games g ON g.appid = h.appid
                LEFT JOIN steam_app_names san ON san.appid = h.appid
                LEFT JOIN game_latest_state gls ON gls.appid = h.appid
            )
            SELECT * FROM hot
            WHERE name != ? AND header_image IS NOT NULL
            ORDER BY current_players DESC, COALESCE(original_rank, 999999)
            LIMIT ?
            """,
            (unknown_name, unknown_name, unknown_name, unknown_name, int(limit)),
        ).fetchall()
        if rows:
            return rows
        return conn.execute(
            """
            SELECT g.appid, NULL AS original_rank, g.name, g.header_image,
                   COALESCE(gls.current_players, 0) AS current_players,
                   NULL AS peak_players, 'local_snapshots' AS source,
                   g.updated_at AS fetched_at, g.tracked, g.is_free,
                   gls.review_score, gls.cn_price, gls.cn_price_final,
                   gls.cn_price_currency, gls.cn_discount_percent,
                   gls.historical_low_cny AS cn_historical_low_cny
            FROM games g
            LEFT JOIN game_latest_state gls ON gls.appid = g.appid
            WHERE g.name != ?
            ORDER BY current_players DESC, g.name ASC
            LIMIT ?
            """,
            (unknown_name, int(limit)),
        ).fetchall()


def query_search_index(term, limit, offset, unknown_name):
    """Query the local FTS index; callers own text normalization and response shaping."""
    with transaction(rows=True) as conn:
        if term.isdigit():
            where_sql = "f.rowid = ?"
            where_params = (int(term),)
            rank_sql = "0"
        elif len(term) >= 3:
            phrase = '"' + term.replace('"', '""') + '"'
            where_sql = "game_search_fts MATCH ?"
            where_params = (phrase,)
            rank_sql = "bm25(game_search_fts, 0.0, 8.0, 6.0, 1.0)"
        else:
            pattern = f"%{term}%"
            where_sql = "(f.name LIKE ? OR f.catalog_name LIKE ? OR f.description LIKE ?)"
            where_params = (pattern, pattern, pattern)
            rank_sql = "0"
        return conn.execute(
            f"""
            SELECT CAST(f.appid AS INTEGER) AS appid,
                   CASE
                     WHEN g.name IS NOT NULL AND g.name != ? THEN g.name
                     WHEN f.name != '' THEN f.name
                     ELSE f.catalog_name
                   END AS name,
                   COALESCE(NULLIF(g.header_image, ''),
                     'https://cdn.akamai.steamstatic.com/steam/apps/' || f.appid || '/header.jpg') AS header_image,
                   COALESCE(g.tracked, 0) AS tracked,
                   gls.current_players, {rank_sql} AS search_rank
            FROM game_search_fts f
            LEFT JOIN games g ON g.appid=CAST(f.appid AS INTEGER)
            LEFT JOIN steam_catalog c ON c.appid=CAST(f.appid AS INTEGER)
            LEFT JOIN game_latest_state gls ON gls.appid=CAST(f.appid AS INTEGER)
            WHERE {where_sql}
              AND COALESCE(c.app_type, 'game') IN ('unknown', 'game')
            ORDER BY tracked DESC, search_rank ASC,
                     COALESCE(gls.current_players, 0) DESC, name ASC
            LIMIT ? OFFSET ?
            """,
            (unknown_name, *where_params, int(limit), int(offset)),
        ).fetchall()


def query_missing_historylow_appids(limit, missing_game_id):
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT g.appid
            FROM games g
            JOIN price_snapshots ps ON ps.appid = g.appid
            LEFT JOIN historical_lows us_low ON us_low.appid = g.appid AND us_low.country = 'US'
            LEFT JOIN historical_lows cn_low ON cn_low.appid = g.appid AND cn_low.country = 'CN'
            WHERE ps.source = 'steam'
              AND (us_low.appid IS NULL OR cn_low.appid IS NULL)
              AND COALESCE(g.itad_game_id, '') != ?
            ORDER BY g.tracked DESC, g.updated_at DESC
            LIMIT ?
            """,
            (missing_game_id, int(limit)),
        ).fetchall()
    return [int(row[0]) for row in rows]


def query_popular_historical_low_rows(limit, min_reviews, min_players):
    with transaction(rows=True) as conn:
        return conn.execute(
            """
            SELECT g.appid, g.name, g.header_image,
                   COALESCE(s.current_players, 0) AS current_players,
                   s.review_score, s.total_reviews, s.cn_price,
                   s.cn_price_final, s.cn_price_currency,
                   COALESCE(s.cn_discount_percent, 0) AS cn_discount_percent,
                   g.is_free, COALESCE(g.tracked, 0) AS tracked,
                   h.amount_cny AS cn_historical_low_cny
            FROM games g
            JOIN game_latest_state s ON s.appid = g.appid
            JOIN historical_lows h ON h.appid = g.appid AND h.country = 'CN'
            LEFT JOIN steam_catalog c ON c.appid = g.appid
            WHERE COALESCE(c.app_type, 'game') = 'game'
              AND COALESCE(g.is_free, 0) = 0
              AND g.header_image IS NOT NULL
              AND s.cn_price_final IS NOT NULL
              AND s.cn_price_currency = 'CNY'
              AND h.amount_cny IS NOT NULL
              AND (COALESCE(s.total_reviews, 0) >= ? OR COALESCE(s.current_players, 0) >= ?)
            ORDER BY COALESCE(s.current_players, 0) DESC, COALESCE(s.total_reviews, 0) DESC
            LIMIT ?
            """,
            (int(min_reviews), int(min_players), int(limit)),
        ).fetchall()


def read_home_snapshot_context(refresh_key, repeat_days):
    with transaction(rows=True) as conn:
        recent = {
            int(row[0])
            for row in conn.execute(
                """
                SELECT historical_low_appid FROM daily_home_snapshots
                WHERE recommendation_date < ? AND historical_low_appid IS NOT NULL
                ORDER BY recommendation_date DESC LIMIT ?
                """,
                (refresh_key, int(repeat_days)),
            ).fetchall()
        }
        current = conn.execute(
            "SELECT historical_low_appid, meme_url FROM daily_home_snapshots WHERE recommendation_date = ?",
            (refresh_key,),
        ).fetchone()
    return recent, current


def upsert_home_snapshot(refresh_key, historical_low_appid, meme_url):
    with transaction() as conn:
        conn.execute(
            """
            INSERT INTO daily_home_snapshots(recommendation_date, historical_low_appid, meme_url, created_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(recommendation_date) DO UPDATE SET
                historical_low_appid=excluded.historical_low_appid,
                meme_url=excluded.meme_url
            """,
            (refresh_key, historical_low_appid, meme_url, now_iso()),
        )


def get_crawl_state(conn, key):
    row = conn.execute("SELECT value FROM crawl_state WHERE key = ?", (key,)).fetchone()
    return row[0] if row else None


def set_crawl_state(conn, key, value):
    conn.execute(
        "INSERT INTO crawl_state(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def enqueue_crawl_tasks_in_conn(conn, appids, task_type, priority, next_attempt_at=None, generation=None):
    stamp = now_iso()
    rows = [
        (int(appid), task_type, int(priority), next_attempt_at or stamp, stamp, generation)
        for appid in appids
    ]
    if not rows:
        return 0
    conn.executemany(
        """
        INSERT INTO crawl_tasks(appid, task_type, priority, next_attempt_at, updated_at, generation)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(appid, task_type) DO UPDATE SET
            priority=MAX(crawl_tasks.priority, excluded.priority),
            status=CASE
                WHEN crawl_tasks.status IN ('failed', 'permanent_failed', 'not_available') AND excluded.priority < 100 THEN crawl_tasks.status
                ELSE 'pending'
            END,
            next_attempt_at=CASE
                WHEN crawl_tasks.status IN ('failed', 'permanent_failed', 'not_available') AND excluded.priority < 100 THEN crawl_tasks.next_attempt_at
                WHEN crawl_tasks.completed_at IS NOT NULL THEN excluded.next_attempt_at
                WHEN crawl_tasks.next_attempt_at IS NULL THEN excluded.next_attempt_at
                WHEN excluded.next_attempt_at < crawl_tasks.next_attempt_at THEN excluded.next_attempt_at
                ELSE crawl_tasks.next_attempt_at
            END,
            completed_at=CASE
                WHEN crawl_tasks.status IN ('failed', 'permanent_failed', 'not_available') AND excluded.priority < 100 THEN crawl_tasks.completed_at
                ELSE NULL
            END,
            updated_at=excluded.updated_at,
            generation=COALESCE(excluded.generation, crawl_tasks.generation)
        """,
        rows,
    )
    return len(rows)


def enqueue_crawl_tasks(appids, task_type, priority, next_attempt_at=None, generation=None):
    with transaction() as conn:
        return enqueue_crawl_tasks_in_conn(
            conn, appids, task_type, priority, next_attempt_at, generation
        )


def enqueue_crawl_task_once_in_conn(conn, appid, task_type, priority, next_attempt_at=None):
    """Create missing detail work without reviving completed or unavailable tasks."""
    stamp = now_iso()
    conn.execute(
        """
        INSERT INTO crawl_tasks(appid, task_type, priority, next_attempt_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(appid, task_type) DO UPDATE SET
            priority=MAX(crawl_tasks.priority, excluded.priority),
            updated_at=excluded.updated_at
        """,
        (int(appid), task_type, int(priority), next_attempt_at or stamp, stamp),
    )


def claim_crawl_tasks(task_type, limit, lock_minutes=15):
    stamp = now_iso()
    locked_until = (
        datetime.now(timezone.utc) + timedelta(minutes=lock_minutes)
    ).replace(microsecond=0).isoformat()
    with transaction() as conn:
        current_generation = int(get_crawl_state(conn, "hotlist_generation") or 0)
        rows = conn.execute(
            """
            SELECT appid FROM crawl_tasks
            WHERE task_type=? AND completed_at IS NULL
              AND status IN ('pending', 'retry')
              AND (next_attempt_at IS NULL OR next_attempt_at <= ?)
              AND (locked_until IS NULL OR locked_until <= ?)
              AND (generation IS NULL OR generation = ?)
            ORDER BY priority DESC, attempts ASC, next_attempt_at ASC
            LIMIT ?
            """,
            (task_type, stamp, stamp, current_generation, limit),
        ).fetchall()
        appids = [int(row[0]) for row in rows]
        if appids:
            placeholders = ",".join("?" for _ in appids)
            conn.execute(
                f"""
                UPDATE crawl_tasks
                SET locked_until=?, status='running',
                    attempt_count=attempt_count+1, attempts=attempts+1, updated_at=?
                WHERE task_type=? AND appid IN ({placeholders})
                """,
                (locked_until, stamp, task_type, *appids),
            )
    return appids


def complete_crawl_tasks(appids, task_type):
    if not appids:
        return
    stamp = now_iso()
    placeholders = ",".join("?" for _ in appids)
    with transaction() as conn:
        conn.execute(
            f"""
            UPDATE crawl_tasks
            SET status='done', completed_at=?, locked_until=NULL,
                last_error=NULL, updated_at=?
            WHERE task_type=? AND appid IN ({placeholders})
            """,
            (stamp, stamp, task_type, *[int(appid) for appid in appids]),
        )


def mark_crawl_tasks_not_available(appids, task_type, reason):
    if not appids:
        return
    stamp = now_iso()
    placeholders = ",".join("?" for _ in appids)
    with transaction() as conn:
        conn.execute(
            f"""
            UPDATE crawl_tasks
            SET status='not_available', completed_at=?, locked_until=NULL,
                last_error=?, updated_at=?
            WHERE task_type=? AND appid IN ({placeholders})
            """,
            (stamp, str(reason)[:500], stamp, task_type, *[int(appid) for appid in appids]),
        )


def fail_crawl_tasks(appids, task_type, error, retry_minutes=60, terminal=False):
    if not appids:
        return
    stamp = now_iso()
    next_attempt = (
        datetime.now(timezone.utc) + timedelta(minutes=retry_minutes)
    ).replace(microsecond=0).isoformat()
    placeholders = ",".join("?" for _ in appids)
    with transaction() as conn:
        conn.execute(
            f"""
            UPDATE crawl_tasks
            SET status=?, next_attempt_at=?, locked_until=NULL,
                last_error=?, updated_at=?
            WHERE task_type=? AND appid IN ({placeholders})
            """,
            (
                "permanent_failed" if terminal else "retry",
                next_attempt,
                str(error)[:500],
                stamp,
                task_type,
                *[int(appid) for appid in appids],
            ),
        )


__all__ = [
    "claim_crawl_tasks", "cleanup_old_records_once", "compact_player_snapshots_once",
    "compact_price_snapshots_once", "complete_crawl_tasks", "connect",
    "enqueue_crawl_task_once_in_conn", "enqueue_crawl_tasks",
    "enqueue_crawl_tasks_in_conn", "ensure_schema",
    "fail_crawl_tasks", "get_crawl_state", "init_db",
    "mark_crawl_tasks_not_available", "set_crawl_state", "transaction",
    "query_game_detail", "query_hot_games", "query_latest_prices_by_region",
    "query_missing_historylow_appids", "query_search_index", "query_tracked_games",
    "query_popular_historical_low_rows", "read_home_snapshot_context",
    "upsert_home_snapshot",
    "CURRENT_SCHEMA_VERSION", "DatabaseMigrationError", "create_database_backup",
    "get_schema_version", "migrate_database", "restore_database_backup",
]
