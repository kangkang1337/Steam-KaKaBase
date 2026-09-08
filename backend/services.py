"""User-facing application operations consumed by the HTTP layer."""

import hashlib
import urllib.parse

from . import _runtime
from .db import (
    CURRENT_SCHEMA_VERSION,
    get_schema_version,
    query_popular_historical_low_rows,
    read_home_snapshot_context,
    transaction,
    upsert_home_snapshot,
)


def list_games():
    return _runtime.list_games()


def ensure_hot_games(target):
    target = min(max(100, int(target)), _runtime.HOTLIST_TARGET)
    hot_count = _runtime.count_hot_games()
    with transaction() as conn:
        hotlist_at = _runtime.get_crawl_state(conn, "hotlist_at")
    force_hotlist = hot_count < target and _runtime.is_due(hotlist_at, 30)
    queued = False
    if force_hotlist or _runtime.is_due(hotlist_at, _runtime.HOTLIST_REFRESH_HOURS * 60):
        queued = _runtime.refresh_hot_database_async(force_hotlist=force_hotlist, quick=True)
    preview_queued = _runtime.enqueue_missing_hot_previews(
        limit=_runtime.HOT_FULL_METADATA_TOP_LIMIT,
        priority=90,
    )
    return {"queued": queued, "count": hot_count, "target": target, "preview_queued": preview_queued}


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
    historical_lows = list_popular_historical_low_games()
    daily_niche = _runtime.get_daily_niche_recommendation()
    if daily_niche:
        niche = daily_niche
    else:
        niche = None
        niche_row = _runtime.list_niche_pool_pick()
        if niche_row:
            item = dict(niche_row)
            item["tracked"] = False
            item["cn_historical_low_cny"] = None
            niche = clean_home_pick(item)
    memes = list_local_memes()
    refresh_key, historical_low, meme_url = ensure_daily_home_snapshot(historical_lows, memes)
    return {
        "refresh_key": refresh_key,
        "historical_low": historical_low,
        "niche": niche,
        "meme": {"url": meme_url, "count": len(memes)},
    }


def clean_home_pick(row):
    if not row:
        return None
    item = dict(row)
    current_cny = _runtime.amount_int_to_cny(
        item["cn_price_final"], item["cn_price_currency"] or "CNY"
    )
    is_low = _runtime.compare_historical_low(current_cny, item["cn_historical_low_cny"])
    return {
        "appid": item["appid"],
        "name": _runtime.fallback_game_name(item["appid"], item["name"]),
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
        "cn_historical_low_cny": item["cn_historical_low_cny"],
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


def track_game(appid, name=None, header_image=None):
    appid = int(appid)
    _runtime.quick_track_game(appid, name, header_image)
    queued = _runtime.refresh_tracked_game_async(appid, name)
    return {"ok": True, "appid": appid, "queued": queued}


def untrack_game(appid):
    appid = int(appid)
    _runtime.untrack_game(appid)
    return {"ok": True, "appid": appid, "tracked": False}


def refresh_all():
    if _runtime.REFRESH_STATUS["running"]:
        return {"ok": True, "running": True, "message": "refresh already running"}
    errors = _runtime.refresh_tracked_once(force_all=True)
    return {"ok": True, "running": False, "errors": errors}


def refresh_game(appid):
    return {"ok": True, **_runtime.refresh_game(int(appid))}


def cache_remote_image(url):
    return _runtime.cache_image(url)


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
