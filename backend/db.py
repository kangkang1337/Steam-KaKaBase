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


def _lease_times(lease_seconds, moment=None):
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc).replace(microsecond=0)
    expires = current + timedelta(seconds=max(1, int(lease_seconds)))
    return current.isoformat(), expires.isoformat()


def acquire_process_lease(name, owner_id, *, pid=None, hostname=None, lease_seconds=120, moment=None):
    """Atomically acquire an expired process lease or renew one we already own."""
    stamp, expires_at = _lease_times(lease_seconds, moment)
    conn = connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT owner_id, expires_at, acquired_at FROM process_leases WHERE name=?",
            (str(name),),
        ).fetchone()
        if row and row[0] != str(owner_id) and row[1] > stamp:
            conn.rollback()
            return False
        acquired_at = row[2] if row and row[0] == str(owner_id) else stamp
        conn.execute(
            """
            INSERT INTO process_leases(
                name, owner_id, pid, hostname, acquired_at, heartbeat_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                owner_id=excluded.owner_id,
                pid=excluded.pid,
                hostname=excluded.hostname,
                acquired_at=excluded.acquired_at,
                heartbeat_at=excluded.heartbeat_at,
                expires_at=excluded.expires_at
            """,
            (str(name), str(owner_id), pid, hostname, acquired_at, stamp, expires_at),
        )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def renew_process_lease(name, owner_id, *, lease_seconds=120, moment=None):
    stamp, expires_at = _lease_times(lease_seconds, moment)
    with transaction() as conn:
        cursor = conn.execute(
            """
            UPDATE process_leases
            SET heartbeat_at=?, expires_at=?
            WHERE name=? AND owner_id=? AND expires_at>=?
            """,
            (stamp, expires_at, str(name), str(owner_id), stamp),
        )
        return cursor.rowcount == 1


def release_process_lease(name, owner_id):
    with transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM process_leases WHERE name=? AND owner_id=?",
            (str(name), str(owner_id)),
        )
        return cursor.rowcount == 1


def release_process_lease_by_pid(name, pid):
    """Release a lease after an external supervisor has stopped its exact PID."""
    with transaction() as conn:
        cursor = conn.execute(
            "DELETE FROM process_leases WHERE name=? AND pid=?",
            (str(name), int(pid)),
        )
        return cursor.rowcount == 1


def get_process_lease(name, *, moment=None):
    stamp, _ = _lease_times(1, moment)
    with transaction(rows=True) as conn:
        row = conn.execute(
            """
            SELECT name, owner_id, pid, hostname, acquired_at, heartbeat_at, expires_at
            FROM process_leases WHERE name=?
            """,
            (str(name),),
        ).fetchone()
    if not row:
        return None
    payload = dict(row)
    payload["active"] = payload["expires_at"] > stamp
    return payload


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
               hl.low_at AS historical_low_at,
               (SELECT MIN(observed.final)
                FROM price_snapshots observed
                WHERE observed.appid = ps.appid
                  AND observed.region = ps.region
                  AND observed.currency = ps.currency
                  AND observed.source = 'steam'
                  AND observed.final IS NOT NULL) AS observed_low_amount_int,
               (SELECT MIN(observed.fetched_at)
                FROM price_snapshots observed
                WHERE observed.appid = ps.appid
                  AND observed.region = ps.region
                  AND observed.source = 'steam') AS observed_low_since,
               (SELECT COUNT(*)
                FROM price_snapshots observed
                WHERE observed.appid = ps.appid
                  AND observed.region = ps.region
                  AND observed.source = 'steam'
                  AND observed.final IS NOT NULL) AS observed_snapshot_count
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
    historical_low_fetched_at = conn.execute(
        "SELECT MAX(fetched_at) FROM historical_lows WHERE appid = ?", (appid,)
    ).fetchone()[0]
    return {
        "game": game,
        "prices": query_latest_prices_by_region(conn, appid),
        "price_history": price_history,
        "players": players,
        "reviews": reviews,
        "site_peak": site_peak,
        "has_historical_low": bool(has_historical_low),
        "historical_low_fetched_at": historical_low_fetched_at,
    }


def query_catalog_game_stub(conn, appid):
    return conn.execute(
        "SELECT appid, name, app_type, updated_at FROM steam_catalog WHERE appid = ?",
        (int(appid),),
    ).fetchone()


def query_header_image_url(appid):
    with transaction(rows=True) as conn:
        row = conn.execute(
            """
            SELECT COALESCE(NULLIF(TRIM(g.header_image), ''), NULLIF(TRIM(h.header_image), '')) AS header_image
            FROM games g
            LEFT JOIN hot_games h ON h.appid = g.appid
            WHERE g.appid = ?
            UNION ALL
            SELECT NULLIF(TRIM(h.header_image), '') AS header_image
            FROM hot_games h
            WHERE h.appid = ? AND NOT EXISTS (SELECT 1 FROM games g WHERE g.appid = h.appid)
            LIMIT 1
            """,
            (int(appid), int(appid)),
        ).fetchone()
    return row["header_image"] if row else None


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
                   (SELECT amount_cny FROM historical_lows WHERE appid = g.appid AND country = 'CN' LIMIT 1) AS cn_itad_low_cny,
                   (SELECT MIN(final) / 100.0 FROM price_snapshots WHERE appid = g.appid AND region = 'CN' AND source = 'steam' AND final IS NOT NULL) AS cn_observed_low_cny,
                   (SELECT MIN(fetched_at) FROM price_snapshots WHERE appid = g.appid AND region = 'CN' AND source = 'steam') AS cn_observed_low_since,
                   (SELECT COUNT(*) FROM price_snapshots WHERE appid = g.appid AND region = 'CN' AND source = 'steam' AND final IS NOT NULL) AS cn_observed_snapshot_count
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
                       gls.historical_low_cny AS cn_itad_low_cny,
                       (SELECT MIN(ps.final) / 100.0 FROM price_snapshots ps WHERE ps.appid=h.appid AND ps.region='CN' AND ps.source='steam' AND ps.final IS NOT NULL) AS cn_observed_low_cny,
                       (SELECT MIN(ps.fetched_at) FROM price_snapshots ps WHERE ps.appid=h.appid AND ps.region='CN' AND ps.source='steam') AS cn_observed_low_since,
                       (SELECT COUNT(*) FROM price_snapshots ps WHERE ps.appid=h.appid AND ps.region='CN' AND ps.source='steam' AND ps.final IS NOT NULL) AS cn_observed_snapshot_count
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
                   gls.historical_low_cny AS cn_itad_low_cny,
                   (SELECT MIN(ps.final) / 100.0 FROM price_snapshots ps WHERE ps.appid=g.appid AND ps.region='CN' AND ps.source='steam' AND ps.final IS NOT NULL) AS cn_observed_low_cny,
                   (SELECT MIN(ps.fetched_at) FROM price_snapshots ps WHERE ps.appid=g.appid AND ps.region='CN' AND ps.source='steam') AS cn_observed_low_since,
                   (SELECT COUNT(*) FROM price_snapshots ps WHERE ps.appid=g.appid AND ps.region='CN' AND ps.source='steam' AND ps.final IS NOT NULL) AS cn_observed_snapshot_count
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
        catalog_rows = conn.execute(
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
        if term.isdigit():
            return catalog_rows

        if len(term) >= 3:
            alias_where_sql = "game_search_alias_fts MATCH ?"
            alias_params = ('"' + term.replace('"', '""') + '"',)
        else:
            alias_where_sql = "a.alias LIKE ?"
            alias_params = (f"%{term}%",)
        alias_rows = conn.execute(
            f"""
            SELECT CAST(a.appid AS INTEGER) AS appid,
                   COALESCE(NULLIF(g.name, ''), NULLIF(c.name, ''), a.display_name) AS name,
                   COALESCE(NULLIF(g.header_image, ''),
                     'https://cdn.akamai.steamstatic.com/steam/apps/' || a.appid || '/header.jpg') AS header_image,
                   COALESCE(g.tracked, 0) AS tracked,
                   gls.current_players, -1000.0 AS search_rank
            FROM game_search_alias_fts a
            LEFT JOIN games g ON g.appid=CAST(a.appid AS INTEGER)
            LEFT JOIN steam_catalog c ON c.appid=CAST(a.appid AS INTEGER)
            LEFT JOIN game_latest_state gls ON gls.appid=CAST(a.appid AS INTEGER)
            WHERE {alias_where_sql}
              AND COALESCE(c.app_type, 'game') IN ('unknown', 'game')
            ORDER BY tracked DESC, COALESCE(gls.current_players, 0) DESC, name ASC
            LIMIT ?
            """,
            (*alias_params, int(limit)),
        ).fetchall()
        merged = []
        seen = set()
        for row in [*alias_rows, *catalog_rows]:
            appid = int(row["appid"])
            if appid not in seen:
                merged.append(row)
                seen.add(appid)
            if len(merged) >= int(limit):
                break
        return merged


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
                   h.amount_cny AS cn_itad_low_cny,
                   (SELECT MIN(ps.final) / 100.0 FROM price_snapshots ps
                    WHERE ps.appid=g.appid AND ps.region='CN' AND ps.source='steam'
                      AND ps.final IS NOT NULL) AS cn_observed_low_cny,
                   (SELECT MIN(ps.fetched_at) FROM price_snapshots ps
                    WHERE ps.appid=g.appid AND ps.region='CN' AND ps.source='steam') AS cn_observed_low_since,
                   (SELECT COUNT(*) FROM price_snapshots ps
                    WHERE ps.appid=g.appid AND ps.region='CN' AND ps.source='steam' AND ps.final IS NOT NULL) AS cn_observed_snapshot_count
            FROM games g
            JOIN game_latest_state s ON s.appid = g.appid
            LEFT JOIN historical_lows h ON h.appid = g.appid AND h.country = 'CN'
            LEFT JOIN steam_catalog c ON c.appid = g.appid
            WHERE COALESCE(c.app_type, 'game') = 'game'
              AND COALESCE(g.is_free, 0) = 0
              AND g.header_image IS NOT NULL
              AND s.cn_price_final IS NOT NULL
              AND s.cn_price_currency = 'CNY'
              AND (h.amount_cny IS NOT NULL OR EXISTS (
                    SELECT 1 FROM price_snapshots ps
                    WHERE ps.appid=g.appid AND ps.region='CN' AND ps.source='steam'
                      AND ps.final IS NOT NULL))
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


def query_home_snapshot(refresh_key):
    with transaction(rows=True) as conn:
        return conn.execute(
            """
            SELECT recommendation_date, historical_low_appid, meme_url, created_at
            FROM daily_home_snapshots WHERE recommendation_date=?
            """,
            (refresh_key,),
        ).fetchone()


def query_daily_niche_snapshot(refresh_key, max_reviews):
    with transaction(rows=True) as conn:
        return conn.execute(
            """
            SELECT n.*, COALESCE(g.tracked, 0) AS tracked,
                   (SELECT amount_cny FROM historical_lows h
                    WHERE h.appid=n.appid AND h.country='CN' LIMIT 1) AS cn_itad_low_cny,
                   (SELECT MIN(ps.final) / 100.0 FROM price_snapshots ps
                    WHERE ps.appid=n.appid AND ps.region='CN' AND ps.source='steam' AND ps.final IS NOT NULL) AS cn_observed_low_cny,
                   (SELECT MIN(ps.fetched_at) FROM price_snapshots ps
                    WHERE ps.appid=n.appid AND ps.region='CN' AND ps.source='steam') AS cn_observed_low_since,
                   (SELECT COUNT(*) FROM price_snapshots ps
                    WHERE ps.appid=n.appid AND ps.region='CN' AND ps.source='steam' AND ps.final IS NOT NULL) AS cn_observed_snapshot_count
            FROM niche_recommendation_snapshots r
            JOIN niche_pool n ON n.appid=r.appid
            LEFT JOIN games g ON g.appid=n.appid
            LEFT JOIN steam_catalog c ON c.appid=n.appid
            WHERE r.recommendation_date=? AND n.eligible=1
              AND n.total_reviews BETWEEN 1 AND ?
              AND COALESCE(c.app_type, 'game')='game'
            """,
            (refresh_key, int(max_reviews)),
        ).fetchone()


def query_tracked_appids():
    with transaction() as conn:
        return [
            int(row[0])
            for row in conn.execute(
                "SELECT appid FROM games WHERE tracked=1 ORDER BY appid"
            ).fetchall()
        ]


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
                WHEN crawl_tasks.status IN ('retry', 'failed', 'permanent_failed', 'not_available') AND excluded.priority < 100 THEN crawl_tasks.status
                ELSE 'pending'
            END,
            next_attempt_at=CASE
                WHEN crawl_tasks.status IN ('retry', 'failed', 'permanent_failed', 'not_available') AND excluded.priority < 100 THEN crawl_tasks.next_attempt_at
                WHEN crawl_tasks.completed_at IS NOT NULL THEN excluded.next_attempt_at
                WHEN crawl_tasks.next_attempt_at IS NULL THEN excluded.next_attempt_at
                WHEN excluded.next_attempt_at < crawl_tasks.next_attempt_at THEN excluded.next_attempt_at
                ELSE crawl_tasks.next_attempt_at
            END,
            completed_at=CASE
                WHEN crawl_tasks.status IN ('retry', 'failed', 'permanent_failed', 'not_available') AND excluded.priority < 100 THEN crawl_tasks.completed_at
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


def recover_abandoned_crawl_tasks(reason="crawler restarted before task completion"):
    """Return tasks owned by a dead crawler to the retry queue immediately."""
    stamp = now_iso()
    with transaction() as conn:
        rows = conn.execute(
            """
            SELECT task_type, COUNT(*)
            FROM crawl_tasks
            WHERE status='running' AND completed_at IS NULL
            GROUP BY task_type
            """
        ).fetchall()
        conn.execute(
            """
            UPDATE crawl_tasks
            SET status='retry', next_attempt_at=?, locked_until=NULL,
                last_error=?, updated_at=?
            WHERE status='running' AND completed_at IS NULL
            """,
            (stamp, str(reason)[:500], stamp),
        )
    by_type = {str(row[0]): int(row[1]) for row in rows}
    return {"count": sum(by_type.values()), "by_type": by_type, "recovered_at": stamp}


def retire_obsolete_crawl_tasks():
    """Stop low-priority work belonging to an older hot-list generation."""
    stamp = now_iso()
    with transaction() as conn:
        generation = int(get_crawl_state(conn, "hotlist_generation") or 0)
        cursor = conn.execute(
            """
            UPDATE crawl_tasks
            SET status='skipped', completed_at=?, locked_until=NULL,
                last_error='obsolete hotlist generation', updated_at=?
            WHERE generation IS NOT NULL AND generation != ?
              AND priority < 100 AND completed_at IS NULL
              AND status IN ('pending', 'retry', 'running')
            """,
            (stamp, stamp, generation),
        )
        return cursor.rowcount


def query_crawl_task_monitor(moment=None):
    """Return a compact queue-health snapshot for status and monitoring APIs."""
    current = moment or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    current = current.astimezone(timezone.utc).replace(microsecond=0)
    stamp = current.isoformat()
    recent_since = (current - timedelta(hours=24)).isoformat()
    with transaction(rows=True) as conn:
        rows = conn.execute(
            """
            SELECT task_type, status, COUNT(*) AS count
            FROM crawl_tasks
            GROUP BY task_type, status
            ORDER BY task_type, status
            """
        ).fetchall()
        summary = conn.execute(
            """
            SELECT
              SUM(CASE WHEN completed_at IS NULL AND status IN ('pending','retry','running') THEN 1 ELSE 0 END),
              SUM(CASE WHEN completed_at IS NULL AND status IN ('pending','retry')
                        AND (next_attempt_at IS NULL OR next_attempt_at <= ?) THEN 1 ELSE 0 END),
              SUM(CASE WHEN status='running' THEN 1 ELSE 0 END),
              SUM(CASE WHEN status='retry' THEN 1 ELSE 0 END),
              SUM(CASE WHEN status='permanent_failed' THEN 1 ELSE 0 END),
              SUM(CASE WHEN status='running' AND (locked_until IS NULL OR locked_until <= ?) THEN 1 ELSE 0 END),
              SUM(CASE WHEN status IN ('retry','permanent_failed') AND updated_at >= ? THEN 1 ELSE 0 END),
              MIN(CASE WHEN completed_at IS NULL AND status IN ('pending','retry')
                        AND (next_attempt_at IS NULL OR next_attempt_at <= ?) THEN next_attempt_at END),
              MAX(attempts)
            FROM crawl_tasks
            """,
            (stamp, stamp, recent_since, stamp),
        ).fetchone()
    values = list(summary) if summary else [None] * 9
    return {
        "active_total": int(values[0] or 0),
        "due_total": int(values[1] or 0),
        "running_total": int(values[2] or 0),
        "retry_total": int(values[3] or 0),
        "permanent_failed_total": int(values[4] or 0),
        "stale_running_total": int(values[5] or 0),
        "recent_failures_24h": int(values[6] or 0),
        "oldest_due_at": values[7],
        "max_attempts": int(values[8] or 0),
        "by_type": [dict(row) for row in rows],
    }


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
    "acquire_process_lease", "get_process_lease", "release_process_lease",
    "release_process_lease_by_pid",
    "renew_process_lease",
    "mark_crawl_tasks_not_available", "set_crawl_state", "transaction",
    "query_crawl_task_monitor", "recover_abandoned_crawl_tasks",
    "retire_obsolete_crawl_tasks",
    "query_catalog_game_stub", "query_game_detail", "query_header_image_url", "query_hot_games", "query_latest_prices_by_region",
    "query_missing_historylow_appids", "query_search_index", "query_tracked_games",
    "query_popular_historical_low_rows", "read_home_snapshot_context",
    "query_daily_niche_snapshot", "query_home_snapshot", "query_tracked_appids",
    "upsert_home_snapshot",
    "CURRENT_SCHEMA_VERSION", "DatabaseMigrationError", "create_database_backup",
    "get_schema_version", "migrate_database", "restore_database_backup",
]
