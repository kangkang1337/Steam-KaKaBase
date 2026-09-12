"""User-facing application operations consumed by the HTTP layer."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path
import urllib.parse

from . import _runtime, config
from .db import (
    CURRENT_SCHEMA_VERSION,
    enqueue_crawl_task_once_in_conn,
    get_schema_version,
    enqueue_crawl_tasks,
    query_daily_niche_snapshot,
    query_header_image_url,
    query_home_snapshot,
    query_popular_historical_low_rows,
    query_tracked_appids,
    query_user_favorite_games,
    read_home_snapshot_context,
    transaction,
    upsert_home_snapshot,
)


def list_games():
    return _runtime.list_games()


def list_hot_games(limit):
    requested = min(max(1, int(limit)), _runtime.HOTLIST_TARGET)
    return {
        "games": _runtime.list_hot_games(requested),
        "count": _runtime.count_hot_games(),
        "version": _runtime.hot_games_version(),
        "queued": False,
    }


def hot_games_version():
    return {"version": _runtime.hot_games_version()}


def list_niche_pool():
    games = _runtime.list_niche_pool_games(_runtime.NICHE_POOL_DISPLAY_LIMIT)
    pool_count = _runtime.count_eligible_niche_pool()
    return {
        "games": games,
        "count": len(games),
        "pool_count": pool_count,
        "selection_mode": "all" if pool_count <= _runtime.NICHE_POOL_DISPLAY_LIMIT else "top_half_random",
        "queued": False,
    }


def get_home_picks():
    refresh_key = _runtime.daily_refresh_key()
    historical_lows = list_popular_historical_low_games()
    lows_by_appid = {int(game["appid"]): game for game in historical_lows}
    snapshot = query_home_snapshot(refresh_key)
    historical_low = None
    meme_url = None
    if snapshot:
        low_appid = snapshot["historical_low_appid"]
        historical_low = lows_by_appid.get(int(low_appid)) if low_appid else None
        meme_url = snapshot["meme_url"]
    niche_row = query_daily_niche_snapshot(refresh_key, _runtime.NICHE_MAX_REVIEWS)
    niche = clean_home_pick(niche_row) if niche_row else None
    memes = list_local_memes()
    return {
        "refresh_key": refresh_key,
        "historical_low": historical_low,
        "niche": niche,
        "meme": {"url": meme_url, "count": len(memes)},
    }


def refresh_daily_home_picks():
    """Create today's recommendation snapshots from the crawler process."""
    historical_lows = list_popular_historical_low_games()
    _runtime.snapshot_daily_niche_recommendation()
    memes = list_local_memes()
    return ensure_daily_home_snapshot(historical_lows, memes)


def clean_home_pick(row):
    if not row:
        return None
    item = dict(row)
    current_cny = _runtime.amount_int_to_cny(
        item["cn_price_final"], item["cn_price_currency"] or "CNY"
    )
    low_cny, low_source = _runtime.effective_historical_low(
        item.get("cn_itad_low_cny", item.get("cn_historical_low_cny")),
        item.get("cn_observed_low_cny"),
    )
    is_low = _runtime.cached_historical_low_match(
        current_cny,
        low_cny,
        low_source,
        item.get("cn_discount_percent"),
        item.get("cn_observed_snapshot_count"),
    )
    return {
        "appid": item["appid"],
        "name": _runtime.fallback_game_name(item["appid"], item["name"]),
        "name_zh": _runtime.fallback_game_name(item["appid"], item["name"]),
        "name_en": item.get("name_en"),
        "header_image": item["header_image"],
        "current_players": item["current_players"] or 0,
        "review_score": item["review_score"],
        "total_reviews": item.get("total_reviews"),
        "cn_price": item["cn_price"],
        "cn_price_display": item["cn_price"] or ("免费" if item.get("is_free") else "国区暂无售价"),
        "is_free": bool(item.get("is_free")),
        "cn_price_final": item["cn_price_final"],
        "cn_discount_percent": item["cn_discount_percent"] or 0,
        "cn_price_historical_low": is_low,
        "cn_price_discounted": bool((item["cn_discount_percent"] or 0) > 0 and not is_low),
        "cn_historical_low_cny": low_cny,
        "cn_historical_low_source": low_source,
        "cn_observed_low_since": item.get("cn_observed_low_since"),
        "cn_observed_snapshot_count": item.get("cn_observed_snapshot_count") or 0,
        "tracked": bool(item["tracked"]),
    }


def list_popular_historical_low_games(limit=200):
    limit = min(max(1, int(limit)), 1000)
    rows = query_popular_historical_low_rows(
        limit, _runtime.HOME_POPULAR_MIN_REVIEWS, _runtime.HOME_POPULAR_MIN_PLAYERS
    )
    games = [clean_home_pick(row) for row in rows]
    return [game for game in games if game["cn_price_historical_low"]]


def daily_index(total, refresh_key=None):
    if total <= 0:
        return 0
    key = refresh_key or _runtime.daily_refresh_key()
    return int(hashlib.sha256(key.encode("utf-8")).hexdigest()[:8], 16) % total


def list_local_memes():
    meme_dir = _runtime.ROOT / "assets" / "memes"
    if not meme_dir.is_dir():
        return []
    files = sorted(
        path for path in meme_dir.iterdir()
        if path.is_file() and path.suffix.lower() in _runtime.MEME_EXTENSIONS
    )
    return [f"/assets/memes/{path.name}" for path in files]


def ensure_daily_home_snapshot(historical_lows, memes):
    refresh_key = _runtime.daily_refresh_key()
    lows_by_appid = {int(game["appid"]): game for game in historical_lows}
    recent_appids, current = read_home_snapshot_context(
        refresh_key, _runtime.HOME_RECOMMENDATION_REPEAT_DAYS
    )
    fresh_lows = [game for game in historical_lows if int(game["appid"]) not in recent_appids]
    preferred_low = (fresh_lows or [None])[0]
    selected_low = (
        lows_by_appid.get(int(current["historical_low_appid"]))
        if current and current["historical_low_appid"] else None
    )
    needs_update = current is None
    if selected_low and int(selected_low["appid"]) in recent_appids:
        selected_low = None
        needs_update = True
    elif current and current["historical_low_appid"] and selected_low is None:
        needs_update = True
    selected_meme = current["meme_url"] if current and current["meme_url"] in memes else None
    if preferred_low and selected_low is None:
        selected_low = preferred_low
        needs_update = True
    if memes and selected_meme is None:
        selected_meme = memes[daily_index(len(memes), refresh_key)]
        needs_update = True
    if needs_update:
        upsert_home_snapshot(
            refresh_key, selected_low["appid"] if selected_low else None, selected_meme
        )
    return refresh_key, selected_low, selected_meme


def get_status():
    return _runtime.get_status()


def _monitor_day():
    return datetime.now(config.DAILY_REFRESH_TZINFO).date().isoformat()


def record_site_request(visitor_hash):
    """Store aggregate traffic only; visitor_hash is a server-side HMAC, never an IP."""
    day = _monitor_day()
    stamp = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    with transaction() as conn:
        conn.execute("INSERT INTO daily_request_metrics(day,request_count,new_visitor_count) VALUES(?,1,0) ON CONFLICT(day) DO UPDATE SET request_count=request_count+1", (day,))
        inserted = conn.execute("INSERT OR IGNORE INTO site_visitors(visitor_hash,first_seen_day,last_seen_day,first_seen_at,last_seen_at) VALUES(?,?,?,?,?)", (visitor_hash, day, day, stamp, stamp)).rowcount
        conn.execute("UPDATE site_visitors SET last_seen_day=?,last_seen_at=? WHERE visitor_hash=?", (day, stamp, visitor_hash))
        if inserted:
            conn.execute("UPDATE daily_request_metrics SET new_visitor_count=new_visitor_count+1 WHERE day=?", (day,))


def admin_monitoring():
    day = _monitor_day()
    status = get_status()
    with transaction(rows=True) as conn:
        metric = conn.execute("SELECT request_count,new_visitor_count FROM daily_request_metrics WHERE day=?", (day,)).fetchone()
        unique_today = conn.execute("SELECT COUNT(*) FROM site_visitors WHERE last_seen_day=?", (day,)).fetchone()[0]
        counts = {
            "users_total": conn.execute("SELECT COUNT(*) FROM users").fetchone()[0],
            "new_users_today": conn.execute("SELECT COUNT(*) FROM users WHERE substr(created_at,1,10)=?", (day,)).fetchone()[0],
            "favorites_total": conn.execute("SELECT COUNT(*) FROM user_favorites").fetchone()[0],
            "games_total": conn.execute("SELECT COUNT(*) FROM games").fetchone()[0],
            "player_snapshots": conn.execute("SELECT COUNT(*) FROM player_snapshots").fetchone()[0],
            "price_snapshots": conn.execute("SELECT COUNT(*) FROM price_snapshots").fetchone()[0],
            "review_snapshots": conn.execute("SELECT COUNT(*) FROM review_snapshots").fetchone()[0],
            "local_backup_at": _runtime.get_crawl_state(conn, "daily_database_backup_at") or None,
            "local_backup_path": _runtime.get_crawl_state(conn, "daily_database_backup_path") or None,
            "offsite_backup_at": _runtime.get_crawl_state(conn, "offsite_database_backup_at") or None,
            "offsite_backup_path": _runtime.get_crawl_state(conn, "offsite_database_backup_path") or None,
        }
    drill_path = config.DATA_DIR / "runtime" / "recovery_drill.json"
    try:
        import json
        drill = json.loads(drill_path.read_text(encoding="utf-8")) if drill_path.is_file() else None
    except (OSError, ValueError):
        drill = None
    return {
        "today": {"day": day, "requests": int(metric["request_count"]) if metric else 0, "visitors": unique_today, "new_visitors": int(metric["new_visitor_count"]) if metric else 0, **{key: counts[key] for key in ("new_users_today", "users_total", "favorites_total")}},
        "server": {"web": "ok", "crawler": status.get("crawler", {}), "database": "ok" if status.get("database_schema_version") else "unavailable", "backup": "configured" if config.DB_DAILY_BACKUP_ENABLED else "disabled", "controls": {"read_only": True, "future_actions": ["restart_web", "restart_crawler", "run_backup"]}},
        "crawler": {"queue": status.get("task_monitor", {}), "rate_limits": status.get("rate_limits", {}), "heartbeat": status.get("crawler", {}).get("heartbeat_at"), "heartbeat_age_seconds": status.get("crawler", {}).get("heartbeat_age_seconds"), "state": status.get("crawler", {}).get("state")},
        "database": {"schema": status.get("database_schema_version"), "storage": status.get("storage", {}), **{key: counts[key] for key in ("games_total", "player_snapshots", "price_snapshots", "review_snapshots")}},
        "backups": {"local": {"at": counts["local_backup_at"], "path": counts["local_backup_path"]}, "offsite": {"at": counts["offsite_backup_at"], "path": counts["offsite_backup_path"]}, "drill": drill},
    }


def search(term, limit=12, offset=0):
    if not term:
        return {"items": [], "limit": limit, "offset": offset, "has_more": False}
    items = _runtime.search_steam(term, limit=limit + 1, offset=offset)
    return {
        "items": items[:limit],
        "limit": limit,
        "offset": offset,
        "has_more": len(items) > limit,
    }


def get_game(appid, history_limit=500):
    return _runtime.get_game_payload(int(appid), history_limit)


def _enqueue_game_refresh(appids, priority=100):
    appids = [int(appid) for appid in appids]
    queued = 0
    for task_type in ("players", "preview", "reviews", "metadata", "regional_prices", "historylow"):
        queued += enqueue_crawl_tasks(appids, task_type, priority)
    return queued


def track_game(appid, name=None, header_image=None):
    appid = int(appid)
    _runtime.quick_track_game(appid, name, header_image)
    queued = _enqueue_game_refresh([appid])
    return {"ok": True, "appid": appid, "queued": bool(queued), "queued_tasks": queued}


def untrack_game(appid):
    appid = int(appid)
    _runtime.untrack_game(appid)
    return {"ok": True, "appid": appid, "tracked": False}


def list_user_favorites(user_id):
    return [_runtime.clean_game(row, summary=True) for row in query_user_favorite_games(user_id)]


def add_user_favorite(user_id, appid, name=None, header_image=None):
    track_game(appid, name, header_image)
    with transaction() as conn:
        conn.execute("INSERT OR IGNORE INTO user_favorites(user_id,appid,created_at) VALUES(?,?,?)", (int(user_id), int(appid), datetime.now(timezone.utc).replace(microsecond=0).isoformat()))
    return {"ok": True, "appid": int(appid), "tracked": True}


def remove_user_favorite(user_id, appid):
    with transaction() as conn:
        conn.execute("DELETE FROM user_favorites WHERE user_id=? AND appid=?", (int(user_id), int(appid)))
    return {"ok": True, "appid": int(appid), "tracked": False}


def refresh_all():
    appids = query_tracked_appids()
    queued = _enqueue_game_refresh(appids) if appids else 0
    return {"ok": True, "running": False, "queued": bool(queued), "queued_tasks": queued}


def refresh_game(appid):
    appid = int(appid)
    queued = _enqueue_game_refresh([appid])
    return {"ok": True, "appid": appid, "queued": bool(queued), "queued_tasks": queued}


def request_game_detail(appid):
    """Accept one bounded public request to prioritize a cached game detail.

    This intentionally only creates missing queue rows. Repeated browser calls
    cannot revive completed work or generate further external requests.
    """
    appid = int(appid)
    with transaction() as conn:
        game_exists = bool(conn.execute("SELECT 1 FROM games WHERE appid=?", (appid,)).fetchone())
        if not game_exists and not _runtime.ensure_game_from_catalog(conn, appid):
            return {"ok": False, "appid": appid, "queued": False, "reason": "not_found"}
        existing = bool(conn.execute(
            """
            SELECT 1 FROM crawl_tasks
            WHERE appid=? AND task_type IN ('players', 'preview', 'reviews', 'metadata', 'regional_prices')
              AND completed_at IS NULL AND status IN ('pending', 'retry', 'running')
            LIMIT 1
            """,
            (appid,),
        ).fetchone())
        active = conn.execute(
            """
            SELECT COUNT(DISTINCT appid) FROM crawl_tasks
            WHERE priority >= 90 AND completed_at IS NULL
              AND status IN ('pending', 'retry', 'running')
            """
        ).fetchone()[0]
        if not existing and int(active or 0) >= config.PUBLIC_DETAIL_QUEUE_LIMIT:
            return {"ok": True, "appid": appid, "queued": False, "reason": "queue_full"}
        for task_type in ("players", "preview", "reviews", "metadata", "regional_prices"):
            enqueue_crawl_task_once_in_conn(conn, appid, task_type, 90)
    return {"ok": True, "appid": appid, "queued": not existing, "reason": None}


def cache_remote_image(url):
    return _runtime.cache_image(url)


def cached_remote_image(url):
    if not is_allowed_image_url(url):
        raise ValueError("unsupported image host")
    parsed = urllib.parse.urlparse(url)
    suffix = {
        ".jpg": ".jpg", ".jpeg": ".jpeg", ".png": ".png", ".webp": ".webp", ".gif": ".gif",
    }.get(Path(parsed.path).suffix.lower(), ".img")
    cache_root = config.IMAGE_CACHE_DIR.resolve()
    path = (cache_root / f"{hashlib.sha256(url.encode('utf-8')).hexdigest()}{suffix}").resolve()
    if path.parent != cache_root:
        raise ValueError("invalid cache path")
    return path if path.is_file() and path.stat().st_size > 0 else None


def header_image_url(appid):
    url = query_header_image_url(int(appid))
    return str(url or "") if is_allowed_image_url(url) else ""


def is_allowed_image_url(url):
    parsed = urllib.parse.urlparse(str(url or ""))
    return parsed.scheme in {"http", "https"} and parsed.hostname in _runtime.ALLOWED_IMAGE_HOSTS


def readiness():
    with transaction() as conn:
        conn.execute("SELECT 1").fetchone()
        schema_version = get_schema_version(conn)
    if schema_version != CURRENT_SCHEMA_VERSION:
        raise RuntimeError(
            f"database schema v{schema_version} is not current v{CURRENT_SCHEMA_VERSION}"
        )
    return {
        "ready": True,
        "database": "ok",
        "schema_version": schema_version,
    }
