"""SQLite reads and writes used by crawler task orchestration."""

from datetime import datetime, timedelta, timezone
import math

from . import config
from .db import enqueue_crawl_tasks, get_crawl_state, is_due, set_crawl_state, transaction
from .utils import UNKNOWN_GAME_NAME, clean_hot_name, fallback_game_name


def save_itad_game_ids(rows, stamp):
    rows = [(int(appid), game_id, stamp) for appid, game_id in rows if game_id]
    if not rows:
        return
    with transaction() as conn:
        conn.executemany(
            "UPDATE games SET itad_game_id=?,updated_at=? WHERE appid=?",
            [(game_id, updated_at, appid) for appid, game_id, updated_at in rows],
        )


def upsert_historical_lows(rows):
    if not rows:
        return
    with transaction() as conn:
        conn.executemany(
            """INSERT INTO historical_lows(appid,itad_game_id,country,shop_id,shop_name,currency,amount,amount_int,amount_cny,regular_amount_int,cut,low_at,fetched_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(appid,country) DO UPDATE SET
            itad_game_id=excluded.itad_game_id,shop_id=excluded.shop_id,shop_name=excluded.shop_name,currency=excluded.currency,
            amount=excluded.amount,amount_int=excluded.amount_int,amount_cny=excluded.amount_cny,regular_amount_int=excluded.regular_amount_int,
            cut=excluded.cut,low_at=excluded.low_at,fetched_at=excluded.fetched_at""",
            rows,
        )


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


def extend_hotlist_with_recent_players(rows, target):
    """Fill Steam's Top 100 ceiling from fresh local player observations.

    The extension is deliberately marked with its own source: it is useful for
    a Top 200 browsing list but is never presented as an official Steam rank.
    """
    result = [dict(row) for row in rows[:int(target)]]
    remaining = int(target) - len(result)
    if remaining <= 0:
        return result
    selected_appids = {int(row["appid"]) for row in result}
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=24)).replace(microsecond=0).isoformat()
    with transaction(rows=True) as conn:
        candidates = conn.execute(
            """
            SELECT g.appid, g.name, g.header_image, s.current_players
            FROM games g
            JOIN game_latest_state s ON s.appid=g.appid
            LEFT JOIN steam_catalog c ON c.appid=g.appid
            WHERE COALESCE(c.app_type, 'game') = 'game'
              AND COALESCE(s.current_players, 0) > 0
              AND s.players_updated_at >= ?
              AND g.name IS NOT NULL AND TRIM(g.name) != ''
              AND g.name NOT LIKE 'App %' AND g.name NOT LIKE 'Steam App %'
            ORDER BY s.current_players DESC, s.players_updated_at DESC, g.appid ASC
            LIMIT ?
            """,
            (cutoff, max(remaining * 3, remaining)),
        ).fetchall()
    for candidate in candidates:
        appid = int(candidate["appid"])
        if appid in selected_appids:
            continue
        result.append({
            "appid": appid,
            "rank": len(result) + 1,
            "name": candidate["name"],
            "current_players": candidate["current_players"],
            "peak_players": candidate["current_players"],
            "header_image": candidate["header_image"] or f"https://cdn.akamai.steamstatic.com/steam/apps/{appid}/header.jpg",
            "source": "local_player_snapshot",
        })
        selected_appids.add(appid)
        if len(result) >= int(target):
            break
    return result


def insert_player_batch(rows):
    if not rows:
        return
    with transaction() as conn:
        conn.executemany("INSERT INTO player_snapshots(appid,player_count,fetched_at) VALUES (?, ?, ?)", rows)
        conn.executemany("UPDATE hot_games SET current_players=?,fetched_at=? WHERE appid=?", [(players, stamp, appid) for appid, players, stamp in rows])


def apply_special_free_app_overrides(hot_rows, stamp):
    """Persist known free AppIDs whose normal Store/player endpoints are restricted.

    Player rows are accepted only when Steam's official popular-games response
    includes a count.  Prices are a local fact for these explicitly curated
    free titles and are refreshed at most once per day.
    """
    player_counts = {
        int(row["appid"]): int(row["current_players"])
        for row in hot_rows
        if int(row.get("appid") or 0) in config.SPECIAL_FREE_APPIDS
        and row.get("current_players") is not None
    }
    with transaction() as conn:
        for appid in config.SPECIAL_FREE_APPIDS:
            conn.execute(
                """INSERT INTO games(appid,name,tracked,is_free,updated_at) VALUES (?, ?, 0, 1, ?)
                ON CONFLICT(appid) DO UPDATE SET is_free=1,updated_at=excluded.updated_at""",
                (appid, "Deadlock" if appid == 1422450 else f"Steam App {appid}", stamp),
            )
            last_price = conn.execute(
                "SELECT MAX(fetched_at) FROM price_snapshots WHERE appid=? AND region='CN' AND source='steam'",
                (appid,),
            ).fetchone()[0]
            if is_due(last_price, 24 * 60):
                conn.execute(
                    """INSERT INTO price_snapshots(appid,region,currency,initial,final,discount_percent,final_formatted,source,fetched_at)
                    VALUES (?, 'CN', 'CNY', 0, 0, 0, 'Free', 'steam', ?)""",
                    (appid, stamp),
                )
                conn.execute(
                    """INSERT INTO game_latest_state(appid,cn_price,cn_price_final,cn_price_currency,cn_discount_percent,price_updated_at,updated_at)
                    VALUES (?, 'Free', 0, 'CNY', 0, ?, ?) ON CONFLICT(appid) DO UPDATE SET
                    cn_price=excluded.cn_price,cn_price_final=excluded.cn_price_final,cn_price_currency=excluded.cn_price_currency,
                    cn_discount_percent=excluded.cn_discount_percent,price_updated_at=excluded.price_updated_at,updated_at=excluded.updated_at""",
                    (appid, stamp, stamp),
                )
        for appid, players in player_counts.items():
            conn.execute(
                "INSERT INTO player_snapshots(appid,player_count,fetched_at) VALUES (?, ?, ?)",
                (appid, players, stamp),
            )
            conn.execute(
                """INSERT INTO game_latest_state(appid,current_players,players_updated_at,updated_at)
                VALUES (?, ?, ?, ?) ON CONFLICT(appid) DO UPDATE SET current_players=excluded.current_players,
                players_updated_at=excluded.players_updated_at,updated_at=excluded.updated_at""",
                (appid, players, stamp, stamp),
            )
            conn.execute(
                "UPDATE hot_games SET current_players=?,fetched_at=? WHERE appid=?",
                (players, stamp, appid),
            )
    return len(player_counts)


def upsert_hot_price_batch(rows, stamp):
    if not rows:
        return
    with transaction() as conn:
        conn.executemany("UPDATE games SET name=COALESCE(?,name),header_image=COALESCE(?,header_image),is_free=COALESCE(?,is_free),updated_at=? WHERE appid=?", [(row.get("name"), row.get("header_image"), row.get("is_free"), stamp, row["appid"]) for row in rows])
        conn.executemany("INSERT INTO price_snapshots(appid,region,currency,initial,final,discount_percent,discount_ends_at,final_formatted,source,fetched_at) VALUES (?, 'CN', ?, ?, ?, ?, ?, ?, 'steam', ?)", [(row["appid"], row.get("currency"), row.get("initial"), row.get("final"), row.get("discount_percent"), row.get("discount_ends_at"), row.get("final_formatted"), stamp) for row in rows if row.get("has_price")])
        conn.executemany(
            """INSERT INTO game_latest_state(appid,cn_price,cn_price_final,cn_price_currency,cn_discount_percent,cn_discount_ends_at,price_updated_at,updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(appid) DO UPDATE SET cn_price=excluded.cn_price,cn_price_final=excluded.cn_price_final,
            cn_price_currency=excluded.cn_price_currency,cn_discount_percent=excluded.cn_discount_percent,cn_discount_ends_at=excluded.cn_discount_ends_at,price_updated_at=excluded.price_updated_at,updated_at=excluded.updated_at""",
            [(row["appid"], row.get("final_formatted") if row.get("has_price") else None, row.get("final") if row.get("has_price") else None, row.get("currency") if row.get("has_price") else None, row.get("discount_percent", 0) if row.get("has_price") else 0, row.get("discount_ends_at") if row.get("has_price") else None, stamp, stamp) for row in rows],
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
        conn.executemany("INSERT INTO price_snapshots(appid,region,currency,initial,final,discount_percent,discount_ends_at,final_formatted,source,fetched_at) VALUES (?, 'CN', ?, ?, ?, ?, ?, ?, 'steam', ?)", [(row["appid"], row.get("currency"), row.get("initial"), row.get("final"), row.get("discount_percent"), row.get("discount_ends_at"), row.get("final_formatted"), stamp) for row in rows if row.get("has_price")])
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


def record_game_interest(conn, appid, *, favorite=False):
    """Store only a per-game recency signal, never a visitor identity."""
    column = "last_favorited_at" if favorite else "last_interested_at"
    conn.execute(
        f"""INSERT INTO game_activity(appid, {column}) VALUES (?, ?)
        ON CONFLICT(appid) DO UPDATE SET {column}=excluded.{column}""",
        (int(appid), datetime.now(timezone.utc).replace(microsecond=0).isoformat()),
    )


def _latest_player_rows(conn, appids):
    if not appids:
        return {}
    placeholders = ",".join("?" for _ in appids)
    rows = conn.execute(
        f"""SELECT p.appid,p.player_count,p.fetched_at FROM player_snapshots p
        JOIN (SELECT appid,MAX(fetched_at) AS fetched_at FROM player_snapshots
              WHERE appid IN ({placeholders}) GROUP BY appid) latest
          ON latest.appid=p.appid AND latest.fetched_at=p.fetched_at""",
        [int(appid) for appid in appids],
    ).fetchall()
    return {int(appid): (int(players or 0), fetched_at) for appid, players, fetched_at in rows}


def _latest_price_rows(conn, appids):
    if not appids:
        return {}
    placeholders = ",".join("?" for _ in appids)
    rows = conn.execute(
        f"""SELECT appid,MAX(fetched_at) FROM price_snapshots
        WHERE region='CN' AND source='steam' AND appid IN ({placeholders}) GROUP BY appid""",
        [int(appid) for appid in appids],
    ).fetchall()
    return {int(appid): fetched_at for appid, fetched_at in rows}


def _coverage_candidates(conn, *, include_hot=True):
    """Return candidate appids with priority sources before due-time filtering."""
    active_since = (
        datetime.now(timezone.utc) - timedelta(days=config.COVERAGE_ACTIVITY_DAYS)
    ).replace(microsecond=0).isoformat()
    result = {}

    def add(appids, priority, source):
        for appid in appids:
            appid = int(appid)
            previous = result.get(appid)
            if previous is None or priority > previous[0]:
                result[appid] = (priority, source)

    if include_hot:
        add(
            [row[0] for row in conn.execute(
                "SELECT appid FROM hot_games ORDER BY COALESCE(rank,999999) LIMIT ?",
                (config.HOTLIST_TARGET,),
            )],
            40, "hot",
        )
    add(
        [row[0] for row in conn.execute(
            "SELECT DISTINCT appid FROM user_favorites ORDER BY appid"
        )],
        30, "favorite",
    )
    add(
        [row[0] for row in conn.execute(
            "SELECT appid FROM game_activity WHERE last_interested_at >= ? ORDER BY last_interested_at DESC LIMIT 500",
            (active_since,),
        )],
        20, "recent",
    )
    # A rotating bounded cold cohort gives already-known games their first
    # sample without turning the entire catalog into an immediate backlog.
    add(
        [row[0] for row in conn.execute(
            """SELECT g.appid FROM games g
            LEFT JOIN game_latest_state s ON s.appid=g.appid
            WHERE g.name IS NOT NULL
            ORDER BY COALESCE(s.players_updated_at, ''), g.appid LIMIT ?""",
            (config.COVERAGE_BACKGROUND_COHORT_LIMIT,),
        )],
        10, "background",
    )
    return result


def get_due_coverage_appids(kind, limit, *, include_hot=True):
    """Choose staggered player or CN-price work from deterministic tiers."""
    if kind not in {"players", "price"}:
        raise ValueError("unsupported coverage kind")
    with transaction() as conn:
        candidates = _coverage_candidates(conn, include_hot=include_hot)
        appids = list(candidates)
        latest = _latest_player_rows(conn, appids) if kind == "players" else _latest_price_rows(conn, appids)
        latest_players = _latest_player_rows(conn, appids) if kind == "price" else None
    selected = []
    for appid, (priority, source) in candidates.items():
        if kind == "players":
            players, fetched_at = latest.get(appid, (0, None))
            interval = (
                30 if source == "hot" else 60 if source == "favorite" else
                240 if source == "recent" else 120 if players > 10 else
                360 if players > 0 else 1440
            )
        else:
            fetched_at = latest.get(appid)
            players = (latest_players or {}).get(appid, (0, None))[0]
            interval = (
                24 * 60 if source in {"hot", "favorite"} else
                48 * 60 if source == "recent" or players > 0 else
                72 * 60
            )
        if is_due(fetched_at, interval):
            selected.append((appid, priority, fetched_at or ""))
    selected.sort(key=lambda row: (-row[1], row[2], row[0]))
    return selected[:max(1, int(limit))]


def enqueue_due_coverage_tasks(kind, limit, *, include_hot=True):
    rows = get_due_coverage_appids(kind, limit, include_hot=include_hot)
    for priority in sorted({priority for _appid, priority, _stamp in rows}, reverse=True):
        enqueue_crawl_tasks(
            [appid for appid, row_priority, _stamp in rows if row_priority == priority],
            kind,
            priority,
        )
    return len(rows)


def _coverage_budget_now(now=None):
    if now is None:
        return datetime.now(config.DAILY_REFRESH_TZINFO)
    if now.tzinfo is None:
        return now.replace(tzinfo=config.DAILY_REFRESH_TZINFO)
    return now.astimezone(config.DAILY_REFRESH_TZINFO)


def _coverage_budget_key(kind, *, now=None):
    day = _coverage_budget_now(now).date().isoformat()
    # v2 starts after popular work moved out of this background-only budget.
    return f"coverage_budget:v2:{kind}:{day}"


def released_coverage_budget(daily_limit, *, now=None):
    """Return the portion of today's quota that may be used by this moment."""
    local_now = _coverage_budget_now(now)
    day_start = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    elapsed = max(0.0, (local_now - day_start).total_seconds())
    # A tiny initial grant lets a fresh day make progress; thereafter the cap
    # rises continuously, preventing a crawler restart from draining the day.
    return min(int(daily_limit), max(1, math.ceil(int(daily_limit) * elapsed / 86400)))


def remaining_coverage_budget(kind, daily_limit, *, now=None):
    with transaction() as conn:
        used = int(get_crawl_state(conn, _coverage_budget_key(kind, now=now)) or 0)
    return max(0, released_coverage_budget(daily_limit, now=now) - used)


def reserve_coverage_budget(kind, count, daily_limit, *, now=None):
    """Reserve attempts before external I/O so failures cannot evade the cap."""
    count = max(0, int(count))
    with transaction() as conn:
        key = _coverage_budget_key(kind, now=now)
        used = int(get_crawl_state(conn, key) or 0)
        granted = min(count, max(0, released_coverage_budget(daily_limit, now=now) - used))
        if granted:
            set_crawl_state(conn, key, str(used + granted))
    return granted


def coverage_status():
    def usage(kind, limit):
        with transaction() as conn:
            used = int(get_crawl_state(conn, _coverage_budget_key(kind)) or 0)
        released = released_coverage_budget(limit)
        return {
            "used": used,
            "released": released,
            "available": max(0, released - used),
            "limit": limit,
        }

    return {
        "players": usage("players", config.PLAYER_DAILY_REQUEST_BUDGET),
        "price": usage("price", config.PRICE_DAILY_REQUEST_BUDGET),
    }
