"""SQLite reads and writes used by crawler task orchestration."""

from . import config
from .db import enqueue_crawl_tasks, get_crawl_state, is_due, transaction
from .utils import UNKNOWN_GAME_NAME, clean_hot_name, fallback_game_name


def upsert_hot_games_batch(rows, stamp):
    if not rows:
        return
    with transaction() as conn:
        conn.executemany(
            """INSERT INTO hot_games(appid,rank,name,current_players,peak_players,header_image,source,fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(appid) DO UPDATE SET
            rank=excluded.rank,name=COALESCE(excluded.name,hot_games.name),current_players=COALESCE(excluded.current_players,hot_games.current_players),
            peak_players=COALESCE(excluded.peak_players,hot_games.peak_players),header_image=COALESCE(excluded.header_image,hot_games.header_image),
            source=excluded.source,fetched_at=excluded.fetched_at""",
            [(row["appid"], row.get("rank"), clean_hot_name(row.get("name")), row.get("current_players"), row.get("peak_players"), row.get("header_image"), row.get("source") or "steam_charts", stamp) for row in rows],
        )
        conn.executemany(
            """INSERT INTO games(appid,name,header_image,tracked,updated_at) VALUES (?, ?, ?, 0, ?)
            ON CONFLICT(appid) DO UPDATE SET name=CASE WHEN excluded.name != ? THEN excluded.name ELSE games.name END,
            header_image=COALESCE(excluded.header_image,games.header_image),updated_at=excluded.updated_at""",
            [(row["appid"], fallback_game_name(row["appid"], row.get("name")), row.get("header_image"), stamp, UNKNOWN_GAME_NAME) for row in rows],
        )


def insert_player_batch(rows):
    if not rows:
        return
    with transaction() as conn:
        conn.executemany("INSERT INTO player_snapshots(appid,player_count,fetched_at) VALUES (?, ?, ?)", rows)
        conn.executemany("UPDATE hot_games SET current_players=?,fetched_at=? WHERE appid=?", [(players, stamp, appid) for appid, players, stamp in rows])


def upsert_hot_price_batch(rows, stamp):
    if not rows:
        return
    with transaction() as conn:
        conn.executemany("UPDATE games SET name=COALESCE(?,name),header_image=COALESCE(?,header_image),is_free=COALESCE(?,is_free),updated_at=? WHERE appid=?", [(row.get("name"), row.get("header_image"), row.get("is_free"), stamp, row["appid"]) for row in rows])
        conn.executemany("INSERT INTO price_snapshots(appid,region,currency,initial,final,discount_percent,final_formatted,source,fetched_at) VALUES (?, 'CN', ?, ?, ?, ?, ?, 'steam', ?)", [(row["appid"], row.get("currency"), row.get("initial"), row.get("final"), row.get("discount_percent"), row.get("final_formatted"), stamp) for row in rows if row.get("has_price")])
        conn.executemany(
            """INSERT INTO game_latest_state(appid,cn_price,cn_price_final,cn_price_currency,cn_discount_percent,price_updated_at,updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(appid) DO UPDATE SET cn_price=excluded.cn_price,cn_price_final=excluded.cn_price_final,
            cn_price_currency=excluded.cn_price_currency,cn_discount_percent=excluded.cn_discount_percent,price_updated_at=excluded.price_updated_at,updated_at=excluded.updated_at""",
            [(row["appid"], row.get("final_formatted") if row.get("has_price") else None, row.get("final") if row.get("has_price") else None, row.get("currency") if row.get("has_price") else None, row.get("discount_percent", 0) if row.get("has_price") else 0, stamp, stamp) for row in rows],
        )
        conn.executemany("UPDATE niche_pool SET cn_price=?,cn_price_final=?,cn_price_currency=?,cn_discount_percent=?,is_free=? WHERE appid=?", [(row.get("final_formatted") if row.get("has_price") else None, row.get("final") if row.get("has_price") else None, row.get("currency") if row.get("has_price") else None, row.get("discount_percent", 0) if row.get("has_price") else 0, row.get("is_free", 0), row["appid"]) for row in rows])


def upsert_release_date_batch(rows, stamp):
    if not rows:
        return
    with transaction() as conn:
        conn.executemany("UPDATE games SET name=COALESCE(?,name),header_image=COALESCE(?,header_image),release_date=COALESCE(release_date,?),is_free=COALESCE(?,is_free),updated_at=? WHERE appid=?", [(row.get("name"), row.get("header_image"), row.get("release_date"), row.get("is_free"), stamp, row["appid"]) for row in rows])


def upsert_review_batch(rows, stamp):
    if not rows:
        return
    with transaction() as conn:
        conn.executemany("INSERT INTO review_snapshots(appid,review_score,review_score_desc,total_positive,total_negative,total_reviews,fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)", [(row["appid"], row.get("review_score"), row.get("review_score_desc"), row.get("total_positive"), row.get("total_negative"), row.get("total_reviews"), stamp) for row in rows if row.get("has_reviews")])
        conn.executemany("INSERT INTO game_latest_state(appid,metadata_updated_at,updated_at) VALUES (?, ?, ?) ON CONFLICT(appid) DO UPDATE SET metadata_updated_at=excluded.metadata_updated_at,updated_at=excluded.updated_at", [(row["appid"], stamp, stamp) for row in rows])
        conn.executemany("INSERT INTO game_latest_state(appid,review_score,total_reviews,review_updated_at,updated_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(appid) DO UPDATE SET review_score=excluded.review_score,total_reviews=excluded.total_reviews,review_updated_at=excluded.review_updated_at,updated_at=excluded.updated_at", [(row["appid"], row.get("review_score"), row.get("total_reviews"), stamp, stamp) for row in rows])


def upsert_hot_metadata_batch(rows, stamp):
    if not rows:
        return
    with transaction() as conn:
        conn.executemany(
            """INSERT INTO games(appid,name,header_image,short_description,developer,publisher,release_date,is_free,screenshots_json,tracked,updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?) ON CONFLICT(appid) DO UPDATE SET
            name=CASE WHEN excluded.name != ? THEN excluded.name ELSE games.name END,header_image=COALESCE(excluded.header_image,games.header_image),
            short_description=COALESCE(excluded.short_description,games.short_description),developer=COALESCE(excluded.developer,games.developer),publisher=COALESCE(excluded.publisher,games.publisher),
            release_date=COALESCE(excluded.release_date,games.release_date),is_free=excluded.is_free,screenshots_json=COALESCE(excluded.screenshots_json,games.screenshots_json),updated_at=excluded.updated_at""",
            [(row["appid"], fallback_game_name(row["appid"], row.get("name")), row.get("header_image"), row.get("short_description"), row.get("developer"), row.get("publisher"), row.get("release_date"), row.get("is_free"), row.get("screenshots_json"), stamp, UNKNOWN_GAME_NAME) for row in rows],
        )
        conn.executemany("INSERT INTO price_snapshots(appid,region,currency,initial,final,discount_percent,final_formatted,source,fetched_at) VALUES (?, 'CN', ?, ?, ?, ?, ?, 'steam', ?)", [(row["appid"], row.get("currency"), row.get("initial"), row.get("final"), row.get("discount_percent"), row.get("final_formatted"), stamp) for row in rows if row.get("has_price")])
        conn.executemany("INSERT INTO review_snapshots(appid,review_score,review_score_desc,total_positive,total_negative,total_reviews,fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)", [(row["appid"], row.get("review_score"), row.get("review_score_desc"), row.get("total_positive"), row.get("total_negative"), row.get("total_reviews"), stamp) for row in rows if row.get("has_reviews")])


def get_hot_appids(limit=config.HOTLIST_TARGET):
    with transaction() as conn:
        rows = conn.execute("SELECT appid FROM hot_games ORDER BY COALESCE(current_players,0) DESC,COALESCE(rank,999999) LIMIT ?", (limit,)).fetchall()
    return [int(row[0]) for row in rows]


def get_due_hot_player_appids():
    with transaction() as conn:
        rows = conn.execute("SELECT h.appid,COALESCE(h.rank,999999),(SELECT MAX(p.fetched_at) FROM player_snapshots p WHERE p.appid=h.appid) FROM hot_games h ORDER BY COALESCE(h.rank,999999) LIMIT ?", (config.HOTLIST_TARGET,)).fetchall()
    return [int(appid) for appid, rank, fetched_at in rows if is_due(fetched_at, 15 if rank <= 10 else 30 if rank <= 50 else 60 if rank <= 100 else 240)]


def _due_appids(column, limit, interval_minutes):
    with transaction() as conn:
        rows = conn.execute(f"SELECT h.appid,COALESCE(p.fetched_at,s.{column}) FROM hot_games h LEFT JOIN game_latest_state s ON s.appid=h.appid LEFT JOIN (SELECT appid,MAX(fetched_at) AS fetched_at FROM {'review_snapshots' if column == 'review_updated_at' else 'price_snapshots'} {'WHERE region=\'CN\' AND source=\'steam\'' if column == 'price_updated_at' else ''} GROUP BY appid) p ON p.appid=h.appid WHERE COALESCE(h.rank,999999)<=? ORDER BY COALESCE(h.rank,999999),h.current_players DESC", (config.HOT_PREVIEW_TOP_LIMIT,)).fetchall()
    return [int(appid) for appid, fetched_at in rows if is_due(fetched_at, interval_minutes)][:limit]


def get_hot_price_due_appids(limit=config.HOT_PREVIEW_BATCH_LIMIT):
    return _due_appids("price_updated_at", limit, config.PRICE_REFRESH_HOURS * 60)


def get_hot_review_due_appids(limit=config.HOT_PREVIEW_BATCH_LIMIT):
    return _due_appids("review_updated_at", limit, config.PRICE_REFRESH_HOURS * 60)


def get_hot_static_due_appids(limit=config.HOT_PREVIEW_BATCH_LIMIT):
    with transaction() as conn:
        rows = conn.execute("SELECT h.appid FROM hot_games h LEFT JOIN games g ON g.appid=h.appid WHERE COALESCE(h.rank,999999)<=? AND g.release_date IS NULL ORDER BY COALESCE(h.rank,999999),h.current_players DESC LIMIT ?", (config.HOT_PREVIEW_TOP_LIMIT, limit)).fetchall()
    return [int(row[0]) for row in rows]


def get_hot_preview_due_appids(limit=config.HOT_PREVIEW_BATCH_LIMIT):
    selected = []
    for appid in get_hot_price_due_appids(limit) + get_hot_static_due_appids(limit):
        if appid not in selected:
            selected.append(appid)
        if len(selected) >= limit:
            break
    return selected


def get_hot_full_metadata_due_appids(limit=config.HOT_METADATA_BATCH_LIMIT):
    with transaction() as conn:
        rows = conn.execute("SELECT h.appid,s.metadata_updated_at FROM hot_games h LEFT JOIN game_latest_state s ON s.appid=h.appid WHERE COALESCE(h.rank,999999)<=? ORDER BY COALESCE(h.rank,999999),h.current_players DESC", (config.HOT_FULL_METADATA_TOP_LIMIT,)).fetchall()
    return [int(appid) for appid, fetched_at in rows if is_due(fetched_at, 7 * 24 * 60)][:limit]


def enqueue_hot_work(missing_historylow_appids):
    top_appids = get_hot_appids()
    with transaction() as conn:
        generation = int(get_crawl_state(conn, "hotlist_generation") or 0)
    enqueue_crawl_tasks(top_appids, "players", 20, generation=generation)
    enqueue_crawl_tasks(get_hot_preview_due_appids(config.HOT_PREVIEW_TOP_LIMIT), "preview", 50, generation=generation)
    enqueue_crawl_tasks(get_hot_review_due_appids(config.HOT_PREVIEW_TOP_LIMIT), "reviews", 50, generation=generation)
    enqueue_crawl_tasks(get_hot_full_metadata_due_appids(), "metadata", 80, generation=generation)
    enqueue_crawl_tasks(missing_historylow_appids(config.ITAD_HISTORYLOW_BATCH_LIMIT), "historylow", 10, generation=generation)
